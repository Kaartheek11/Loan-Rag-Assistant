import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

import { ask, noHorizontalScroll, signedIn, state } from "./support";

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map(
      (v) =>
        `${v.id}: ${v.nodes
          .map((n) => n.target.join(" "))
          .slice(0, 3)
          .join(", ")}`,
    );
}

for (const scheme of ["light", "dark"] as const) {
  test(`UI-T10: no serious axe violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    expect(await seriousViolations(page), "welcome").toEqual([]);

    await signedIn(page, "alice");
    expect(await seriousViolations(page), "dashboard").toEqual([]);

    await page.getByTestId("file-list").getByRole("button").first().click();
    await expect(page.getByTestId("page-indicator")).toBeVisible();
    await expect(page.getByTestId("page-body")).not.toContainText("Loading page");
    expect(await seriousViolations(page), "source panel").toEqual([]);
  });
}

test.describe("UI-T11: phone width (390px)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("drawer sidebar and full-screen source sheet, no sideways scrolling", async ({ page }) => {
    const { deep } = state();
    await signedIn(page, "alice").catch(async () => {
      // On phones the user card lives in the drawer: wait for the dashboard instead.
      await page.waitForURL(/\/dashboard/);
    });
    await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
    await noHorizontalScroll(page);
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByTestId("user-card")).toBeVisible();
    await expect(page.getByTestId("access-card")).toBeVisible();
    await noHorizontalScroll(page);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("user-card")).toHaveCount(0);

    await ask(page, deep.question);
    const answer = page.getByTestId("assistant-answer");
    await expect(answer).toBeVisible({ timeout: 120_000 });
    await noHorizontalScroll(page);
    await answer
      .getByRole("button", { name: new RegExp(`^Source \\d+: ${deep.filename}`) })
      .first()
      .click();
    const sheet = page.getByRole("dialog");
    await expect(sheet).toBeVisible();
    const box = await sheet.boundingBox();
    expect(box?.width).toBe(390);
    await expect(page.getByTestId("cited-passage")).toBeVisible();
    await noHorizontalScroll(page);
    await page.getByRole("button", { name: "Close source" }).click();
    await expect(sheet).toHaveCount(0);
  });
});
