import { expect, type Page, test } from "@playwright/test";

import { apiToken, ask, signedIn, state } from "./support";

test.describe.configure({ mode: "serial" });

async function openFiles(page: Page) {
  await signedIn(page, "admin");
  await page.getByRole("link", { name: "Manage files" }).click();
  await page.waitForURL(/\/admin\/files/);
  await expect(page.getByRole("heading", { name: "Loan files" })).toBeVisible();
}

async function uploadVia(page: Page, paths: string[], group = "Underwriting") {
  await page.getByRole("button", { name: "Upload files" }).click();
  await page.getByLabel("Choose PDF files").setInputFiles(paths);
  await page.getByRole("dialog").getByText(group, { exact: true }).click();
  await page
    .getByRole("button", {
      name: `Upload ${paths.length} ${paths.length === 1 ? "file" : "files"}`,
    })
    .click();
}

function row(page: Page, filename: string) {
  return page.getByTestId("file-row").filter({ hasText: filename });
}

async function search(page: Page, filename: string) {
  await page.getByPlaceholder("Search by filename").fill(filename);
}

const adminBodies: string[] = [];

test("UI-T14: admin uploads two 80-page PDFs; Alice can then ask about them", async ({
  page,
  browser,
}) => {
  const { fixtures } = state();
  const [a, b] = fixtures.eighty;
  page.on("response", async (r) => {
    if (r.url().includes("/v1/admin/")) adminBodies.push(await r.text().catch(() => ""));
  });
  await openFiles(page);
  await uploadVia(page, [a!.path, b!.path]);
  const statuses = page.getByTestId("upload-status");
  await expect(
    statuses.filter({ hasText: /^(Uploading|Waiting|Processing)/ }).first(),
  ).toBeVisible();
  await expect(statuses.filter({ hasText: /^Processing, page \d+ of 80$/ }).first()).toBeVisible({
    timeout: 180_000,
  });
  await expect(page.getByText("2 files are ready")).toBeVisible({ timeout: 300_000 });
  await search(page, a!.filename);
  await expect(row(page, a!.filename)).toContainText("Ready");

  const alice = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
  await signedIn(alice, "alice");
  await ask(alice, a!.question);
  const answer = alice.getByTestId("assistant-answer");
  await expect(answer).toBeVisible({ timeout: 120_000 });
  await expect(
    answer.getByRole("button", { name: new RegExp(`^Source \\d+: ${a!.filename}`) }).first(),
  ).toBeVisible();
});

test("UI-T15: Bob has no Manage files link and the page tells him it is for administrators", async ({
  page,
}) => {
  await signedIn(page, "bob");
  await expect(page.getByRole("link", { name: "Manage files" })).toHaveCount(0);
  await page.goto("/admin/files");
  await expect(page.getByText("This page is for administrators.")).toBeVisible();
});

test("UI-T16: bad files show their own messages; valid files in the batch still upload", async ({
  page,
}) => {
  const { fixtures } = state();
  const valid = fixtures.eighty[2]!;
  await openFiles(page);
  await uploadVia(page, [
    fixtures.textAsPdf,
    fixtures.encrypted,
    fixtures.duplicate.path,
    valid.path,
  ]);
  const tray = page.getByRole("complementary", { name: "Uploads" });
  await expect(tray).toContainText("This file isn't a PDF.", { timeout: 60_000 });
  await expect(tray).toContainText(
    "This PDF is password protected. Remove the password and upload it again.",
  );
  await expect(tray).toContainText(
    `This file was already uploaded as ${fixtures.duplicate.existing}.`,
  );
  await expect(page.getByText("1 file is ready, 3 need attention")).toBeVisible({
    timeout: 300_000,
  });
  await search(page, valid.filename);
  await expect(row(page, valid.filename)).toContainText("Ready");
});

test("UI-T17: removing underwriting from a file's access hides it from Alice", async ({
  page,
  browser,
}) => {
  const a = state().fixtures.eighty[0]!;
  await openFiles(page);
  await search(page, a.filename);
  await row(page, a.filename)
    .getByRole("button", { name: `Actions for ${a.filename}` })
    .click();
  await page.getByRole("menuitem", { name: "Edit access" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByText("Underwriting", { exact: true }).click();
  await dialog.getByText("Compliance", { exact: true }).click();
  await dialog.getByRole("button", { name: "Save access" }).click();
  await expect(page.getByText("Access updated")).toBeVisible();

  const alice = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
  await signedIn(alice, "alice");
  await ask(alice, a.question);
  await expect(alice.getByTestId("insufficient")).toBeVisible({ timeout: 120_000 });
});

test("UI-T18: deleting a file removes it from the table, Alice's files, and answers", async ({
  page,
  browser,
  request,
}) => {
  const b = state().fixtures.eighty[1]!;
  await openFiles(page);
  await search(page, b.filename);
  await row(page, b.filename)
    .getByRole("button", { name: `Actions for ${b.filename}` })
    .click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await expect(page.getByText(`Delete ${b.filename}?`)).toBeVisible();
  await expect(page.getByText("It will stop appearing in answers for everyone.")).toBeVisible();
  await page.getByRole("button", { name: "Delete file" }).click();
  await expect(page.getByText("File deleted")).toBeVisible();
  await expect(row(page, b.filename)).toHaveCount(0);

  // Alice's file list (all pages of /v1/me/documents) and her answers.
  const token = await apiToken("alice");
  let cursor: string | null = null;
  const names: string[] = [];
  do {
    const r: Awaited<ReturnType<typeof request.get>> = await request.get(
      `/v1/me/documents?limit=100${cursor ? `&cursor=${cursor}` : ""}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    const body = (await r.json()) as { items: { filename: string }[]; next_cursor: string | null };
    names.push(...body.items.map((i) => i.filename));
    cursor = body.next_cursor;
  } while (cursor);
  expect(names.length).toBeGreaterThan(0);
  expect(names).not.toContain(b.filename);
  const alice = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
  await signedIn(alice, "alice");
  await ask(alice, b.question);
  await expect(
    alice.locator('[data-testid="insufficient"], [data-testid="assistant-answer"]'),
  ).toBeVisible({ timeout: 120_000 });
  await expect(
    alice.getByRole("button", { name: new RegExp(`^Source \\d+: ${b.filename}`) }),
  ).toHaveCount(0);
});

test("UI-T19: an admin without groups cannot read the file they uploaded", async ({ page }) => {
  const c = state().fixtures.eighty[3]!;
  await openFiles(page);
  await uploadVia(page, [c.path]);
  await expect(page.getByText("1 file is ready")).toBeVisible({ timeout: 300_000 });
  await page.getByRole("link", { name: "LoanLens" }).click();
  await page.waitForURL(/\/dashboard/);
  await ask(page, c.question);
  await expect(page.getByTestId("insufficient")).toBeVisible({ timeout: 120_000 });
});

test("UI-T20: the admin page shows metadata only, in the DOM and on the network", async ({
  page,
}) => {
  page.on("response", async (r) => {
    if (r.url().includes("/v1/admin/")) adminBodies.push(await r.text().catch(() => ""));
  });
  await openFiles(page);
  await search(page, "loan_");
  await expect(page.getByTestId("file-row").first()).toBeVisible();
  const dom = await page.content();
  const network = adminBodies.join("\n");
  expect(adminBodies.length).toBeGreaterThan(0);
  for (const pageOnlyText of [
    "Borrower name:",
    "Statement from",
    "Transfers between the borrower",
  ]) {
    expect(dom).not.toContain(pageOnlyText);
    expect(network).not.toContain(pageOnlyText);
  }
  expect(network).not.toMatch(/"(text|excerpt)"\s*:/);
});

test("UI-T21: after closing the dialog and reloading, in-progress jobs still show live status", async ({
  page,
}) => {
  const { fixtures } = state();
  const [d, e] = [fixtures.eighty[4]!, fixtures.eighty[5]!];
  await openFiles(page);
  await uploadVia(page, [d.path, e.path]); // the dialog closes as the batch starts
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const statuses = page.getByTestId("upload-status");
  await expect(statuses.filter({ hasText: /^(Waiting|Processing)/ })).toHaveCount(2, {
    timeout: 60_000,
  });
  await page.reload();
  for (const f of [d, e]) {
    await search(page, f.filename);
    await expect(row(page, f.filename)).toContainText(/Waiting|Processing|Ready/);
  }
  await search(page, e.filename);
  await expect(row(page, e.filename)).toContainText("Ready", { timeout: 300_000 });
});
