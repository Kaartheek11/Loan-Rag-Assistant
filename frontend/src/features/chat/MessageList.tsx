import { motion, useReducedMotion } from "framer-motion";
import { ArrowDown } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { AssistantMessage } from "./AssistantMessage";
import { type ChatMessage, useChat } from "./ChatContext";
import { ErrorMessage } from "./ErrorMessage";
import { InsufficientMessage } from "./InsufficientMessage";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { UserMessage } from "./UserMessage";

function Message({ m }: { m: ChatMessage }) {
  const chat = useChat();
  if (m.role === "user") return <UserMessage text={m.text} />;
  if (m.status === "pending") return <ThinkingIndicator />;
  if (m.status === "insufficient") return <InsufficientMessage answer={m.answer ?? ""} />;
  if (m.status === "error")
    return (
      <ErrorMessage
        kind={m.errorKind ?? "other"}
        requestId={m.requestId}
        onRetry={() => chat.retry(m.id)}
        disabled={chat.pending}
      />
    );
  return <AssistantMessage answer={m.answer ?? ""} citations={m.citations ?? []} />;
}

/** Auto-scrolls to the newest message unless the user scrolled up (UI-F7). */
export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const reduce = useReducedMotion();
  const scroller = useRef<HTMLDivElement | null>(null);
  const [pinned, setPinned] = useState(true);
  const last = messages[messages.length - 1];
  const lastKey = last ? `${last.id}:${last.role === "assistant" ? last.status : ""}` : "";

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned) el.scrollTop = el.scrollHeight;
  }, [lastKey, pinned]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scroller} className="h-full overflow-y-auto" data-testid="message-list">
        <ol className="mx-auto flex max-w-chat flex-col gap-6 px-4 py-6" aria-label="Conversation">
          {messages.map((m) => (
            <motion.li
              key={m.id}
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduce ? 0.01 : 0.18, ease: "easeOut" }}
            >
              <Message m={m} />
            </motion.li>
          ))}
        </ol>
      </div>
      {!pinned && (
        <button
          type="button"
          onClick={() => {
            const el = scroller.current;
            if (el) el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : "smooth" });
            setPinned(true);
          }}
          className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-spruce px-3 py-1.5 text-meta font-medium text-on-spruce shadow-md"
        >
          <ArrowDown size={14} aria-hidden="true" />
          Jump to latest
        </button>
      )}
    </div>
  );
}
