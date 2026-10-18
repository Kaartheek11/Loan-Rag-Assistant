import { AlertCircle } from "lucide-react";

import { Button } from "../../components/Button";
import type { ErrorKind } from "./ChatContext";

// UI-F12. No status codes, request IDs, stack traces, or JSON in visible text.
export const ERROR_MESSAGES: Record<ErrorKind, string> = {
  rate_limited: "You're asking questions quickly. Wait a minute, then try again.",
  unavailable: "The assistant isn't available right now. Try again in a moment.",
  network: "Couldn't reach LoanLens. Check your connection and try again.",
  invalid: "That question couldn't be sent. Keep it under 1,000 characters.",
  other: "Something went wrong while answering. Try again.",
};

type Props = {
  kind: ErrorKind;
  requestId?: string | null;
  onRetry: () => void;
  disabled?: boolean;
};

export function ErrorMessage({ kind, requestId, onRetry, disabled }: Props) {
  return (
    <div
      className="flex max-w-reading items-start gap-3 rounded-2xl border border-coral/40 bg-surface px-4 py-3"
      data-testid="error-card"
      data-error-kind={kind}
      // Hidden, for debugging in developer tools only (UI-F12).
      data-request-id={requestId ?? undefined}
    >
      <AlertCircle size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-coral" />
      <div className="flex-1">
        <p className="text-message text-ink">{ERROR_MESSAGES[kind]}</p>
        <Button
          variant="secondary"
          className="mt-2 px-3 py-1.5"
          onClick={onRetry}
          disabled={disabled}
        >
          Try again
        </Button>
      </div>
    </div>
  );
}
