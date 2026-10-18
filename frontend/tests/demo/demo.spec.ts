import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { type Browser, type BrowserContext, expect, type Page, test } from "@playwright/test";

import { ask, REPO, signedIn, state } from "../e2e/support";

const VIDEO_DIR = resolve(REPO, "frontend/test-results/demo-videos");
const SHOTS = resolve(REPO, "docs/screenshots");

type Actor = { name: string; context: BrowserContext; page: Page; start: number };
const scenes: { actor: string; from: number; to: number; title: string }[] = [];

async function actor(browser: Browser, name: string): Promise<Actor> {
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: resolve(VIDEO_DIR, name), size: { width: 1280, height: 800 } },
  });
  const page = await context.newPage();
  return { name, context, page, start: Date.now() };
}

async function scene(a: Actor, title: string, run: () => Promise<void>) {
  const from = (Date.now() - a.start) / 1000;
  await run();
  await a.page.waitForTimeout(1500);
  scenes.push({ actor: a.name, from, to: (Date.now() - a.start) / 1000, title });
}

test("LoanLens demo", async ({ browser }) => {
  const { fixtures } = state();
  const file = fixtures.eighty[0]!;
  const admin = await actor(browser, "admin");
  const alice = await actor(browser, "alice");
  const bob = await actor(browser, "bob");

  await scene(admin, "An admin uploads loan files for underwriting", async () => {
    const p = admin.page;
    await signedIn(p, "admin");
    await p.getByRole("link", { name: "Manage files" }).click();
    await p.getByRole("button", { name: "Upload files" }).click();
    await p
      .getByLabel("Choose PDF files")
      .setInputFiles([fixtures.eighty[0]!.path, fixtures.eighty[1]!.path]);
    await p.getByRole("dialog").getByText("Underwriting", { exact: true }).click();
    await p.waitForTimeout(800);
    await p.getByRole("button", { name: "Upload 2 files" }).click();
    await expect(p.getByText(/Processing, page \d+ of 80/).first()).toBeVisible({
      timeout: 300_000,
    });
    await expect(p.getByText("2 files are ready")).toBeVisible({ timeout: 600_000 });
    await p.getByPlaceholder("Search by filename").fill(file.filename);
    await expect(p.getByTestId("file-row").filter({ hasText: file.filename })).toContainText(
      "Ready",
    );
  });

  await scene(alice, "Alice asks about one and opens a citation deep in the file", async () => {
    const p = alice.page;
    await signedIn(p, "alice");
    await p.waitForTimeout(1200);
    await ask(p, file.question);
    const answer = p.getByTestId("assistant-answer");
    await expect(answer).toBeVisible({ timeout: 180_000 });
    await p.waitForTimeout(1500);
    await answer
      .getByRole("button", { name: new RegExp(`^Source \\d+: ${file.filename}`) })
      .first()
      .click();
    await expect(p.getByTestId("cited-passage")).toBeVisible();
    await p.waitForTimeout(3500);
  });

  // Screenshots for the README (light and dark), from Alice's session.
  mkdirSync(SHOTS, { recursive: true });
  for (const scheme of ["light", "dark"] as const) {
    await alice.page.emulateMedia({ colorScheme: scheme });
    await alice.page.waitForTimeout(400);
    await alice.page.screenshot({ path: resolve(SHOTS, `dashboard-${scheme}.png`) });
  }
  await alice.page.emulateMedia({ colorScheme: "light" });

  await scene(bob, "Bob asks the same question and gets the neutral card", async () => {
    const p = bob.page;
    await signedIn(p, "bob");
    await ask(p, file.question);
    await expect(p.getByTestId("insufficient")).toBeVisible({ timeout: 180_000 });
    await p.waitForTimeout(2500);
  });

  await scene(admin, "The admin removes underwriting from the file's access", async () => {
    const p = admin.page;
    await p
      .getByTestId("file-row")
      .filter({ hasText: file.filename })
      .getByRole("button", { name: `Actions for ${file.filename}` })
      .click();
    await p.getByRole("menuitem", { name: "Edit access" }).click();
    const dialog = p.getByRole("dialog");
    await dialog.getByText("Underwriting", { exact: true }).click();
    await dialog.getByText("Compliance", { exact: true }).click();
    await p.waitForTimeout(800);
    await dialog.getByRole("button", { name: "Save access" }).click();
    await expect(p.getByText("Access updated")).toBeVisible();
  });

  await scene(alice, "Alice's sidebar updates live; her next question finds nothing", async () => {
    const p = alice.page;
    const before = await p.getByTestId("access-card").innerText();
    await p.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(p.getByTestId("access-card")).not.toHaveText(before);
    await p.waitForTimeout(1500);
    await ask(p, file.question);
    await expect(p.getByTestId("insufficient")).toBeVisible({ timeout: 180_000 });
    await p.waitForTimeout(2500);
  });

  for (const a of [admin, alice, bob]) await a.context.close();
  const videos: Record<string, string> = {};
  for (const a of [admin, alice, bob]) videos[a.name] = await a.page.video()!.path();
  writeFileSync(resolve(VIDEO_DIR, "scenes.json"), JSON.stringify({ scenes, videos }, null, 2));
});
