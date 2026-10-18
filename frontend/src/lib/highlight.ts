export type TextSegment = { text: string; highlighted: boolean };

/**
 * Find a citation excerpt inside page text, ignoring whitespace differences on both sides
 * (extension.md §8.7). Returns the original-text range of the first match, or null.
 */
export function findExcerpt(
  pageText: string,
  excerpt: string,
): { start: number; end: number } | null {
  const target = excerpt.replace(/\s+/g, " ").trim();
  if (!target) return null;
  // Normalized page text plus a map from each normalized index back to the original index.
  let normalized = "";
  const map: number[] = [];
  let inSpace = false;
  for (let i = 0; i < pageText.length; i++) {
    const ch = pageText[i]!;
    if (/\s/.test(ch)) {
      if (!inSpace && normalized.length > 0) {
        normalized += " ";
        map.push(i);
      }
      inSpace = true;
    } else {
      normalized += ch;
      map.push(i);
      inSpace = false;
    }
  }
  const at = normalized.indexOf(target);
  if (at < 0) return null;
  const start = map[at]!;
  const end = map[at + target.length - 1]! + 1;
  return { start, end };
}

/** Split page text into plain and highlighted segments (rendered as React elements). */
export function highlightSegments(
  pageText: string,
  excerpt: string | null,
): {
  segments: TextSegment[];
  found: boolean;
} {
  if (!excerpt) return { segments: [{ text: pageText, highlighted: false }], found: false };
  const range = findExcerpt(pageText, excerpt);
  if (!range) return { segments: [{ text: pageText, highlighted: false }], found: false };
  const segments: TextSegment[] = [];
  if (range.start > 0) segments.push({ text: pageText.slice(0, range.start), highlighted: false });
  segments.push({ text: pageText.slice(range.start, range.end), highlighted: true });
  if (range.end < pageText.length)
    segments.push({ text: pageText.slice(range.end), highlighted: false });
  return { segments, found: true };
}
