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
