import type { Citation } from "../../api/types";

export type AnswerSegment =
  { kind: "text"; text: string } | { kind: "citation"; citation: Citation };

/**
 * Split an answer into text and citation-chip segments (UI-F10). A `[n]` marker becomes a chip
 * only when a citation with that marker exists; anything else stays plain text.
 */
export function parseAnswer(answer: string, citations: Citation[]): AnswerSegment[] {
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const segments: AnswerSegment[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(answer)) !== null) {
    const citation = byMarker.get(Number(match[1]));
    if (!citation) continue;
    if (match.index > last) segments.push({ kind: "text", text: answer.slice(last, match.index) });
    segments.push({ kind: "citation", citation });
    last = match.index + match[0].length;
  }
  if (last < answer.length) segments.push({ kind: "text", text: answer.slice(last) });
  return mergeText(segments);
}

function mergeText(segments: AnswerSegment[]): AnswerSegment[] {
  const out: AnswerSegment[] = [];
  for (const s of segments) {
    const prev = out[out.length - 1];
    if (s.kind === "text" && prev?.kind === "text") prev.text += s.text;
    else out.push(s.kind === "text" ? { ...s } : s);
  }
  return out;
}

/** Each citation once, in marker order, for the "Sources" row. */
export function uniqueSources(citations: Citation[]): Citation[] {
  return [...citations].sort((a, b) => a.marker - b.marker);
}
