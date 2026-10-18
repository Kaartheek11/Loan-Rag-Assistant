import { motion, useReducedMotion } from "framer-motion";
import { useRef } from "react";

import { useMe } from "../../api/me";
import { firstName, greeting } from "../../lib/greeting";
import { useChat } from "./ChatContext";
import { Composer, type ComposerHandle } from "./Composer";
import { MessageList } from "./MessageList";
import { StarterPrompts } from "./StarterPrompts";

let greetedOnce = false;

export function ChatWindow() {
  const chat = useChat();
  const me = useMe();
  const reduce = useReducedMotion();
  const composer = useRef<ComposerHandle | null>(null);
  // The dashboard's single automatic animation: the greeting fades in once (§9.6).
  const animateGreeting = !greetedOnce && !reduce;
  greetedOnce = true;
  const name = firstName(me.data?.display_name ?? "");
  return (
    <section aria-label="Chat" className="flex h-full min-h-0 flex-col bg-surface">
      {chat.messages.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 text-center">
          <motion.h1
            initial={animateGreeting ? { opacity: 0 } : false}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="font-display text-greeting font-semibold text-ink"
          >
            {greeting()}, {name}
          </motion.h1>
          <p className="text-message text-ink-muted">Ask about any loan file you can open.</p>
          <div className="mt-2 max-w-chat">
            <StarterPrompts onPick={(t) => composer.current?.fill(t)} />
          </div>
        </div>
      ) : (
        <MessageList messages={chat.messages} />
      )}
      <div className="mx-auto w-full max-w-chat px-4 pb-4">
        <Composer ref={composer} pending={chat.pending} onSend={chat.ask} />
        <p id="composer-hint" className="px-2 text-meta text-ink-muted">
          Each question is answered on its own, so include the loan number or borrower name.
        </p>
      </div>
    </section>
  );
}
