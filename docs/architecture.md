# Architecture

This document describes how the Loan File RAG Assistant is built and records every design
decision that the build specification (`task.md`, v2.1) does not settle, with the reason.
Spec section numbers are given as §N.

## 1. Request path

```
POST /v1/query  (Bearer JWT)
  │
  ├─ deps.get_identity            verify RS256 token → sub; load groups/roles from Postgres (S4)
  ├─ slowapi limiter              30/min per user_id (moving window, in-memory)
  └─ QueryService.answer          (run in the thread pool; all blocking work below)
       ├─ IndexHolder.ensure_current      compare index_state.version, reload under write lock
       ├─ Embedder.embed_query            bge query prefix, L2-normalized
       ├─ Retriever.retrieve              adaptive loop N = 50, 100, … ≤ 800
       │    ├─ FAISS top-N                (IndexIDMap2(IndexFlatIP(384)), ids = chunk_id)
       │    ├─ keyword_search top-N       identifier matches first, then `simple` FTS
       │    ├─ permissions.visible_ids    E1: predicate in Postgres, union of both lists
       │    ├─ rrf(k=60)                  on visible-only lists, tie → lower chunk_id
       │    └─ permissions.load_permitted E1 again, with text → PermittedSet
       ├─ prompts.build_prompt            E2: only objects from this request's PermittedSet
       ├─ LLM adapter                     temperature 0, max 512 tokens
       ├─ citations.validate/render       E3: labels ⊆ S1..Sk, one regeneration, else insufficient
       └─ AuditLogger.write               IDs, status, HMAC(question), latency — no text
```

`GET /v1/documents/{id}/pages/{n}` is E4: one SQL statement joins `documents` (with the
document-level ACL predicate) and `pages`.

## 2. Modules

| Module | Role |
|---|---|
| `app/security/permissions.py` | The only code that reads or writes ACL columns: the chunk and document predicates, `acl_columns()` for ingestion, `replace_document_acl()` for the admin endpoint. Also defines `VisibleChunk` and `PermittedSet`. |
| `app/security/auth.py` | `TokenVerifier` (RS256 only; static PEM for tests or `JwksCache` for Keycloak), `resolve_identity()`, `mint_token()`. |
| `app/security/redaction.py` | structlog JSON config, redaction processor, stdlib bridge. |
| `app/ingestion/*` | extract → clean → chunk → ACL → pipeline (idempotent, crash-safe). |
| `app/retrieval/*` | embedder, FAISS index manager, keyword search, identifiers, fusion, retriever. |
| `app/generation/*` | prompt, adapters (Ollama / OpenAI-compatible hosted / Fake), citations. |
| `app/query_service.py` | Orchestrates §5.3 steps 4–12. |
| `app/admin_ops.py` | ACL replace, soft delete + vector removal, status listing. |
| `app/api/*` | FastAPI app factory, routes, schemas, error mapping. No business logic. |
| `eval/*` | Synthetic corpus, question generation, evaluation runner. |

## 3. Decisions not covered by the spec

Each entry: decision → reason. The short IDs match the decision log in `CLAUDE.md`.

**D1 Repository root.** The repo root is the project folder itself instead of a nested
`loan-rag-assistant/`. The layout below it follows §7.1.

**D2 Python 3.12.** The spec allows 3.11+. 3.12 has wheels for every pinned dependency
(faiss-cpu, torch).

**D3 `.env` loading.** pydantic-settings reads `.env` as a development convenience. Real
environment variables always win, so configuration is still environment-driven (§15).

**D4 Two database roles.** Alembic runs as the owner (`MIGRATION_DATABASE_URL`). The API and
CLI connect as `loanrag_app`, which gets SELECT/INSERT/UPDATE/DELETE on working tables and
only INSERT/SELECT on `audit_log` (migration `0002`). This makes the audit log append-only
at the permission level (S12). Tests connect as the app role too.

**D5 ACL writes through the permission module.** Ingestion copies ACLs onto chunk rows with
`permissions.acl_columns()`, and the admin endpoint calls `permissions.replace_document_acl()`.
That keeps the T18 grep at zero exceptions in query code. `app/db/tables.py` is allow-listed
because it only declares columns.

**D6 macOS OpenMP guard.** The torch and faiss-cpu wheels for macOS each bundle their own
`libomp`. With both loaded, the process segfaults or aborts as soon as either one runs
multi-threaded. `app/__init__.py` sets `OMP_NUM_THREADS=1` on macOS before torch or faiss is
imported. Embedding throughput drops to about 50 chunks/s, which is acceptable for this
corpus. Linux (Docker, CI) uses one shared `libgomp` and is unaffected.

**D7 Loan number in every page heading (synthetic corpus).** The cleaner removes lines that
repeat on more than 60% of pages (§5.4), which strips a per-page header such as
`Loan LN-… | Confidential`. Without another occurrence, the loan number would exist only on
page 1, and near-duplicate borrowers (§11.1) could not be told apart for questions about
later pages. Each generated page therefore starts with a distinct section heading,
`ESCROW ANALYSIS - LOAN LN-2024-000123`, much like form-based loan files.

**D8 Repeat detection masks only page references.** When comparing lines for header/footer
detection, only `page N (of M)` fragments are normalized. Masking every digit made
`SECTION 1` and `SECTION 2` look identical and removed real content.

**D9 Document store copy.** Ingestion copies each PDF to `DOCUMENT_STORE_PATH/<doc_id>.pdf`,
so the original is kept for audit and recovery (§6.7). Encryption at rest is covered in
`docs/security.md`.

**D10 FakeAdapter.** With no script, it answers extractively: it picks the source sentence
that shares the most content words with the question and adds that source's label. It
returns `INSUFFICIENT_CONTEXT` when fewer than two words overlap, or when the question names a
loan number that no source contains. Tests can script it with `responses=[…]` or
`responder=fn`. It records every `(system, user)` call so tests can inspect the prompt.

**D11 Early exit from the adaptive loop.** The loop also stops when both retrievers return
fewer than N results: the corpus is exhausted and doubling N cannot add candidates. This
does not change the results.

**D12 OR-ed full-text query.** `websearch_to_tsquery` ANDs bare words, so a full
natural-language question would match almost nothing. The question is reduced to content
words (stopwords dropped, at most 24 terms) joined with the websearch `or` operator, and
then passed to `websearch_to_tsquery('simple', …)` as §8.3 requires. Identifier matches
are ordered by the number of matching identifiers, then by `ts_rank_cd`.

**D13 No similarity floor.** The spec defines no vector score threshold, and adding one
would change recall. A user who can see any chunks therefore always gets nearest
neighbours, and the model is responsible for answering `INSUFFICIENT_CONTEXT`. "A question
matching nothing" (T9) means no visible candidates; T9 uses a user with zero visibility, so
the adapter is never called.

**D14 Uncited-sentence rule (§8.6).** A sentence with no `[S…]` label counts as an uncited
factual claim if it contains a digit or has at least 8 words. A fragment made only of labels
(`Fact. [S1]`) attaches to the previous sentence, and label lists (`[S1, S2]`) are accepted.
Any answer containing `INSUFFICIENT_CONTEXT` is treated as insufficient.

**D15 Extra prompt escaping.** Besides `<source` / `</source`, lines in document or question
text that equal `SOURCES`, `END SOURCES` or `QUESTION` are rewritten (`[end sources]`), so a
document cannot fake the end of the source block.

**D16 Service modules.** `app/query_service.py` and `app/admin_ops.py` are not in §7.1. They
keep the API layer free of business logic (§5.2).

**D17 Hidden equals missing, including malformed input.** The click-through route parses
`doc_id` and `page` by hand, so a malformed UUID or page number returns the same
`{"error":"not_found"}` as a hidden document (§6.6), not a 422.

**D18 Delete waits for the writer lock.** `DELETE /v1/admin/documents/{id}` waits up to
10 s for the writer lock and then returns 503 `unavailable`. Soft delete and vector
cleanup both run under the lock, as §6.7 requires.

**D19 Error bodies.** A 422 is `{"error":"validation_error"}` with no field echo, because
inputs can contain document text. A 500 is `{"error":"internal_error"}`. Exceptions are
logged by type only (S13).

**D20 E2 by object identity.** `PermittedSet.__contains__` checks that the chunk is the
exact object returned by E1 for the current `request_id` (`is`, not equality). A chunk
rebuilt from the same ID, or carried over from another request, raises
`PermissionViolation` in the prompt builder and in citation rendering.

**D21 One audit row per query, including failures.** `status='error'` is written when the
index or the LLM is unavailable (503). Requests rejected before identity is resolved (401,
422, 429) get no audit row, because no query ran.

**D22 Rate-limit key.** The limiter runs after authentication and keys on `user_id`.
Unauthenticated requests are rejected with 401 before they count against any limit.

**D23 Realm files generated from code.** `scripts/make_realm.py` writes the dev realm
(Direct Access Grants on, passwords from `${DEV_USER_PASSWORD}` substituted by Keycloak at
import) and a prod realm (grants off, no credentials) from the same user list as
`users_seed.yaml`. `seed-users` still checks that the two files list the same IDs (§6.1.1).
Keycloak user IDs are the readable strings `u_alice` …, used as `sub` and `users.user_id`;
Keycloak usernames drop the prefix (`alice`). Each user gets Keycloak's `default-roles-loanrag`
so Keycloak's own account console works. Those token roles are ignored by the API.

**D24 Embedding device.** The embedder runs on CPU, so incremental and rebuilt indexes are
bit-identical, which the "rebuild equals incremental" test requires.

## 4. Index consistency (summary of §5.5 as implemented)

- Writer lock: `pg_try_advisory_lock(0x4C4F414E524147)` held on a dedicated connection by
  ingest, rebuild-index and delete.
- Atomic save: `faiss.write_index` → temp file in the same directory → `fsync` → `os.replace`.
- Ingest order: rows `pending` → vectors in memory → atomic save → one transaction marks the
  documents `indexed` and bumps `index_state.version`.
- Reader: `IndexHolder` keeps `(index, version)` behind a writer-preferring readers/writer
  lock. Each query reads `version` and reloads if it changed.
- Startup refuses to run if `index_state.embedding_model ≠ EMBEDDING_MODEL`, or if
  `version > 0` and the index file is missing (`rebuild-index` fixes both).

## 5. Version 2 (extension.md): chat UI, admin uploads, large loan files

`task.md` is unchanged; `extension.md` is the change document. New pieces:

```
browser ─► Caddy (https://localhost, single origin, CSP)
             ├─ /            built React app (frontend/, Dockerfile target `web`)
             ├─ /auth/*      Keycloak (KC_HTTP_RELATIVE_PATH=/auth), /auth/admin/* → 404
             ├─ /v1/*        api (1 MB body limit)
             └─ POST /v1/admin/uploads  api (55 MB body limit)
api ──► Postgres ◄── worker (`python -m app.worker`, same image) ──► shared FAISS file in /data
```

| Area | Modules |
|---|---|
| Large files (§17) | `eval/synth/generate_corpus.py` (v2), `app/ingestion/context.py` (fields + prefix), `app/ingestion/progress.py`, pipeline steps `prepare_document` / `write_content` / `embed_into` |
| Backend for the UI | `app/api/routes_me.py` (BE-1, BE-2), excerpts in `query_service.py` (BE-3), `SessionStartRecorder` (BE-4) |
| Uploads | `app/uploads.py` (BE-8, BE-10, BE-11), `app/worker.py` (BE-9), `app/admin_ops.py` (BE-12, BE-13), migration `0005` |
| Frontend | `frontend/src/*` per extension.md §7.1; tests in `frontend/tests/{unit,component,e2e}` |

### Decisions not covered by extension.md

**D30 Section headings in the v2 corpus.** Each section's first page starts with an all-caps
title; table headers are sentence case ("Posting date"), so they are never mistaken for
headings. The per-page `Loan … | Page X of Y` header repeats on every page and is removed by
cleaning, as in real files. Version 1's workaround (D7, a loan number in every heading) is gone:
the context prefix (§17.2) now solves the problem it addressed.

**D31 Heading detection ignores table rows.** A line counts as a heading only if at least half
of its non-space characters are letters, and a title-case line must contain no digits. Without
this, a row such as `2023-04-01 $1,200.00 PAID` became the "section" of every later chunk.

**D32 Readable section labels.** All-caps headings are stored in sentence case ("Bank
statements", as in §17.2's example); words containing digits keep their case, so identifiers
survive.

**D33 Document fields come from raw text.** The loan number and borrower name are read from
the first 5 pages *before* cleaning, so a loan number that only appears in the page header
still counts. A borrower name ends at the first period ("Borrower name: John Stephens. Loan…").

**D34 Prefix token budget.** Body size = 400 − (tokens of the document prefix + `" | Section: "`
+ 20 tokens for the section name). `token_count` stores the tokens of prefix + newline + text,
the string that is embedded.

**D35 Parallel preparation.** Extraction, OCR, cleaning and chunking run in a spawned process
pool (`ingest --workers N`, default up to 6). Workers load only the tokenizer (same one the
embedder uses), run single-threaded (Tesseract and HF tokenizers would otherwise oversubscribe
the CPU ~10×), and return results in input order; embedding and DB writes stay in the main
process. A test checks parallel output equals sequential output.

**D36 `EMBEDDING_DEVICE`.** `cpu` by default (deterministic; tests and containers). Bulk
ingestion on Apple silicon can use `mps`: 2.5× faster than CPU on this machine, vectors equal
to CPU within 1.3e-7. Torch inside the Linux Docker VM on macOS embeds at only ~12 chunks/s, so
the 75K-page corpus was ingested on the host and the index shared with the containers.

**D37 Data outside synced folders.** `CORPUS_DIR` and `APP_DATA_DIR` let Compose mount the
corpus and `/data` (index, document store) from a host folder. On macOS, iCloud-evicted files
cannot be read through the Docker mount (EDEADLK), so they live in `~/loanrag-data`.

**D38 Audit event shapes.** `audit_log.event` is `query`, `session_start`, `upload`,
`acl_change` or `delete`. Query-only columns became nullable, with CHECK constraints: query rows
must have every v1 field; `session_start` rows have no question hash and empty chunk arrays; admin
rows also carry `doc_id`.

**D39 Once per token.** `session_start` is deduplicated by `jti` (or a hash of the token when a
token has no `jti`) in memory for 20 minutes, longer than the 15-minute token lifetime.

**D40 Page endpoint additions.** It also returns `page_count` (for "Page 63 of 87" and "Go to
page") and `section` (the page's first chunk's section, as §8.7 asks). Both are returned only
after the access check.

**D41 `X-Request-Id`.** Every `/v1` response carries the request ID (the same ID as the query's
audit row), so error cards can hold it as a hidden attribute (UI-F12) while error bodies stay
`{"error": …}`.

**D42 SHA-256 unique among live documents only** (migration `0005`), so the content of a deleted
file can be uploaded again (§6.1 rule 7 excludes soft-deleted documents). The CLI still treats a
deleted match as indexed and skips it: CLI re-runs never resurrect a deleted document.

**D43 The CLI does not touch uploads.** A document with an upload job belongs to the worker; the
CLI skips it.

**D44 Admin list shape.** `GET /v1/admin/documents` returns `{items, next_cursor, summary}`.
Each item has the BE-12 fields plus `failure_reason` and `upload` (job status and pages done,
for "Processing 40%"); `summary` has total / processing / failed counts for the summary chips.
Still metadata only.

**D45 ACL edits follow the upload rule.** `PUT …/acl` rejects an empty ACL and unknown groups
or users with 422 `invalid_access`, like uploads (UI-F19's "same at-least-one rule"), and
writes an `acl_change` audit event in the same transaction.

**D46 Upload error codes.** 413 `file_too_large`; zero or several files → 422
`validation_error`; 409 `duplicate` with `existing_filename` (the only error body with an
extra field, as §6.1 specifies); retrying a job that has not failed → 409 `conflict`.

**D47 Worker details.** The writer lock is held for the whole batch (spec step 2), with
heartbeats while waiting for it. A document deleted while its job was queued is marked failed
with no reason. A failed job's partial pages, chunks and vectors are removed; the stored PDF is
kept for retry. `pages_done` follows extraction (every 5 pages), and embedding happens at
"page N of N".

**D48 No browser storage at all.** Even the theme choice is kept in memory (it resets to
"system" on reload). ESLint bans `localStorage`, `sessionStorage`, `indexedDB`,
`dangerouslySetInnerHTML` and `innerHTML` in app code.

**D49 401 handling.** A 401 from `/v1/me` after a successful token refresh means "not
provisioned or disabled": show the no-access screen (§5's flow). This avoids a sign-in loop that
§10.2's generic rule would cause. Other 401s after one refresh-and-retry end the session:
caches are cleared and `keycloak.login()` returns to `/dashboard?session=ended` (a flag, never a
token), which shows the toast.

**D50 Back-forward cache.** A page restored from bfcache is reloaded, so the back button after
sign-out can never show the old conversation from memory (UI-T6).

**D51 Contrast fixes (§9.2 "adjust values if needed").** Light coral #D9584A → #C24336 (error
text on white was 3.9:1). New tokens: `marigold-line` (#B87C0B light) for chip outlines and the
drop zone (marigold on white was 1.9:1, below 3:1 for UI components); `on-marigold` (dark ink,
both themes) for chip text (light ink on dark-theme marigold failed); `on-coral`, `on-spruce`,
`on-spruce-deep(-muted)` and `line`. `frontend/tests/unit/contrast.test.ts` checks every pair in
both themes.

**D52 Keycloak admin console.** Reachable only on the localhost-bound port
(`KC_HOSTNAME_ADMIN=http://localhost:8080/auth`); Caddy returns 404 for `/auth/admin/*` (S15).

**D53 Production web client origin.** The production realm reads `loanrag-web`'s exact
redirect URI and web origin from `${LOANRAG_PUBLIC_ORIGIN}` at import time.

**D54 Upload temp files** stream into `DOCUMENT_STORE_PATH/.incoming` (same filesystem, so the
final move to `{doc_id}.pdf` is a rename) and are removed whatever the outcome.

**D55 E2E fixtures.** The Playwright global setup builds unique fixture PDFs each run
(`scripts/e2e_fixtures.py`, generator flags `--start-index` / `--no-config`), so reruns never
collide as duplicates. It also picks a deep, underwriting-only question that the API really
answers with the expected citation, so UI tests test the UI rather than retrieval quality.

**D56 Silent-SSO page framing.** §11.2's CSP has `frame-ancestors 'none'`, but keycloak-js loads
`/silent-check-sso.html` in a same-origin iframe for `check-sso` (the reason §11.2 includes
`frame-src 'self'`). With `'none'` on that page the browser refuses to render it, and sign-in
never initializes (found by the E2E suite: the Sign in button stayed disabled). Caddy therefore
serves `/silent-check-sso.html` and its script with `frame-ancestors 'self'`. Every other app
route keeps `frame-ancestors 'none'`.

**D57 Admin console sign-in stays on 127.0.0.1:8080.** `KC_HOSTNAME=https://localhost/auth` applies
to every realm, so the admin console (on `KC_HOSTNAME_ADMIN`, port 8080) sent its sign-in to the
master realm at `https://localhost/auth/realms/master`, through Caddy. In a browser that does not
trust Caddy's local certificate, opening http://localhost:8080 ended on a certificate error, and the
master-realm sign-in page was reachable on the public origin (against S15). The one-shot
`keycloak-init` Compose service sets the master realm's `frontendUrl` to
`http://localhost:8080/auth` after every Keycloak start (`start-dev` keeps no database), and Caddy
returns 404 for `/auth/realms/master/*` as well as `/auth/admin/*`. The `loanrag` realm keeps the
issuer `https://localhost/auth/realms/loanrag`.

**D58 Insufficient-answer wording mentions access (owner decision, 2026-10-17).** The owner asked
for the "no answer" message to point users to their admin. Saying "you are not authorized" only
when a hidden file matched would confirm that the file exists, breaking "hidden equals missing"
(task.md §6.6, T9, UI-T3), so that option was declined. Instead, every insufficient answer
(nothing visible matched, the model found no answer, citations failed twice) gets the same fixed
text. The API answer is now "I couldn't find information to answer that in the files you have
access to." (was task.md §9.2's "…in the documents available to you."). The card's hint is now
"Try rephrasing, or include the loan number. If you think you should have access to this loan
file, please check with your admin." This deliberately departs from UI-F11, which forbids access
hints. Because the text is identical whether the loan is hidden, nonexistent or simply lacks the
fact, it reveals nothing about any particular file. The cost is that users with no matching file
are also pointed to their admin.
