import { ArrowRight, Loader2 } from "lucide-react";
import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

export const MAX_QUESTION = 1000;
export const COUNTER_FROM = 800;
const BLANK = "___";
const LINE_HEIGHT = 24;

export type ComposerHandle = { fill: (template: string) => void };

type Props = { pending: boolean; onSend: (question: string) => void };

/** UI-F6: auto-growing input, Enter sends, Shift+Enter adds a line, 1,000-character limit. */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer(
  { pending, onSend },
  ref,
) {
  const [value, setValue] = useState("");
  const area = useRef<HTMLTextAreaElement | null>(null);
  const tooLong = value.length > MAX_QUESTION;
  const blank = value.trim().length === 0;
  const canSend = !pending && !blank && !tooLong;

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, LINE_HEIGHT), LINE_HEIGHT * 6) + 4}px`;
  }, [value]);

  useImperativeHandle(ref, () => ({
    fill: (template: string) => {
      setValue(template);
      requestAnimationFrame(() => {
        const el = area.current;
        if (!el) return;
        el.focus();
        const at = template.indexOf(BLANK);
        if (at >= 0) el.setSelectionRange(at, at + BLANK.length);
        else el.setSelectionRange(template.length, template.length);
      });
    },
  }));

  const send = useCallback(() => {
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
  }, [canSend, onSend, value]);

  return (
    <div className="w-full">
      <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-[0_4px_18px_rgba(0,0,0,0.08)]">
        <label htmlFor="composer" className="sr-only">
          Your question
        </label>
        <textarea
          id="composer"
          ref={area}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask about a loan file..."
          aria-describedby="composer-hint composer-limit"
          aria-invalid={tooLong}
          className="max-h-[148px] min-h-[28px] flex-1 resize-none bg-transparent px-2 py-1.5 text-message leading-6 text-ink placeholder:text-ink-muted focus:outline-none"
        />
        <button
          type="button"
          onClick={send}
          disabled={!canSend}
          aria-label="Send"
          className="group inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-spruce text-on-spruce transition-transform disabled:opacity-40"
        >
          {pending ? (
            <Loader2 size={18} className="animate-spin" aria-hidden="true" />
          ) : (
            <ArrowRight
              size={18}
              aria-hidden="true"
              className="transition-transform group-active:translate-x-0.5"
            />
          )}
        </button>
      </div>
      <div id="composer-limit" className="mt-1 flex min-h-5 justify-end px-2 text-meta">
        {tooLong ? (
          <span className="text-coral">Questions can be up to 1,000 characters.</span>
        ) : value.length > COUNTER_FROM ? (
          <span className="text-ink-muted tabular">
            {value.length.toLocaleString("en-US")} / 1,000
          </span>
        ) : null}
      </div>
    </div>
  );
});
