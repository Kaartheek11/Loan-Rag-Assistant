import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useState } from "react";

import { Button } from "../../components/Button";
import { LogoMark } from "../../components/LogoMark";

type Props = {
  phase?: "signing-in" | "checking";
  failed?: boolean;
  onRetry?: () => void;
};

/** UI-F2: shown while /v1/me is in flight right after sign-in. */
export function CheckingAccess({ phase = "checking", failed = false, onRetry }: Props) {
  const reduce = useReducedMotion();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setSlow(true), 8000);
    return () => window.clearTimeout(t);
  }, []);
  return (
    <main className="flex min-h-full flex-col items-center justify-center gap-5 bg-paper p-6 text-center">
      <motion.div
        animate={reduce || failed ? undefined : { scale: [1, 1.08, 1], opacity: [1, 0.8, 1] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      >
        <LogoMark size={56} />
      </motion.div>
      {failed ? (
        <>
          <p className="text-message text-ink">
            Couldn&apos;t check your access. Check your connection and try again.
          </p>
          <Button onClick={onRetry}>Try again</Button>
        </>
      ) : (
        <>
          <p className="text-message font-medium text-ink" role="status">
            {phase === "signing-in" ? "Signing you in" : "Checking your access"}
          </p>
          {slow && phase === "checking" && (
            <>
              <p className="text-ui text-ink-muted">This is taking longer than usual.</p>
              <Button variant="secondary" onClick={onRetry}>
                Try again
              </Button>
            </>
          )}
        </>
      )}
    </main>
  );
}
