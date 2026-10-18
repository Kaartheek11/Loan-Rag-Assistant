/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Production builds drop console.* and debugger statements (UI-S8).
// `npm run dev` proxies the API and Keycloak to the Caddy origin, so the app still talks to a
// single origin; open the app through https://localhost in normal use.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  esbuild: mode === "production" ? { drop: ["console", "debugger"] } : {},
  build: { sourcemap: false, outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/v1": { target: "https://localhost", changeOrigin: true, secure: false },
      "/auth": { target: "https://localhost", changeOrigin: true, secure: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.test.ts?(x)", "tests/component/**/*.test.ts?(x)"],
    css: false,
  },
}));
