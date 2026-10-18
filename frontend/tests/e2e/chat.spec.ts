import { readFileSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

import { apiToken, ask, psql, signedIn, state } from "./support";

async function openCitation(page: Page, filename: string, pageNo: number) {
  await page
    .getByRole("button", { name: new RegExp(`^Source \\d+: ${filename}, page ${pageNo}$`) })
    .first()
    .click();
}

test("UI-T2: Alice's answer has citation chips that open the highlighted source page", async ({
  page,
}) => {
  const { deep } = state();
  await signedIn(page, "alice");
  await ask(page, deep.question);
  const answer = page.getByTestId("assistant-answer");
  await expect(answer).toBeVisible({ timeout: 120_000 });
  const chips = answer.getByRole("button", {
    name: new RegExp(`^Source \\d+: ${deep.filename}, page ${deep.page}$`),
  });
  await expect(chips.first()).toBeVisible();
  await expect(answer.getByText("Sources")).toBeVisible();
  await openCitation(page, deep.filename, deep.page);
  await expect(page.getByTestId("page-indicator")).toHaveText(
    `Page ${deep.page} of ${deep.pageCount}`,
  );
  const mark = page.getByTestId("cited-passage");
  await expect(mark).toBeVisible();
  await expect(mark).toBeInViewport();
});

test("UI-T3: Bob gets the neutral card and never sees the hidden file", async ({ page }) => {
  const { deep } = state();
  const bodies: string[] = [];
  page.on("response", async (r) => {
    if (r.url().includes("/v1/")) bodies.push(await r.text().catch(() => ""));
  });
  await signedIn(page, "bob");
  await ask(page, deep.question);
  const card = page.getByTestId("insufficient");
  await expect(card).toBeVisible({ timeout: 120_000 });
  // Same fixed text as for a loan that does not exist (architecture D58): nothing file-specific.
  await expect(card).toHaveText(
    "I couldn't find information to answer that in the files you have access to." +
      "Try rephrasing, or include the loan number. If you think you should have access to this loan file, please check with your admin.",
  );
  await page.waitForLoadState("networkidle");
  expect(await page.content()).not.toContain(deep.filename);
  expect(bodies.join("\n")).not.toContain(deep.filename);
});

test("UI-T4: removing Alice from underwriting updates the sidebar and her answers live", async ({
  page,
}) => {
  const { deep } = state();
  await signedIn(page, "alice");
  await ask(page, deep.question);
  await expect(page.getByTestId("assistant-answer")).toBeVisible({ timeout: 120_000 });
  psql("DELETE FROM user_groups WHERE user_id = 'u_alice' AND group_name = 'underwriting'");
  try {
    await page.evaluate(() => window.dispatchEvent(new Event("focus"))); // refocus
    await expect(page.getByText("Your access was updated.")).toBeVisible();
    await expect(page.getByTestId("access-card")).not.toContainText("Underwriting");
    await ask(page, deep.question);
    await expect(page.getByTestId("insufficient")).toBeVisible({ timeout: 120_000 });
    await openCitation(page, deep.filename, deep.page); // the earlier answer's chip
    await expect(page.getByText("This page isn't available.")).toBeVisible();
  } finally {
    psql("INSERT INTO user_groups VALUES ('u_alice', 'underwriting') ON CONFLICT DO NOTHING");
  }
});

test("UI-T9: the 31st question within a minute shows the rate-limit card", async ({
  page,
  request,
}) => {
  await signedIn(page, "henry");
  // The limit is per user, whatever the client: send 30 questions as Henry straight to the API
  // (concurrently, so they land well within the minute), then ask the 31st in the app.
  const token = await apiToken("henry");
  const codes = await Promise.all(
    Array.from({ length: 30 }, (_, i) =>
      request
        .post("/v1/query", {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            question: `Rate check ${i + 1} for loan LN-2099-${String(i + 1).padStart(6, "0")}?`,
          },
        })
        .then((r) => r.status()),
    ),
  );
  expect(codes.filter((c) => c === 200)).toHaveLength(30);
  await ask(page, "Rate check 31 for loan LN-2099-000031?");
  const card = page.getByTestId("error-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText(
    "You're asking questions quickly. Wait a minute, then try again.",
  );
  await expect(card.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(card).toHaveAttribute("data-error-kind", "rate_limited");
});

test("UI-T13: markup inside a page is shown as plain text and never runs", async ({
  page,
  request,
}) => {
  const { fixtures } = state();
  const token = await apiToken("admin");
  const up = await request.post("/v1/admin/uploads", {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      file: {
        name: "markup-test.pdf",
        mimeType: "application/pdf",
        buffer: readFileSync(fixtures.xss.path),
      },
      "groups[]": "underwriting",
    },
  });
  expect(up.status()).toBe(202);
  const { job_id } = await up.json();
  await expect
    .poll(
      async () => {
        const r = await request.get(`/v1/admin/uploads/${job_id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        return (await r.json()).status;
      },
      { timeout: 180_000, intervals: [2000] },
    )
    .toBe("indexed");

  let dialogs = 0;
  page.on("dialog", async (d) => {
    dialogs += 1;
    await d.dismiss();
  });
  await signedIn(page, "alice");
  await ask(
    page,
    `What escrow balance was being checked in the review notes for loan ${fixtures.xss.loanNumber}?`,
  );
  const answer = page.getByTestId("assistant-answer");
  await expect(answer).toBeVisible({ timeout: 120_000 });
  await answer
    .getByRole("button", { name: /^Source \d+: markup-test\.pdf, page 2$/ })
    .first()
    .click();
  const body = page.getByTestId("page-body");
  await expect(body).toContainText("<img src=x onerror=alert(1)>");
  await expect(body).toContainText("<script>alert('x')</script>");
  expect(await page.locator('img[src="x"]').count()).toBe(0);
  expect(await body.locator("script").count()).toBe(0);
  expect(dialogs).toBe(0);
});

test("UI-T22: Go to page jumps within a long file; the highlight stays on the cited page", async ({
  page,
}) => {
  const { deep } = state();
  await signedIn(page, "alice");
  await ask(page, deep.question);
  await expect(page.getByTestId("assistant-answer")).toBeVisible({ timeout: 120_000 });
  await openCitation(page, deep.filename, deep.page);
  await expect(page.getByTestId("page-indicator")).toHaveText(
    `Page ${deep.page} of ${deep.pageCount}`,
  );
  await expect(page.getByTestId("cited-passage")).toBeVisible();
  const goTo = page.getByLabel("Go to page");
  await goTo.fill("10");
  await goTo.press("Enter");
  await expect(page.getByTestId("page-indicator")).toHaveText(`Page 10 of ${deep.pageCount}`);
  await expect(page.getByTestId("cited-passage")).toHaveCount(0);
  await goTo.fill(String(deep.page));
  await goTo.press("Enter");
  await expect(page.getByTestId("page-indicator")).toHaveText(
    `Page ${deep.page} of ${deep.pageCount}`,
  );
  await expect(page.getByTestId("cited-passage")).toBeVisible();
});
