import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// WCAG AA (extension.md §9.2): 4.5:1 for body text, 3:1 for large text and UI components.
type RGB = [number, number, number];

function themes(): { light: Record<string, RGB>; dark: Record<string, RGB> } {
  const css = readFileSync(resolve(__dirname, "../../src/styles/tokens.css"), "utf8");
  const block = (selector: string) => {
    const start = css.indexOf(selector);
    const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
    const out: Record<string, RGB> = {};
    for (const m of body.matchAll(/--([\w-]+):\s*(\d+)\s+(\d+)\s+(\d+)/g)) {
      out[m[1]!] = [Number(m[2]), Number(m[3]), Number(m[4])];
    }
    return out;
  };
  return { light: block(":root {"), dark: block(':root[data-theme="dark"]') };
}

function luminance([r, g, b]: RGB): number {
  const c = [r, g, b].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
}

export function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** Every text/icon-on-background pair the UI renders, with the AA threshold that applies. */
const PAIRS: [fg: string, bg: string, min: number, where: string][] = [
  ["ink", "paper", 4.5, "body text on the page background"],
  ["ink", "surface", 4.5, "messages, panels, cards"],
  ["ink-muted", "paper", 4.5, "hints on the page background"],
  ["ink-muted", "surface", 4.5, "hints, timestamps, the composer hint"],
  ["on-spruce", "spruce", 4.5, "user bubbles, primary buttons"],
  ["on-spruce-deep", "spruce-deep", 4.5, "sidebar text"],
  ["on-spruce-deep-muted", "spruce-deep", 4.5, "sidebar secondary text"],
  ["ink", "sky", 4.5, "insufficient-answer card text"],
  ["ink-muted", "sky", 4.5, "insufficient-answer hint line"],
  ["ink", "highlight", 4.5, "highlighted cited passage"],
  ["on-marigold", "marigold", 4.5, "citation chip numbers, filled starter chips"],
  ["coral", "surface", 4.5, "error text"],
  ["spruce", "surface", 3, "focus rings, links, send button (UI component)"],
  ["spruce", "paper", 3, "focus rings on the page background"],
  ["marigold-line", "surface", 3, "starter-prompt and source chip outlines (UI component)"],
  ["marigold-line", "paper", 3, "drop-zone outline on drag-over (UI component)"],
  ["on-coral", "coral", 4.5, "danger button text"],
  ["coral", "paper", 4.5, "error text on the page background"],
];

describe("design token contrast (WCAG AA)", () => {
  const { light, dark } = themes();
  for (const [name, tokens] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    it.each(PAIRS)(`${name}: %s on %s >= %f (%s)`, (fg, bg, min) => {
      expect(tokens[fg], fg).toBeDefined();
      expect(tokens[bg], bg).toBeDefined();
      expect(contrast(tokens[fg]!, tokens[bg]!)).toBeGreaterThanOrEqual(min);
    });
  }
});
