# CLAUDE.md — Loan File RAG Assistant

Working notes for Claude (and humans) on this repo. The build spec is [task.md](task.md) (v2.1).
Requirement IDs (G/F/S/T) come from the spec. Design decisions not covered by the spec are
recorded with reasons in [docs/architecture.md](docs/architecture.md) (spec §0 requires this).

## Hard rules (from spec §6, §16 — never break)

- Permission SQL (`acl_groups` / `acl_users`) lives ONLY in `app/security/permissions.py`
  (plus schema in `app/db/tables.py` and migrations). `scripts/check_permission_sql.py` enforces it (T18).
- Groups/roles come from Postgres on every request, never from the JWT or the request body.
- No admin read bypass. Default deny: empty ACL = nobody.
- Never log question text, answer text, chunk text, loan numbers, or secrets.
- No caching of retrieval results across requests.
- Postgres is the source of truth; FAISS is derived and rebuildable.

## Commands

```bash
uv sync                                   # install (Python 3.12, pinned via .python-version)
uv run pytest                             # all tests (needs Docker for testcontainers Postgres)
uv run pytest tests/security              # mandatory security suite (T1–T18)
uv run ruff check . && uv run mypy app    # lint + types (strict on app/security)
uv run python scripts/check_permission_sql.py   # T18 CI check
uv run python -m app.cli --help           # ingest, rebuild-index, mint-token, seed-users, eval, ...
```

## Environment notes (this machine)

- macOS, Apple M5, 16 GB RAM. Docker Desktop (needs to be started: `open -a Docker`).
- Installed via Homebrew for this project: `uv`, `tesseract`, `poppler`.
- System Python is 3.14; project uses uv-managed 3.12 (faiss/torch wheels).
- Ollama installed via Homebrew, `llama3.1:8b` pulled; run `ollama serve` (127.0.0.1:11434).
- Docker Desktop got Desktop-folder permission on 2026-10-17 (before that, bind mounts from
  `~/Desktop` hung container start and wedged the engine; fix was `docker desktop restart`).
- Running stack (macOS): `docker compose -f docker/docker-compose.yml -f docker/docker-compose.host-ollama.yml --env-file .env up -d`
  → caddy (https://localhost), api, postgres (127.0.0.1:5432, volume `loanrag_pgdata`, corpus
  already ingested), keycloak (127.0.0.1:8080). LLM = host `ollama serve` (Metal) via
  `host.docker.internal:11434`. API index lives in the `loanrag_appdata` volume (version 2).
- `.env` exists locally (git-ignored, random secrets). Never print it.

## Progress (spec §13 phases)

Legend: [x] done · [~] partial · [ ] not started

- [x] Phase 0 — setup: structure, pyproject/uv, .env.example, .gitignore, compose, CI
      (Dockerfile/Caddyfile finished in Phase 12)
- [x] Phase 1 — synthetic corpus (`eval/synth/generate_corpus.py`; full corpus in `./corpus`,
      seed 42: 1000 docs, 50 scanned, 10 injection, 10 empty-ACL, 4983 facts) — tests/unit/test_synth_corpus.py
- [x] Phase 2 — migrations 0001 (schema) + 0002 (app-role grants, audit_log INSERT/SELECT only),
      `app/db/seed.py` (seed-users w/ realm check) — tests/integration/test_db.py
- [x] Phase 3 — ingestion (`app/ingestion/*`) — tests/unit/test_chunk_clean.py,
      tests/integration/test_ingestion_index.py
- [x] Phase 4 — embeddings + index (`app/retrieval/embedder.py`, `index_manager.py`,
      `rebuild_index` in pipeline.py) — same integration test file
- [x] Phase 5 — auth + identity: `app/security/auth.py` (RS256-only, JWKS cache w/ throttled
      refetch), realm files via `scripts/make_realm.py`, `mint-token`/`gen-test-keys` CLI.
      T6/T7/T7b pass. Manual check done 2026-10-17: curl password grant against Keycloak
      26.0.7 → token `sub=u_alice`, `aud=loanrag-api`, RS256 + kid, 900 s → API (JWKS mode)
      answered for carol/bob, tampered token → 401, admin endpoints OK.
- [x] Phase 6 — `app/security/permissions.py` (+ `PermittedSet` object-identity check for E2/E3),
      `scripts/check_permission_sql.py`. T15–T18 pass.
- [x] Phase 7 — `keyword_search.py`, `fusion.py`, `retriever.py`. T1, T2 + retrieval functional tests pass.
- [x] Phase 8 — `prompts.py`, `citations.py`, `llm_adapter.py` (Ollama/Hosted/Fake). T9–T11 pass.
- [x] Phase 9 — API (`app/api/*`, `app/query_service.py`, `app/admin_ops.py`), slowapi per-user
      limit. T3, T4, T5, T8, T13, T14 pass.
- [x] Phase 10 — `app/audit/audit_log.py` (HMAC), `app/security/redaction.py` (structlog JSON +
      stdlib bridge). T12 passes.
- [~] Phase 11 — `eval/synth/generate_questions.py` (160 answerable / 25 unanswerable /
      35 probes, 30-question hand review done), `eval/run_eval.py` (all §11.3 metrics,
      independent ACL oracle). Full corpus ingested (7,654 chunks, 418 OCR pages).
      Fake + Ollama llama3.1:8b runs done → docs/evaluation.md. Hybrid R@10 0.988,
      Ollama accuracy 0.894, refusal 1.00, **0 permission leaks**, p50/p95 5.7/8.0 s.
      **Pending: hosted (Groq/Gemini) run — needs API key + S8 terms.**
- [~] Phase 12 — Dockerfile (CPU torch on Linux, model baked in, non-root; built + smoke-tested),
      Caddyfile, compose, docs/architecture.md, docs/security.md, docs/evaluation.md, README,
      `scripts/demo.sh` (verified live: carol answered, bob insufficient).
      Full compose stack verified 2026-10-17 (Caddy TLS, Keycloak tokens, Ollama on host,
      HTTP→HTTPS 301, Keycloak admin 404 via Caddy, API/DB/KC not publicly exposed).
      **Pending: the 2-minute demo recording (human).**

Test status (2026-10-17): `uv run pytest` → 73 passed (~100 s); ruff + format clean; mypy clean;
permission-SQL check passes; Docker image builds.

## Known findings to act on (from evaluation)

- RRF (k=60, equal weights) demotes identifier-matched chunks missing from the vector top-N;
  hybrid MRR 0.59 < keyword-only 0.80; 12/17 Ollama misses are this. Candidate fix (needs a
  spec update first): weight the keyword list when the question has an identifier.
- Ollama answered one probe from a *different visible loan* with the same condition text
  (no leak, but ignores the loan-number mismatch).
- Timing side channel measured: zero-visibility users run 5 rounds (186 ms vs 51 ms median).

## Decision log (short form — details in docs/architecture.md)

| # | Decision | Why |
|---|---|---|
| D1 | Repo root is this folder (`loan_rag/`), not a nested `loan-rag-assistant/`. | Avoid a pointless extra level; layout under it matches spec §7.1. |
| D2 | Python 3.12 via uv (spec says 3.11+). | faiss-cpu / torch wheels; system Python 3.14 too new. |
| D3 | `.env` is read by pydantic-settings as a dev convenience; real env vars win. | Spec §15 says env-only config; `.env` is just a way to set env vars locally. |
| D4 | Two DB roles: owner runs migrations (`MIGRATION_DATABASE_URL`), app role (`DATABASE_URL`) has INSERT/SELECT only on audit_log. | S12 append-only audit at the permission level. |
| D5 | ACL columns are written/read only via `app/security/permissions.py` helpers (`acl_columns`, `replace_document_acl`, predicates). `app/db/tables.py` is allow-listed in the T18 check (schema, not SQL). | T18 grep must pass with zero exceptions in query code. |
| D6 | macOS only: `OMP_NUM_THREADS=1` set in `app/__init__.py`; torch stays single-threaded. | torch + faiss-cpu wheels bundle separate libomp copies → segfault when either goes multi-threaded. Linux is unaffected. Import `app` before torch/faiss in any script. |
| D7 | Synthetic corpus: every page has a section heading line `SECTION - LOAN LN-…`; a repeated header/footer (`Page n of m`) is stripped by the cleaner. | Header lines repeat on >60% of pages and are removed (spec §5.4), so the loan number would otherwise exist only on page 1, making near-duplicate disambiguation impossible. |
| D8 | Cleaner masks only `page N of M` fragments when detecting repeated lines (not all digits). | Masking all digits merged distinct lines like `SECTION 1`/`SECTION 2`. |
| D9 | Ingestion copies each PDF to `DOCUMENT_STORE_PATH/<doc_id>.pdf`. | Spec keeps originals for audit/recovery (§6.7); store path is configured (§15). |
| D11 | Adaptive loop also stops when both retrievers return fewer than N results (corpus exhausted). | Growing N cannot add candidates; same results, fewer rounds. |
| D12 | Full-text query = content words joined with websearch `or` (stopwords dropped), still via `websearch_to_tsquery('simple', …)`. | Bare websearch syntax ANDs every word of a natural-language question → almost no matches. |
| D13 | No vector similarity floor. "Matches nothing" (T9) = no *visible* candidates. A user who can see some documents always gets nearest neighbours, and the model must answer INSUFFICIENT_CONTEXT. | Spec defines no threshold; a floor would be an unrequested feature that changes recall. T9 uses a user with zero visibility. |
| D14 | Uncited-sentence heuristic: a sentence without a label is a factual claim if it contains a digit or has ≥ 8 words; label-only fragments attach to the previous sentence; `[S1, S2]` lists accepted. | §8.6 says "factual sentences with no label" without defining "factual". |
| D15 | Prompt escaping also neutralizes lines that equal `SOURCES` / `END SOURCES` / `QUESTION` in document or question text. | Same threat as fake `<source>` tags (§8.5). |
| D16 | Query orchestration lives in `app/query_service.py`; admin mutations in `app/admin_ops.py` (not in spec's file list). | Keeps API layer free of business logic (§5.2). |
| D17 | Malformed doc IDs / page numbers on the click-through route return the same 404 as hidden docs (path params parsed by hand). | §6.6 "hidden equals missing". |
| D18 | `DELETE` waits up to 10 s for the writer lock, then 503 `unavailable`. | Spec requires the lock; a running ingestion shouldn't make deletes fail instantly. |
| D19 | Errors: 422 body is `{"error":"validation_error"}` (no field echo), 500 is `{"error":"internal_error"}`, exceptions logged by type only. | §9 error shape + S13 (inputs/tracebacks can contain document text). |
| D20–D24 | E2 by object identity; audit rows for 503s; rate-limit key after auth; realm files generated from code; CPU embeddings. | See docs/architecture.md §3. |
| D25 | Linux builds take torch from the PyTorch CPU index (`tool.uv.sources`); model baked into the image (`HF_HOME=/opt/hf`, offline). | Avoid ~3 GB CUDA wheels; non-root runtime can't write a cache; no download at startup. |
| D27 | macOS compose override `docker/docker-compose.host-ollama.yml`: API uses host Ollama, Ollama container moved to a profile. | Docker on macOS has no GPU; 8B on CPU inside the VM is too slow. |
| D28 | Realm users get `default-roles-loanrag` (Keycloak account-console roles); Keycloak usernames are `alice`… (IDs `u_alice`…). Regenerate with `uv run python scripts/make_realm.py`, then `up -d --force-recreate keycloak`. | Without it the account page returned 401. API still ignores token roles. |
| D26 | Eval throttle (`EVAL_REQUESTS_PER_MINUTE`) applies only to `--provider hosted`. | It exists for free-tier limits (§8.7); throttling local Ollama only slows the run. |
| D10 | Fake LLM adapter is extractive (best-overlap sentence + label, or INSUFFICIENT_CONTEXT) and scriptable (`responses=` / `responder=`). | Deterministic CI and offline retrieval eval. |

## Open items / blockers

- [ ] Hosted LLM run (Groq/Gemini) needs the user's API key + recording provider terms in
      docs/security.md §3 (S8) before first use.
- [ ] 2-minute demo recording (Phase 12) is a human task.
- [ ] Git: repo initialised, nothing committed yet (user hasn't asked for commits).
