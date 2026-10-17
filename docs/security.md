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
| S14 | `uv.lock`; `pip-audit` in CI; images pinned (`postgres:16.4`, `keycloak:26.0.7`, `caddy:2.8.4`, `ollama:0.5.7`, `python:3.12.7-slim-bookworm`). |
| S15 | Keycloak admin console on 127.0.0.1:8080 only, not routed by Caddy. Production realm file (`realm-loanrag.prod.json`) has Direct Access Grants disabled. |

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
