import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.node } },
    settings: { react: { version: "18.3" } },
    plugins: { react, "react-hooks": reactHooks, "jsx-a11y": jsxA11y },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      // UI-S2: all API text is rendered as text nodes. dangerouslySetInnerHTML is banned.
      "react/no-danger": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "dangerouslySetInnerHTML is banned (UI-S2): render API text as text nodes.",
        },
        {
          selector: "MemberExpression[property.name='innerHTML']",
          message: "innerHTML is banned (UI-S2).",
        },
      ],
      // UI-S3/UI-S4: nothing sensitive goes to browser storage.
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Browser storage is not allowed (UI-S3, UI-S4)." },
        { name: "sessionStorage", message: "Browser storage is not allowed (UI-S3, UI-S4)." },
        { name: "indexedDB", message: "Browser storage is not allowed (UI-S3, UI-S4)." },
      ],
    },
  },
  {
    // UI-T7 reads browser storage to prove the app never writes to it.
    files: ["tests/e2e/**/*.ts"],
    rules: { "no-restricted-globals": "off" },
  },
);
