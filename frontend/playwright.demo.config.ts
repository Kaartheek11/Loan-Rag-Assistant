import { defineConfig, devices } from "@playwright/test";

// Records the README demo video and screenshots (extension.md Phase I) against the running
// stack:  npx playwright test -c playwright.demo.config.ts   then  node tests/demo/stitch.mjs
export default defineConfig({
  testDir: "./tests/demo",
  globalSetup: "./tests/e2e/global-setup.ts",
  timeout: 900_000,
  expect: { timeout: 60_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "https://localhost",
    ignoreHTTPSErrors: true,
    ...devices["Desktop Chrome"],
    viewport: { width: 1280, height: 800 },
    launchOptions: { slowMo: 120 },
  },
});
