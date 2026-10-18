import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { Button } from "../../components/Button";
import { LogoMark } from "../../components/LogoMark";
import { brand } from "../../config/brand";

// Static demo text only, never real data (UI-F1).
const DEMO_QUESTION = "What's the interest rate on loan LN-0000-000000?";
const DEMO_ANSWER = "The note carries a fixed rate of 6.375%";

/** UI-F1: product name, one sentence, Sign in, and the screen's single motion moment. */
export function WelcomeScreen() {
  const { status, signIn } = useAuth();
  const reduce = useReducedMotion();
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (reduce) return; // reduced motion: the preview stays on its first frame (§9.6)
    const t = window.setInterval(() => setStep((s) => (s + 1) % 4), 1800);
    return () => window.clearInterval(t);
  }, [reduce]);
  if (status === "authenticated") return <Navigate to="/dashboard" replace />;
  return (
    <main className="ruled-paper flex min-h-full items-center justify-center bg-paper px-6 py-12">
      <div className="grid w-full max-w-5xl items-center gap-12 md:grid-cols-2">
        <section>
          <div className="mb-6 flex items-center gap-3">
            <LogoMark size={40} />
            <span className="font-display text-panel font-semibold text-ink">{brand.name}</span>
          </div>
          <h1 className="font-display text-welcome font-bold text-ink">{brand.name}</h1>
          <p className="mt-4 max-w-md text-message text-ink-muted">{brand.tagline}</p>
          <Button className="mt-8 px-6 py-3" onClick={signIn} disabled={status === "loading"}>
            Sign in
          </Button>
        </section>
        <section
          aria-label="Example of an answer with a source"
          className="rounded-[18px] border border-line bg-surface p-6 shadow-sm"
        >
          <div className="flex justify-end">
            <p className="max-w-[80%] rounded-[18px] rounded-br-[6px] bg-spruce px-4 py-2.5 text-message text-on-spruce">
              {DEMO_QUESTION}
            </p>
          </div>
          <AnimatePresence>
            {(reduce || step >= 1) && (
              <motion.div
                key="answer"
                initial={reduce ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                className="mt-4 flex gap-3"
              >
                <LogoMark size={24} className="mt-1 shrink-0" />
                <p className="text-message text-ink">
                  {DEMO_ANSWER}{" "}
                  <span
                    className={`inline-flex rounded-full px-2 text-meta font-semibold transition-colors ${
                      !reduce && step >= 2
                        ? "bg-marigold text-on-marigold"
                        : "bg-marigold/30 text-ink"
                    }`}
                  >
                    1
                  </span>
                  .
                </p>
              </motion.div>
            )}
          </AnimatePresence>
          <div className="mt-5 flex items-center gap-2 text-meta text-ink-muted">
            <FileText size={14} aria-hidden="true" />
            <span>
              Source:{" "}
              <mark className={`cited ${!reduce && step >= 2 ? "sweep" : ""}`}>
                fixed rate of 6.375%
              </mark>{" "}
              on page 1
            </span>
          </div>
        </section>
      </div>
    </main>
  );
}
