import * as Dialog from "@radix-ui/react-dialog";
import { useReducedMotion } from "framer-motion";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../../api/client";
import { usePage } from "../../api/pages";
import { highlightSegments } from "../../lib/highlight";
import type { Breakpoint } from "../../lib/useBreakpoint";
import { useSource } from "./SourceContext";

/** UI-F13: page viewer with the cited passage highlighted. */
export function SourcePanel({ breakpoint }: { breakpoint: Breakpoint }) {
  const source = useSource();
  const t = source.target;
  const modal = breakpoint !== "desktop";
  return (
    <Dialog.Root
      open={t !== null}
      modal={modal}
      onOpenChange={(open) => {
        if (!open) source.close();
      }}
    >
      {modal && t && (
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
          <Dialog.Content
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              source.openerRef.current?.focus();
            }}
            className={
              breakpoint === "phone"
                ? "fixed inset-0 z-50 flex flex-col bg-surface"
                : "fixed inset-y-0 right-0 z-50 flex w-[420px] max-w-full flex-col bg-surface shadow-2xl"
            }
          >
            <PanelBody />
          </Dialog.Content>
        </Dialog.Portal>
      )}
      {!modal && t && (
        <Dialog.Content
          onInteractOutside={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            source.openerRef.current?.focus();
          }}
          className="flex h-full w-[420px] shrink-0 flex-col border-l border-line bg-surface shadow-[-8px_0_24px_rgba(0,0,0,0.06)]"
        >
          <PanelBody />
        </Dialog.Content>
      )}
    </Dialog.Root>
  );
}

function PanelBody() {
  const source = useSource();
  const t = source.target!;
  const page = usePage(t.docId, t.page);
  const reduce = useReducedMotion();
  const markRef = useRef<HTMLElement | null>(null);
  const [goTo, setGoTo] = useState("");
  const total = page.data?.page_count ?? null;
  const onCited = t.citedPage === t.page && t.excerpt !== null;
  const { segments, found } = highlightSegments(page.data?.text ?? "", onCited ? t.excerpt : null);
  const notFound = page.error instanceof ApiError && page.error.status === 404;

  useEffect(() => {
    if (found) markRef.current?.scrollIntoView?.({ block: "center" });
  }, [found, page.data]);

  const jump = (n: number) => {
    if (total !== null && n >= 1 && n <= total && n !== t.page) source.goTo(n);
  };

  return (
    <>
      <header className="border-b border-line px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <Dialog.Title className="truncate font-display text-panel font-semibold text-ink">
              {t.filename}
            </Dialog.Title>
            <Dialog.Description className="text-meta text-ink-muted">
              {page.data?.section ?? "Source page"}
            </Dialog.Description>
          </div>
          <Dialog.Close
            aria-label="Close source"
            className="rounded-lg p-1.5 text-ink-muted hover:bg-ink/5 hover:text-ink"
          >
            <X size={18} aria-hidden="true" />
          </Dialog.Close>
        </div>
        {!notFound && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-ui text-ink">
            <button
              type="button"
              aria-label="Previous page"
              disabled={t.page <= 1}
              onClick={() => jump(t.page - 1)}
              className="rounded-lg border border-line p-1.5 disabled:opacity-40"
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <span className="tabular" data-testid="page-indicator">
              Page {t.page}
              {total !== null ? ` of ${total}` : ""}
            </span>
            <button
              type="button"
              aria-label="Next page"
              disabled={total === null || t.page >= total}
              onClick={() => jump(t.page + 1)}
              className="rounded-lg border border-line p-1.5 disabled:opacity-40"
            >
              <ChevronRight size={16} aria-hidden="true" />
            </button>
            <form
              className="ml-auto flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                jump(Number(goTo));
                setGoTo("");
              }}
            >
              <label htmlFor="go-to-page" className="text-meta text-ink-muted">
                Go to page
              </label>
              <input
                id="go-to-page"
                type="number"
                inputMode="numeric"
                min={1}
                max={total ?? undefined}
                value={goTo}
                onChange={(e) => setGoTo(e.target.value)}
                className="w-16 rounded-lg border border-line bg-surface px-2 py-1 text-ui tabular"
              />
              <button
                type="submit"
                className="rounded-lg border border-line px-2 py-1 text-meta font-medium hover:bg-paper"
              >
                Go
              </button>
            </form>
          </div>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" data-testid="page-body">
        {notFound ? (
          <div className="flex flex-col items-start gap-3">
            <p className="text-message text-ink">This page isn&apos;t available.</p>
            <Dialog.Close className="rounded-xl border border-line px-4 py-2 text-ui font-medium">
              Close
            </Dialog.Close>
          </div>
        ) : page.isLoading ? (
          <p className="text-ui text-ink-muted" role="status">
            Loading page
          </p>
        ) : page.isError ? (
          <p className="text-ui text-ink">Couldn&apos;t load this page. Try again in a moment.</p>
        ) : (
          <>
            {onCited && !found && (
              <p className="mb-3 rounded-lg bg-sky px-3 py-2 text-meta text-ink">
                Couldn&apos;t locate the exact passage on this page.
              </p>
            )}
            <p className="whitespace-pre-wrap break-words text-message tabular text-ink">
              {segments.map((s, i) =>
                s.highlighted ? (
                  <mark
                    key={i}
                    ref={markRef}
                    data-testid="cited-passage"
                    className={`cited ${reduce ? "" : "sweep"}`}
                  >
                    {s.text}
                  </mark>
                ) : (
                  <span key={i}>{s.text}</span>
                ),
              )}
            </p>
          </>
        )}
      </div>
    </>
  );
}
