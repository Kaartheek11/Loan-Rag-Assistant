import type { Config } from "tailwindcss";

// Colors come from CSS variables in src/styles/tokens.css (light, dark, and [data-theme]).
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        spruce: token("spruce"),
        "spruce-deep": token("spruce-deep"),
        marigold: token("marigold"),
        highlight: token("highlight"),
        paper: token("paper"),
        surface: token("surface"),
        ink: token("ink"),
        "ink-muted": token("ink-muted"),
        coral: token("coral"),
        sky: token("sky"),
        "on-spruce": token("on-spruce"),
        "on-spruce-deep": token("on-spruce-deep"),
        "on-spruce-deep-muted": token("on-spruce-deep-muted"),
        line: token("line"),
        "marigold-line": token("marigold-line"),
        "on-marigold": token("on-marigold"),
        "on-coral": token("on-coral"),
      },
      fontFamily: {
        display: ['"Bricolage Grotesque"', "system-ui", "sans-serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
      },
      fontSize: {
        meta: ["0.8125rem", { lineHeight: "1.4" }],
        ui: ["0.9375rem", { lineHeight: "1.45" }],
        message: ["1rem", { lineHeight: "1.6" }],
        panel: ["1.25rem", { lineHeight: "1.3" }],
        greeting: ["1.75rem", { lineHeight: "1.2" }],
        welcome: ["2.5rem", { lineHeight: "1.1" }],
      },
      maxWidth: { reading: "70ch", chat: "760px" },
    },
  },
  plugins: [],
} satisfies Config;
