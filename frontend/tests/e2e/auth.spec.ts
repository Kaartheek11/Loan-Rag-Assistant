import { expect, type Page, test } from "@playwright/test";

import { ask, envValue, kcAdmin, signedIn, signIn, state } from "./support";

test("UI-T1: Alice signs in, sees 'Checking your access', then her access", async ({ page }) => {
  let me: { display_name: string; groups: string[]; readable_document_count: number } | null = null;
  // Slow /v1/me slightly so the short "Checking your access" state is observable.
  await page.route("**/v1/me", async (route) => {
    await new Promise((r) => setTimeout(r, 1200));
    await route.continue();
  });
  page.on("response", async (r) => {
    if (r.url().endsWith("/v1/me") && r.ok()) me = await r.json();
  });
  await signIn(page, "alice");
  await expect(page.getByText("Checking your access")).toBeVisible();
  await expect(page.getByTestId("user-card")).toContainText("Alice Underwriter");
  const card = page.getByTestId("access-card");
  await expect(card).toContainText("Underwriting");
  await expect(card).toContainText("Western region");
  expect(me).not.toBeNull();
  await expect(card).toContainText(
    `${me!.readable_document_count.toLocaleString("en-US")} loan files`,
  );
  await expect(page.getByRole("textbox", { name: "Your question" })).toBeVisible();
});

test("UI-T5: a Keycloak user with no Postgres row gets the no-access screen", async ({ page }) => {
  const username = `ghost${Date.now()}`;
  const created = await kcAdmin("POST", "/users", {
    username,
    enabled: true,
    email: `${username}@example.test`,
    emailVerified: true,
    firstName: "Ghost",
    lastName: "User",
    credentials: [{ type: "password", value: "Ghost-pass-123", temporary: false }],
  });
  expect(created.status).toBe(201);
  const id = created.headers.get("location")!.split("/").pop()!;
  try {
    await signIn(page, username, "Ghost-pass-123");
    await page.waitForURL(/\/no-access/);
    await expect(page.getByText(/hasn't been set up for LoanLens yet/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Your question" })).toHaveCount(0);
  } finally {
    await kcAdmin("DELETE", `/users/${id}`);
  }
});

test("UI-T6: after sign-out, the back button never shows the old conversation", async ({
  page,
}) => {
  await signedIn(page, "henry");
  const question = "What is the escrow balance on loan LN-2099-000001?";
  await ask(page, question);
  await expect(page.getByTestId("insufficient")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL((u) => u.pathname === "/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.goBack();
  await page.waitForLoadState("networkidle");
  await expect(page.getByText(question)).toHaveCount(0);
  const onWelcomeOrSignIn =
    (await page.getByRole("button", { name: "Sign in" }).count()) > 0 ||
    (await page.locator("#kc-login").count()) > 0;
  expect(onWelcomeOrSignIn).toBe(true);
});

test("UI-T8: silent refresh keeps a 1-minute token alive for 3 minutes", async ({ page }) => {
  test.setTimeout(330_000);
  const original = (await (await kcAdmin("GET", "")).json()) as { accessTokenLifespan: number };
  await kcAdmin("PUT", "", { accessTokenLifespan: 60 });
  try {
    await signedIn(page, "henry");
    const start = Date.now();
    let asked = 0;
    while (Date.now() - start < 190_000) {
      await ask(page, `Question ${asked + 1} about loan LN-2099-000002?`);
      asked += 1;
      await expect(page.getByTestId("insufficient")).toHaveCount(asked);
      await page.waitForTimeout(40_000);
    }
    expect(asked).toBeGreaterThanOrEqual(4);
    await expect(page).toHaveURL(/\/dashboard/); // never sent back to sign in
    await expect(page.locator("#kc-login")).toHaveCount(0);
  } finally {
    await kcAdmin("PUT", "", { accessTokenLifespan: original.accessTokenLifespan });
  }
});

async function focusVisible(page: Page) {
  const style = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { style: cs.outlineStyle, width: cs.outlineWidth };
  });
  expect(style).not.toBeNull();
  expect(style!.style).not.toBe("none");
  expect(style!.width).not.toBe("0px");
}

async function tabTo(page: Page, match: (label: string) => boolean, key = "Tab", max = 80) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press(key);
    const label = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el ? (el.getAttribute("aria-label") ?? el.textContent?.trim() ?? "") : "";
    });
    if (match(label)) return;
  }
  throw new Error("element not reachable by keyboard");
}

test("UI-T12: keyboard only: sign in, ask, open a citation, close it, sign out", async ({
  page,
}) => {
  const { deep } = state();
  await page.goto("/");
  await tabTo(page, (l) => l === "Sign in");
  await focusVisible(page);
  await page.keyboard.press("Enter");
  await expect(page.locator("#username")).toBeFocused(); // Keycloak autofocuses the field
  await page.keyboard.type("alice");
  await page.keyboard.press("Tab");
  await page.keyboard.type(envValue("DEV_USER_PASSWORD"));
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/dashboard/);
  await tabTo(page, (l) => l === "Your question" || l === "", "Tab", 120).catch(() => undefined);
  await page.getByRole("textbox", { name: "Your question" }).focus();
  await page.keyboard.type(deep.question);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("assistant-answer")).toBeVisible({ timeout: 120_000 });
  const chipLabel = `${deep.filename}, page ${deep.page}`;
  await tabTo(page, (l) => l.startsWith("Source ") && l.endsWith(chipLabel), "Shift+Tab");
  await focusVisible(page);
  const opener = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("cited-passage")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("page-body")).toHaveCount(0);
  await expect // focus returns to the chip (restored just after the panel unmounts)
    .poll(() => page.evaluate(() => document.activeElement?.getAttribute("aria-label")))
    .toBe(opener);
  await focusVisible(page);
  await tabTo(page, (l) => l === "Sign out", "Shift+Tab", 200);
  await focusVisible(page);
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => u.pathname === "/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
