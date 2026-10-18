# Security

## 1. What we protect and from whom

**Assets:** loan document text (pages and chunks), the fact that a given document exists,
ACLs, audit trail, secrets (HMAC key, DB passwords, provider API keys).

**Actors considered**

| Actor | Capability | Goal |
|---|---|---|
| Authenticated user | Valid token, crafted questions and request bodies | Read documents outside their ACL, learn that hidden documents exist |
| Document author | Controls text inside an ingested PDF | Prompt injection: widen access, exfiltrate other documents, distort answers |
| Network attacker | Forged/replayed/altered tokens | Impersonate a user |
| Admin | `admin` role | Should manage, not read; must not get a silent read bypass |
| Operator with log access | Reads application logs | Should not see document content, questions, or secrets |

## 2. Controls

### Core rule: permissions are enforced in code, never by the model

The model only receives chunks that passed the Postgres permission predicate for this
request. Four enforcement points, none relied on alone (§6.4):

| # | Where | Implementation | Tests |
|---|---|---|---|
| E1 | Retrieval | `permissions.visible_ids` on the union of FAISS + keyword candidates every loop round; `permissions.load_permitted` re-checks the final top-k when loading text | T1, T2, T15, T16, T17 |
| E2 | Prompt | `build_prompt` calls `PermittedSet.require()` for every chunk: it must be the *same object* E1 returned for this `request_id` | `test_prompt_labels_escaping_and_e2` |
| E3 | Citations | Labels must be in `S1..Sk`; each maps through `PermittedSet.require()`; invalid → one regeneration → insufficient | T11 |
| E4 | Click-through | One SQL statement with the document-level predicate joined to `pages` | T8, T15 |

The predicate (`indexed` AND not soft-deleted AND (group overlap OR explicit user)) is
written once, in `app/security/permissions.py`. `scripts/check_permission_sql.py` fails CI if
`acl_groups`/`acl_users` appear anywhere else in `app/` or `eval/` (T18).

### Requirement mapping

| ID | Control |
|---|---|
| S1 | Secrets only from env; `.env` git-ignored; `.env.example` has placeholders; gitleaks in CI. Dev Keycloak passwords use `${DEV_USER_PASSWORD}` placeholders in the realm file. |
| S2 | `Redactor` replaces literal values of known secrets (`Settings.secret_values()`: HMAC key, hosted API key, Keycloak admin password, DB passwords parsed from URLs). |
| S3 | Caddy terminates TLS and redirects HTTP → HTTPS. Postgres, Keycloak and Ollama bind to 127.0.0.1 only. |
| S4 | Groups/roles loaded from `user_groups`/`user_roles` on every request (`resolve_identity`). T3. |
| S5 | `make_engine` sets `sslmode=require` for non-local hosts in production. |
| S6 | See §4 (known gap in development). |
| S7 | Only the top-k permitted chunks are placed in the prompt. Full documents and page text are never sent. |
| S8 | See §3. |
| S9 | All request models `extra="forbid"`; question 1–1000 chars; `top_k` 1–10. T6, T13. |
| S10 | slowapi, 30/min per `user_id`, moving window. T14. |
| S11 | Source blocks with escaped `<source`/`</source` and block markers; system prompt rule 4. T10. |
| S12 | App DB role has INSERT/SELECT only on `audit_log` (`tests/integration/test_db.py`). |
| S13 | Content keys dropped, loan numbers masked in every log record (structlog + stdlib), exceptions logged by type only. T12. |
| S14 | `uv.lock` and `frontend/package-lock.json`; `pip-audit` and `npm audit` in CI; images pinned (`postgres:16.4`, `keycloak:26.0.7`, `caddy:2.8.4`, `ollama:0.5.7`, `python:3.12.7-slim-bookworm`, `node:22.11.0-bookworm-slim`). |
| S15 | Keycloak admin console and master-realm sign-in on 127.0.0.1:8080 only; Caddy returns 404 for `/auth/admin/*` and `/auth/realms/master/*` (architecture D57). Production realm file (`realm-loanrag.prod.json`) has Direct Access Grants disabled. |

### Authentication details

- RS256 is the only accepted algorithm; the header `alg` is checked before key lookup and
  PyJWT is called with `algorithms=["RS256"]`. HS256 tokens signed with the public key
  (algorithm confusion) and `alg: none` are rejected (T7).
- `exp`, `iss`, `aud`, `sub` are required. Keycloak tokens carry `loanrag-api` in `aud`
  through the client's audience mapper.
- JWKS keys are cached by `kid`; an unknown `kid` triggers at most one refetch per minute
  (key rotation without restart, and no refetch amplification from garbage `kid`s).
- A valid token for a user not in `users`, or a disabled user, gets 401. Group/role claims
  inside tokens are ignored (T7b).
- Production refuses to start with a static public key, with the fake LLM, or with any
  secret still set to a `CHANGE_ME` placeholder.

### Information leakage

- Hidden equals missing: unreadable, unknown, malformed and out-of-range page requests
  return byte-identical `{"error":"not_found"}` (T8).
- A question whose only matches are hidden returns the same body as a question matching
  nothing, and the LLM is not called when no chunk is visible (T9).
- The insufficient wording (API answer and UI hint) is one fixed text for every case. It
  suggests checking with an admin, but never depends on whether a hidden file matched
  (architecture D58).
- Error bodies are fixed codes only; 422 does not echo fields.
- `/health` returns no document data or per-group counts.

## 3. Hosted LLM providers (S8)

The corpus is **synthetic**. That is the only reason free-tier hosted inference is
acceptable here. **Real loan data would require a paid tier with contractual no-training
and limited-retention terms, or the local Ollama model.**

Before the first hosted run, the operator must read the provider's current terms and fill
in this table (terms change often; do not rely on remembered terms):

| Provider | Tier | Data used for training? | Retention | Terms URL | Checked on | By |
|---|---|---|---|---|---|---|
| Groq | free | _to record_ | _to record_ | _to record_ | _pending_ | |
| Google Gemini | free | _to record_ (free-tier traffic falls under Google's data-use terms, which can include use for product improvement) | _to record_ | _to record_ | _pending_ | |

Status: **no hosted run has been made yet** (no API key configured). The adapter fails
closed: if the provider errors or rate limits run out, the client gets 503. There is no
silent fallback to Ollama.

## 4. Known limitations

1. **Timing reveals visibility volume (accepted, §6.6).** The adaptive over-fetch loop runs
   more rounds (N = 50 → 800) for users who can see little of the corpus, so latency can
   reveal roughly how much of the corpus is visible to the caller. It does not reveal which
   documents match. Measured on the 1,000-document corpus: users with zero visibility run 5
   rounds, with a median of 186 ms versus 51 ms for a 1-round `insufficient` answer
   (`docs/evaluation.md`). Mitigation if needed: always run a fixed number of rounds, at a
   latency cost for broad-access users.
2. **Prompt injection can distort answers.** Escaping and the "sources are data" rule stop
   fake source boundaries, and injection cannot widen access because the model controls
   nothing about access (T10). A malicious document can still steer the wording of an
   answer drawn from that same document. Citations let users check the source page.
3. **Encryption at rest (S6) — development gap.** In development, `./corpus` and
   `DOCUMENT_STORE_PATH` are plain directories on the developer's disk (macOS FileVault is
   the only protection). Production must place `DOCUMENT_STORE_PATH` and the Postgres volume
   on encrypted storage (e.g. LUKS/dm-crypt volume or an encrypted cloud disk).
4. **In-memory rate limiting.** Limits reset on restart and are per process, as the spec
   allows for a single API process.
5. **Uncited-sentence heuristic.** "Factual sentence" is approximated (digit or ≥ 8 words,
   see architecture D14). Short uncited sentences without digits are allowed.
6. **Soft delete keeps text in Postgres.** Deleted documents are invisible but their rows
   remain until a purge job (out of scope) removes them.
7. **macOS development builds run single-threaded OpenMP** (architecture D6). Not a
   security issue; noted because it affects latency figures measured on macOS.

## 5. Version 2: browser app and admin uploads (extension.md)

**The browser is not a security boundary.** Every permission decision stays in the backend
(extension.md §2). The app shows exactly what the API returns: nothing is fetched and then
hidden, group and role names are display-only, and every call carries the user's token and is
checked by the same enforcement points as in §2 above.

| ID | Control | Where |
|---|---|---|
| UI-S1 | No permission logic in the UI. The admin link is shown when `/v1/me` lists the `admin` role, and that is cosmetic only: every admin endpoint returns 403 to non-admins (`tests/security/test_uploads.py`). | `Sidebar.tsx`, `routes_admin.py` |
| UI-S2 | API text is rendered as React text nodes only. ESLint bans `dangerouslySetInnerHTML` and `innerHTML`. Highlights split text into elements. UI-T13 shows markup as plain text, with no `<img>` and no dialog. | `eslint.config.js`, `SourcePanel.tsx` |
| UI-S3 | Tokens live only in keycloak-js memory: never in storage, cookies or URLs. UI-T7 checks storage after a full session. | `auth/keycloak.ts` |
| UI-S4 | Questions, answers, excerpts and page text live only in React state and the TanStack Query cache, both cleared on sign-out. ESLint bans storage globals in app code. | `ChatContext.tsx`, `AuthProvider.tsx` |
| UI-S5 | The bundle contains only the public client ID, realm and relative URLs (`VITE_*`). | `config/env.ts` |
| UI-S6 | `loanrag-web`: public client, Standard Flow only, PKCE S256 required, Direct Access Grants off, exact redirect URI and web origin. `scripts/check_web_login.py` confirms that requests without PKCE, foreign redirects and password grants are refused. | `scripts/make_realm.py` |
| UI-S7 | Strict CSP from Caddy on app routes (`script-src 'self'`, `frame-ancestors 'none'`, and so on), plus `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and `Permissions-Policy`. The silent-SSO page loads an external script, so no inline script is needed. | `docker/Caddyfile`, `public/silent-check-sso.*` |
| UI-S8 | Production builds drop `console.*` and `debugger`. | `vite.config.ts` |
| UI-S9 | `Cache-Control: no-store` on every `/v1/*` response, errors included. | `app/api/main.py` |

**Single origin.** The app, `/v1` and `/auth` are served by Caddy from https://localhost, so no
CORS configuration exists. `/auth/admin/*` and `/auth/realms/master/*` return 404, and the Keycloak admin console is
reachable only on 127.0.0.1:8080 (S15). The token issuer is fixed to
`https://localhost/auth/realms/loanrag`; the API fetches keys over the internal network.

**Session behaviour.** `/v1/me` resolves access from Postgres right after sign-in, and again on
window focus and every 5 minutes. That only keeps the display current: the per-request checks
are what make a revocation take effect on the very next question (UI-T4). A page restored from
the back-forward cache is reloaded, so the back button after sign-out never shows the previous
conversation (UI-T6).

**Upload hardening (§6.1).**
- Validated before anything is stored permanently: admin role; one file of at most 50 MB
  (Caddy caps the route at 55 MB, the API checks again); `%PDF-` magic bytes and pypdf can open
  it; not encrypted; 1–500 pages; at least one existing group or user; not a duplicate of a live
  document; 120 uploads per hour per admin.
- The upload filename is never used as a path. Files are stored as `{doc_id}.pdf`; the display
  name has path parts and control characters removed and is trimmed to 200 characters.
- An uploaded document is invisible to everyone until the worker marks it `indexed`. The
  uploading admin can read it only if they are in its ACL (UI-T19).
- `upload`, `acl_change` and `delete` are audited with the admin's ID, the document ID and a
  timestamp, in the same transaction as the change. No content is stored in these rows.

**Known limitations added by version 2.**
1. Uploaded PDFs are parsed by pdfplumber, pypdf and Tesseract in the worker. A malicious
   PDF can still exercise parser bugs. The worker runs as a non-root user in its own container,
   with no network need beyond Postgres. Stronger isolation (seccomp, a separate sandbox) is
   future work.
2. The admin's people picker lists users' IDs and display names. That is admin-only metadata,
   not document content.
3. Keycloak's own httpOnly session cookies (`/auth/realms/loanrag/…`) exist so silent SSO can
   work. They are the identity provider's session, not app tokens, and the app sets no cookies of
   its own (checked in UI-T7).
4. The timing note in §4.1 applies to the UI as well. The "thinking" status lines are cosmetic
   timers that never reflect retrieval rounds (UI-F8).
5. **Accepted dependency advisories (checked 2026-10-17).** `npm audit` reports two moderate
   advisories for React Router 6 (GHSA-wrjc-x8rr-h8h6, an open redirect through backslashes in
   `<Link>`/`useNavigate` paths, and GHSA-337j-9hxr-rhxg, in SSR hydration). Neither applies:
   every navigation target in the app is a constant path (`/`, `/dashboard`, `/admin/files`,
   `/no-access`), and the app has no server-side rendering. The only fix is React Router 7, a
   breaking upgrade. CI fails on advisories rated high or above (`npm audit --omit=dev
   --audit-level=high`).
