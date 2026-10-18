import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, type Page } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO = resolve(HERE, "../../..");
export const STATE_FILE = resolve(HERE, "../../.e2e-state/state.json");

export type E2EState = {
  deep: { question: string; filename: string; page: number; pageCount: number; loanNumber: string };
  fixtures: {
    eighty: { path: string; filename: string; loanNumber: string; question: string }[];
    textAsPdf: string;
    encrypted: string;
    duplicate: { path: string; existing: string };
    xss: { path: string; loanNumber: string; question: string };
    extra: string[];
  };
};

export function state(): E2EState {
  return JSON.parse(readFileSync(STATE_FILE, "utf8")) as E2EState;
}

export function envValue(key: string): string {
  const line = readFileSync(resolve(REPO, ".env"), "utf8")
    .split("\n")
    .find((l) => l.startsWith(`${key}=`));
  if (!line) throw new Error(`${key} missing from .env`);
  return line.slice(key.length + 1).trim();
}

/** Run SQL as the database owner in the Compose Postgres container (test setup only). */
export function psql(sql: string): string {
  return execFileSync(
    "docker",
    ["exec", "loanrag-postgres-1", "psql", "-U", "loanrag_owner", "-d", "loanrag", "-tAc", sql],
    { encoding: "utf8" },
  ).trim();
}

const KC_DIRECT = "http://localhost:8080/auth";

async function kcAdminToken(): Promise<string> {
  const res = await fetch(`${KC_DIRECT}/realms/master/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "admin-cli",
      username: "admin",
      password: envValue("KEYCLOAK_ADMIN_PASSWORD"),
    }),
  });
  if (!res.ok) throw new Error(`Keycloak admin token: ${res.status}`);
  return ((await res.json()) as { access_token: string }).access_token;
}

/** Keycloak admin REST call over the localhost-only port (never through Caddy, S15). */
export async function kcAdmin(method: string, path: string, body?: unknown): Promise<Response> {
  const token = await kcAdminToken();
  return fetch(`${KC_DIRECT}/admin/realms/loanrag${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** Dev password grant on the loanrag-api client (API-level setup steps only). */
export async function apiToken(username: string): Promise<string> {
  const res = await fetch(`${KC_DIRECT}/realms/loanrag/protocol/openid-connect/token`, {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "loanrag-api",
      username,
      password: envValue("DEV_USER_PASSWORD"),
    }),
  });
  if (!res.ok) throw new Error(`token for ${username}: ${res.status}`);
  return ((await res.json()) as { access_token: string }).access_token;
}

/** Sign in through the real Keycloak page, as a user would. */
export async function signIn(
  page: Page,
  username: string,
  password = envValue("DEV_USER_PASSWORD"),
) {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(password);
  await page.locator("#kc-login").click();
}

export async function signedIn(page: Page, username: string) {
  await signIn(page, username);
  await page.waitForURL(/\/dashboard/);
  await expect(page.getByTestId("user-card")).toBeVisible();
}

export async function ask(page: Page, question: string) {
  const box = page.getByRole("textbox", { name: "Your question" });
  await box.fill(question);
  await box.press("Enter");
}

export async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}
