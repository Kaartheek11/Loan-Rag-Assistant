import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import { apiToken, envValue, REPO, STATE_FILE, type E2EState } from "./support";

type Candidate = E2EState["deep"];
type Fixtures = Omit<E2EState["fixtures"], "extra"> & {
  eighty: E2EState["fixtures"]["eighty"];
  deepCandidates: Candidate[];
};

/** Checks the stack, builds fresh fixtures, and picks a deep question Alice gets answered. */
export default async function globalSetup() {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0"; // Caddy's local CA, development only
  const health = await fetch("https://localhost/health");
  if (!health.ok) throw new Error("Stack is not running: start docker compose first");

  const out = resolve(REPO, "frontend/.e2e-state/fixtures");
  mkdirSync(out, { recursive: true });
  const raw = execFileSync("uv", ["run", "python", "scripts/e2e_fixtures.py", out], {
    cwd: REPO,
    encoding: "utf8",
    env: { ...process.env, CORPUS_DIR: envValue("CORPUS_DIR") },
  });
  const fx = JSON.parse(raw.trim().split("\n").pop()!) as Fixtures;

  // Use a question the backend really answers with a citation on the expected page, so the
  // UI tests exercise the UI rather than retrieval quality (measured in docs/evaluation.md).
  const token = await apiToken("alice");
  let deep: Candidate | null = null;
  for (const c of fx.deepCandidates) {
    const res = await fetch("https://localhost/v1/query", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ question: c.question }),
    });
    const body = (await res.json()) as {
      status: string;
      citations: { filename: string; page: number }[];
    };
    if (
      body.status === "answered" &&
      body.citations.some((x) => x.filename === c.filename && x.page === c.page)
    ) {
      deep = c;
      break;
    }
  }
  if (!deep) throw new Error("No deep candidate question was answered with the expected citation");

  const state: E2EState = {
    deep,
    fixtures: {
      eighty: fx.eighty,
      textAsPdf: fx.textAsPdf,
      encrypted: fx.encrypted,
      duplicate: fx.duplicate,
      xss: fx.xss,
      extra: [],
    },
  };
  mkdirSync(dirname(STATE_FILE), { recursive: true });
  writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}
