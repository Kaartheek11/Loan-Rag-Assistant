import { describe, expect, it } from "vitest";

import type { Citation } from "../../src/api/types";
import { parseAnswer, uniqueSources } from "../../src/features/chat/citations";

const cite = (marker: number): Citation => ({
  marker,
  doc_id: `d${marker}`,
  filename: `loan_00000${marker}.pdf`,
  page: marker,
  excerpt: "x",
});

describe("parseAnswer", () => {
  it("splits text and citation chips", () => {
    const segs = parseAnswer("Rate is 6.5% [1] and term 30 years [2].", [cite(1), cite(2)]);
    expect(segs.map((s) => (s.kind === "text" ? s.text : `#${s.citation.marker}`))).toEqual([
      "Rate is 6.5% ",
      "#1",
      " and term 30 years ",
      "#2",
      ".",
    ]);
  });

  it("keeps markers without a matching citation as plain text", () => {
    const segs = parseAnswer("See [3] and [1].", [cite(1)]);
    expect(segs[0]).toEqual({ kind: "text", text: "See [3] and " });
    expect(segs[1]).toMatchObject({ kind: "citation" });
    expect(segs[2]).toEqual({ kind: "text", text: "." });
  });

  it("handles markers at the start, the end, and next to punctuation", () => {
    expect(parseAnswer("[1] starts", [cite(1)])[0]).toMatchObject({ kind: "citation" });
    const end = parseAnswer("ends [1]", [cite(1)]);
    expect(end[end.length - 1]).toMatchObject({ kind: "citation" });
    const punct = parseAnswer("Yes,[1][2].", [cite(1), cite(2)]);
    expect(punct.map((s) => s.kind)).toEqual(["text", "citation", "citation", "text"]);
  });

  it("lists sources once in marker order", () => {
    expect(uniqueSources([cite(2), cite(1)]).map((c) => c.marker)).toEqual([1, 2]);
  });
});
