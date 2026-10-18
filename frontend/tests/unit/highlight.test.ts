import { describe, expect, it } from "vitest";

import { findExcerpt, highlightSegments } from "../../src/lib/highlight";

describe("highlight", () => {
  const page =
    "BANK STATEMENTS\nThe ending balance on the April\nstatement was $4,512.88.  Next line.";

  it("finds an excerpt despite whitespace differences", () => {
    const r = findExcerpt(page, "The ending balance on the April statement\n was $4,512.88.");
    expect(r).not.toBeNull();
    expect(page.slice(r!.start, r!.end)).toBe(
      "The ending balance on the April\nstatement was $4,512.88.",
    );
  });

  it("returns no highlight when not found", () => {
    const { segments, found } = highlightSegments(page, "not on this page");
    expect(found).toBe(false);
    expect(segments).toEqual([{ text: page, highlighted: false }]);
  });

  it("highlights the first of two occurrences", () => {
    const text = "alpha beta. alpha beta.";
    const { segments } = highlightSegments(text, "alpha beta.");
    expect(segments).toEqual([
      { text: "alpha beta.", highlighted: true },
      { text: " alpha beta.", highlighted: false },
    ]);
  });

  it("keeps all text when splitting", () => {
    const { segments } = highlightSegments(page, "statement was $4,512.88.");
    expect(segments.map((s) => s.text).join("")).toBe(page);
    expect(segments.filter((s) => s.highlighted)).toHaveLength(1);
  });
});
