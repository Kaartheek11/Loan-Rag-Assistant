import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

import { ApiError, NetworkError } from "../../api/client";
import { useAskQuestion } from "../../api/query";
import type { Citation } from "../../api/types";

export type ErrorKind = "rate_limited" | "unavailable" | "network" | "invalid" | "other";

export type ChatMessage =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      question: string;
      status: "pending" | "answered" | "insufficient" | "error";
      answer?: string;
      citations?: Citation[];
      errorKind?: ErrorKind;
      requestId?: string | null;
    };

type ChatValue = {
  messages: ChatMessage[];
  pending: boolean;
  ask: (question: string) => void;
  retry: (assistantId: string) => void;
  clear: () => void;
};

const ChatContext = createContext<ChatValue | null>(null);
let seq = 0;
const newId = () => `m${++seq}`;

export function errorKindOf(error: unknown): ErrorKind | null {
  if (error instanceof NetworkError) return "network";
  if (error instanceof ApiError) {
    if (error.status === 401) return null; // handled by sign-in, never shown in chat (UI-F12)
    if (error.status === 429) return "rate_limited";
    if (error.status === 503) return "unavailable";
    if (error.status === 422) return "invalid";
  }
  return "other";
}

/** Conversation state lives in React memory only (UI-S4); it is gone on reload or sign-out. */
export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const ask = useAskQuestion();
  const pendingRef = useRef(false);
  const [pending, setPending] = useState(false);

  const run = useCallback(
    (question: string, assistantId: string) => {
      pendingRef.current = true;
      setPending(true);
      ask.mutate(question, {
        onSuccess: (data) =>
          setMessages((ms) =>
            ms.map((m) =>
              m.id === assistantId && m.role === "assistant"
                ? {
                    ...m,
                    status: data.status,
                    answer: data.answer,
                    citations: data.status === "answered" ? data.citations : [],
                    requestId: data.request_id,
                  }
                : m,
            ),
          ),
        onError: (error) => {
          const kind = errorKindOf(error);
          setMessages((ms) =>
            kind === null
              ? ms.filter((m) => m.id !== assistantId)
              : ms.map((m) =>
                  m.id === assistantId && m.role === "assistant"
                    ? {
                        ...m,
                        status: "error",
                        errorKind: kind,
                        requestId: error instanceof ApiError ? error.requestId : null,
                      }
                    : m,
                ),
          );
        },
        onSettled: () => {
          pendingRef.current = false;
          setPending(false);
        },
      });
    },
    [ask],
  );

  const value = useMemo<ChatValue>(
    () => ({
      messages,
      pending,
      ask: (question) => {
        if (pendingRef.current) return; // one question at a time (UI-F6)
        const assistantId = newId();
        setMessages((ms) => [
          ...ms,
          { id: newId(), role: "user", text: question },
          { id: assistantId, role: "assistant", question, status: "pending" },
        ]);
        run(question, assistantId);
      },
      retry: (assistantId) => {
        if (pendingRef.current) return;
        const target = messages.find((m) => m.id === assistantId);
        if (!target || target.role !== "assistant") return;
        setMessages((ms) =>
          ms.map((m) =>
            m.id === assistantId && m.role === "assistant"
              ? { id: m.id, role: "assistant", question: m.question, status: "pending" }
              : m,
          ),
        );
        run(target.question, assistantId);
      },
      clear: () => setMessages([]),
    }),
    [messages, pending, run],
  );
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat(): ChatValue {
  const value = useContext(ChatContext);
  if (!value) throw new Error("useChat must be used inside a ChatProvider");
  return value;
}
