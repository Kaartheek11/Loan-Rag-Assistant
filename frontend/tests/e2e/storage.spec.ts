import { expect, test } from "@playwright/test";

import { ask, signedIn, state } from "./support";

const JWT = /eyJ[\w-]+\.[\w-]+\.[\w-]+/;

test("UI-T7: no tokens or document content in browser storage", async ({ page, context }) => {
  const { deep } = state();
  const tokens = new Set<string>();
  let answer = "";
  let pageText = "";
  page.on("request", (req) => {
    const auth = req.headers()["authorization"];
    if (auth?.startsWith("Bearer ")) tokens.add(auth.slice(7));
  });
  page.on("response", async (res) => {
    if (res.url().endsWith("/v1/query") && res.ok()) answer = (await res.json()).answer;
    if (/\/v1\/documents\/.+\/pages\//.test(res.url()) && res.ok())
      pageText = (await res.json()).text;
  });
  await signedIn(page, "alice");
  await ask(page, deep.question);
  await expect(page.getByTestId("assistant-answer")).toBeVisible({ timeout: 120_000 });
  await page
    .getByRole("button", { name: new RegExp(`^Source \\d+: ${deep.filename}`) })
    .first()
    .click();
  await expect(page.getByTestId("cited-passage")).toBeVisible();
  expect(tokens.size).toBeGreaterThan(0);
  expect(answer).not.toBe("");
  expect(pageText).not.toBe("");

  const stored = await page.evaluate(async () => {
    const dump = (s: Storage) => Object.keys(s).map((k) => `${k}=${s.getItem(k)}`);
    const dbs = "databases" in indexedDB ? await indexedDB.databases() : [];
    return { local: dump(window.localStorage), session: dump(window.sessionStorage), dbs };
  });
  const cookies = await context.cookies();
  const everything = [
    ...stored.local,
    ...stored.session,
    ...cookies.map((c) => `${c.name}=${c.value}`),
  ].join("\n");
  for (const token of tokens) expect(everything).not.toContain(token);
  expect([...stored.local, ...stored.session].join("\n")).not.toMatch(JWT);
  expect(stored.dbs).toEqual([]);
  for (const secret of [deep.question, answer.slice(0, 40), pageText.slice(0, 40)]) {
    expect(everything).not.toContain(secret);
  }
  // The app sets no cookies of its own; Keycloak's httpOnly session cookies are scoped to /auth.
  for (const c of cookies) expect(c.path.startsWith("/auth")).toBe(true);
});
