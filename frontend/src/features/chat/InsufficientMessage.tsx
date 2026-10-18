import { Info } from "lucide-react";

import { LogoMark } from "../../components/LogoMark";

/**
 * Shown under every insufficient answer, whatever the cause (hidden file, no such loan, fact
 * not in the user's files), so it never reveals that a hidden file matched (architecture D58).
 */
export const INSUFFICIENT_HINT =
  "Try rephrasing, or include the loan number. If you think you should have access to this loan file, please check with your admin.";

/** UI-F11: neutral, never an error style, and identical for every insufficient answer. */
export function InsufficientMessage({ answer }: { answer: string }) {
  return (
    <div className="flex items-start gap-3" data-testid="insufficient">
      <LogoMark size={24} className="mt-1 shrink-0" />
      <div className="flex max-w-reading gap-3 rounded-2xl bg-sky px-4 py-3 text-ink">
        <Info size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-ink-muted" />
        <div>
          <p className="whitespace-pre-wrap text-message">{answer}</p>
          <p className="mt-1 text-ui text-ink-muted">{INSUFFICIENT_HINT}</p>
        </div>
      </div>
    </div>
  );
}
