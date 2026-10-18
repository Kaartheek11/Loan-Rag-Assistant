import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run against the real Docker Compose stack (extension.md §12.2):
//   docker compose -f docker/docker-compose.yml [-f docker/docker-compose.host-ollama.yml] up -d
// with the synthetic corpus ingested and users seeded. Sign-ins use the Keycloak page.
export default defineConfig({
  testDir: "./tests/e2e",
  globalSetup: "./tests/e2e/global-setup.ts",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost",
    ignoreHTTPSErrors: true, // Caddy's local CA in development
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1360, height: 900 } },
    },
  ],
});
