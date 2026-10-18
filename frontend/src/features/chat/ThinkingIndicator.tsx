import { useEffect, useState } from "react";

import { LogoMark } from "../../components/LogoMark";

// Cosmetic timers only: they MUST NOT reflect backend internals such as retrieval rounds,
// which could hint at how much of the corpus the user can see (UI-F8).
export const THINKING_STEPS = ["Searching your files", "Reading the sources", "Writing the answer"];

export function ThinkingIndicator() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const t = window.setInterval(
      () => setStep((s) => Math.min(s + 1, THINKING_STEPS.length - 1)),
      2500,
    );
    return () => window.clearInterval(t);
  }, []);
  return (
    <div className="flex items-start gap-3" data-testid="thinking">
      <LogoMark size={24} className="mt-1 shrink-0" />
      <div>
        <div className="flex h-6 items-center gap-1" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-2 w-2 animate-bounce rounded-full bg-spruce"
              style={{ animationDelay: `${i * 150}ms` }}
            />
          ))}
        </div>
        <p className="text-meta text-ink-muted" role="status">
          {THINKING_STEPS[step]}
        </p>
      </div>
    </div>
  );
}
