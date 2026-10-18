import { Check, Copy, FileText } from "lucide-react";
import { useState } from "react";

import type { Citation } from "../../api/types";
import { LogoMark } from "../../components/LogoMark";
import { useSource } from "../source/SourceContext";
import { parseAnswer, uniqueSources } from "./citations";

function sourceLabel(c: Citation): string {
  return `Source ${c.marker}: ${c.filename}, page ${c.page}`;
}

/** UI-F7 + UI-F10: plain-text answer with inline citation chips and a Sources row. */
export function AssistantMessage({ answer, citations }: { answer: string; citations: Citation[] }) {
  const source = useSource();
  const [copied, setCopied] = useState(false);
  const open = (c: Citation, el: HTMLElement) =>
    source.open(
      {
        docId: c.doc_id,
        filename: c.filename,
        page: c.page,
        citedPage: c.page,
        excerpt: c.excerpt,
      },
      el,
    );
  return (
    <div className="group flex items-start gap-3" data-testid="assistant-answer">
      <LogoMark size={24} className="mt-1 shrink-0" />
      <div className="min-w-0 max-w-reading flex-1">
        <p className="whitespace-pre-wrap break-words text-message text-ink">
          {parseAnswer(answer, citations).map((seg, i) =>
            seg.kind === "text" ? (
              <span key={i}>{seg.text}</span>
            ) : (
              <button
                key={i}
                type="button"
                aria-label={sourceLabel(seg.citation)}
                onClick={(e) => open(seg.citation, e.currentTarget)}
                className="mx-0.5 inline-flex h-5 min-w-5 -translate-y-0.5 items-center justify-center rounded-full bg-marigold px-1.5 align-middle text-meta font-semibold text-on-marigold hover:brightness-95"
              >
                {seg.citation.marker}
              </button>
            ),
          )}
        </p>
        {citations.length > 0 && (
          <div className="mt-3">
            <p className="text-meta font-medium text-ink-muted">Sources</p>
            <ul className="mt-1 flex flex-wrap gap-2">
              {uniqueSources(citations).map((c) => (
                <li key={c.marker}>
                  <button
                    type="button"
                    aria-label={sourceLabel(c)}
                    onClick={(e) => open(c, e.currentTarget)}
                    className="inline-flex items-center gap-1.5 rounded-full border border-marigold-line bg-marigold/15 px-3 py-1 text-meta text-ink hover:bg-marigold/30"
                  >
                    <FileText size={13} aria-hidden="true" />
                    <span className="font-semibold">{c.marker}</span>
                    <span>{c.filename}</span>
                    <span className="text-ink-muted">p. {c.page}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-2 h-8 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard?.writeText(answer).then(() => {
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1500);
              });
            }}
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-meta text-ink-muted hover:bg-ink/5 hover:text-ink"
          >
            {copied ? (
              <Check size={14} aria-hidden="true" />
            ) : (
              <Copy size={14} aria-hidden="true" />
            )}
            {copied ? "Copied" : "Copy answer"}
          </button>
        </div>
      </div>
    </div>
  );
}
