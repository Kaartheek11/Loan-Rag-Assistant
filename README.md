# Loan File RAG Assistant

Ask natural-language questions across a corpus of loan PDFs and get answers grounded in
retrieved pages, with citations to the exact page. **Permissions are enforced by the
retrieval layer in code, never by the language model**: the model only ever sees text the
requesting user is allowed to read.

All data is synthetic. Build spec: [`task.md`](task.md). Design decisions:
[`docs/architecture.md`](docs/architecture.md). Threat model and controls:
[`docs/security.md`](docs/security.md). Measured quality: [`docs/evaluation.md`](docs/evaluation.md).

## How it works

```
JWT (Keycloak, RS256) ─► identity = sub + groups/roles from Postgres (every request)
question ─► bge embedding ─► FAISS top-N ┐
          ─► identifier + full-text top-N ┴► permission filter (Postgres) ─► RRF ─► top-k
top-k permitted chunks ─► prompt with S1..Sk ─► LLM ─► citation check ─► answer + [n] citations
```

### Permission model

A chunk is readable only if its document is `indexed`, neither the chunk nor the document
is soft-deleted, and the user's groups overlap the ACL groups **or** the user is listed
explicitly. Empty ACL = nobody (default deny). The `admin` role manages ACLs and deletions
but **does not grant read access**. Groups come from Postgres on every request, so removing
someone from a group takes effect on their next request, even with an unexpired token.

The predicate exists once, in `app/security/permissions.py`, and CI fails if ACL columns
appear in SQL anywhere else. It is checked at four points: retrieval, prompt building,
citation mapping, and page click-through.

### Adaptive over-fetch, and its trade-off

Vector and keyword search each return the top N candidates *before* permission
filtering. If fewer than `top_k` survive, N doubles (50 → 100 → … → 800). Users with broad
access usually finish in one round; users who can see little of the corpus take more
rounds. **Known limitation:** that extra latency reveals roughly how much of the corpus a
caller can see (not which documents match). It is documented in `docs/security.md`. The
mitigation, if ever needed, is a fixed number of rounds.

## Setup

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Tesseract and Poppler for OCR
(`brew install tesseract poppler` / `apt install tesseract-ocr poppler-utils`).

```bash
uv sync                                   # Python 3.12 env from uv.lock
cp .env.example .env                      # then replace every CHANGE_ME value
```

### Run the stack

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d
# postgres, keycloak (127.0.0.1:8080), ollama (127.0.0.1:11434), api, caddy (https://localhost)
docker compose -f docker/docker-compose.yml exec ollama ollama pull llama3.1:8b
```

**macOS:** Docker has no GPU access, so run Ollama natively (`ollama serve`,
`ollama pull llama3.1:8b`) and add the override, which points the API at
`host.docker.internal:11434` and skips the Ollama container:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.host-ollama.yml \
  --env-file .env up -d --build
```

The API refuses to start if Postgres says documents are indexed but its `/data` volume has
no FAISS file (for example, when you ingested from the host). Regenerate it from Postgres:
`docker compose … run --rm --no-deps api python -m app.cli rebuild-index`, then `up -d api`.

### Generate the corpus, seed users, ingest

```bash
uv run python -m eval.synth.generate_corpus --seed 42 --count 1000 --out ./corpus
uv run python -m app.cli migrate          # owner role (MIGRATION_DATABASE_URL)
uv run python -m app.cli seed-users       # must match docker/keycloak/realm-loanrag.json
uv run python -m app.cli ingest --source ./corpus --acl-config ./config/acl_seed.yaml
# re-running ingest skips already-indexed files; `rebuild-index` regenerates FAISS from Postgres
```

Inside Compose the same commands run in the API container:
`docker compose -f docker/docker-compose.yml run --rm api python -m app.cli ingest --source /corpus --acl-config /app/config/acl_seed.yaml`.

### Keycloak admin console

Open **http://localhost:8080** (it redirects to `/auth/admin/`) and sign in with
`KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` from `.env`. The console and its sign-in page are
served only on this localhost-bound port. Through Caddy, `https://localhost/auth/admin/*` and
`https://localhost/auth/realms/master/*` return 404. The `keycloak-init` service keeps the
admin sign-in on port 8080 each time Keycloak starts.

### Get a token

**Keycloak (development, Direct Access Grants enabled only in the dev realm):**

```bash
TOKEN=$(curl -s http://localhost:8080/auth/realms/loanrag/protocol/openid-connect/token \
  -d grant_type=password -d client_id=loanrag-api \
  -d username=alice -d password="$DEV_USER_PASSWORD" | jq -r .access_token)
```

**Local test key (no Keycloak; tests and CI use this):**

```bash
uv run python -m app.cli gen-test-keys                  # ./keys (git-ignored)
# in .env: JWT_PUBLIC_KEY_PATH=./keys/test_public.pem and leave JWKS_URL empty
TOKEN=$(uv run python -m app.cli mint-token --user u_alice)
```

### API docs page (developers)

End users use the web app ([LoanLens](#loanlens-the-web-app-version-2)). Caddy only routes
`/v1/*` and `/health` to the API, so FastAPI's docs page is available when you run the API
directly (`uv run uvicorn --factory app.api.main:app_factory --port 8000`) at
`http://localhost:8000/docs`:

1. `scripts/token.sh carol` prints a 15-minute Keycloak token (dev password grant).
2. Open the docs page, click **Authorize**, and paste the token.
3. Use `POST /v1/query`, then `GET /v1/documents/{doc_id}/pages/{page}` to open a cited page.

To add documents from the command line (admins can also upload in the app), copy PDFs into
`CORPUS_DIR`, give each one an ACL entry in
`config/acl_seed.yaml` (no entry means nobody can see it), and run
`docker compose … run --rm --no-deps api python -m app.cli ingest --source /corpus --acl-config /app/config/acl_seed.yaml`.
The API picks up the new index version on its next query.

### Ask a question

```bash
uv run uvicorn --factory app.api.main:app_factory --port 8000     # or use https://localhost via Caddy
curl -s localhost:8000/v1/query -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is the interest rate on loan LN-2024-000123?", "top_k": 6}'
```

```json
{"request_id": "…", "status": "answered",
 "answer": "The note carries a fixed interest rate of 6.375% [1].",
 "citations": [{"marker": 1, "doc_id": "…", "filename": "loan_000123.pdf", "page": 1}]}
```

Follow a citation: `GET /v1/documents/{doc_id}/pages/{page}`. Admin endpoints:
`PUT /v1/admin/documents/{id}/acl`, `DELETE /v1/admin/documents/{id}`,
`GET /v1/admin/documents?status=failed`. API docs at `/docs`.

### Switch generators

| `LLM_PROVIDER` | Settings |
|---|---|
| `ollama` (default) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL=llama3.1:8b` (`llama3.2:3b` on CPU-only hosts) |
| `hosted` | `HOSTED_LLM_BASE_URL` (Groq `https://api.groq.com/openai/v1`, or Gemini `https://generativelanguage.googleapis.com/v1beta/openai/`), `HOSTED_LLM_MODEL`, `HOSTED_LLM_API_KEY`. Read `docs/security.md` §3 first. |
| `fake` | Deterministic test adapter. Refused in production. |

There is no automatic fallback between providers.

### Evaluate

```bash
uv run python -m eval.synth.generate_questions          # eval/questions.jsonl
uv run python -m app.cli eval --provider fake           # retrieval metrics + pipeline checks
uv run python -m app.cli eval --provider ollama
uv run python -m app.cli eval --provider hosted         # throttled: EVAL_REQUESTS_PER_MINUTE
```

## LoanLens: the web app (version 2)

Version 2 adds a browser app with sign-in, a dashboard and a chat window, plus admin uploads,
on top of the API. Its spec is [`extension.md`](extension.md). Corpus files are now 50–100 pages
each.

### Open the app

1. Start the stack (above; on macOS use the `host-ollama` override and run `ollama serve`).
2. Open **https://localhost**. Caddy uses a local certificate, so accept the browser warning
   once, or run `caddy trust`.
3. Click **Sign in**. You land on Keycloak's sign-in page, served from the same origin under
   `/auth`. The password for every demo user is `DEV_USER_PASSWORD` in your `.env`.
4. After "Checking your access", the dashboard shows who you are, your access groups and how
   many loan files you can read. Ask a question that names a loan number, then click a
   citation chip to open the page with the cited passage highlighted.

### Demo users and their access

Counts are for the generated corpus (seed 42), before any uploads.

| Username | Groups | Role | Readable loan files |
|---|---|---|---|
| `alice` | Underwriting, Western region | | 402 |
| `bob` | Servicing | | 207 |
| `carol` | Compliance, Eastern region | | 357 |
| `dana` | Servicing (+ individual grants) | | 224 |
| `erin` | Executive | | 199 |
| `frank` | Underwriting, Eastern region | | 369 |
| `grace` | Western region | | 228 |
| `ivan` | Compliance | | 198 |
| `henry` | none | | 0 |
| `admin` | none | admin | 0 (admins manage files but cannot read them) |

### Upload loan files (admins)

Sign in as `admin`, then open **Manage files**. Click **Upload files**, drop up to 20 PDFs
(50 MB each), and choose who can read them. Files upload two at a time and move through
Uploading → Waiting → Processing (page X of Y) → Ready. Progress continues in the Uploads tray
if you close the dialog. From the table you can edit a file's access, retry a failed upload or
delete a file. The `worker` service does the processing, with the same pipeline as the CLI.

### Frontend development

```bash
cd frontend
npm ci
npm run lint && npm run typecheck && npm test   # ESLint, tsc (strict), Vitest + RTL + MSW
npm run build                                   # production build (also built in Docker, target `web`)
npm run e2e                                     # Playwright + axe against the running stack
```

The end-to-end suite (UI-T1 to UI-T22) needs the full stack with the corpus ingested. It signs
in through the real Keycloak page, uploads fresh fixture PDFs on each run, and temporarily
changes state it restores afterwards: group memberships and the realm's token lifespan.

### macOS notes

- Docker on macOS has no GPU, so run Ollama natively and use
  `docker/docker-compose.host-ollama.yml`.
- Keep generated data outside iCloud-synced folders: set `CORPUS_DIR` and `APP_DATA_DIR` in
  `.env` (for example `~/loanrag-data/corpus` and `~/loanrag-data/data`). iCloud-evicted files
  cannot be read through Docker mounts.
- Bulk ingestion is fastest on the host with `EMBEDDING_DEVICE=mps` and
  `ingest --workers 6`. The containers then share the index through `APP_DATA_DIR`.

## Development

```bash
uv run pytest                     # all tests; Docker required (testcontainers Postgres)
uv run pytest tests/security      # mandatory suite T1–T18
uv run ruff check . && uv run mypy app
uv run python scripts/check_permission_sql.py
```

Tests use the FakeAdapter and a locally generated RSA key. They never need Keycloak, a
model server, or an API key.

## Design decisions (short)

- **Postgres is the source of truth; FAISS is derived.** A single writer (advisory lock),
  atomic index saves, and a version counter keep them consistent. The API reloads when the
  version changes. A crash mid-ingest leaves documents `pending`, which makes them invisible.
- **ACLs are copied onto chunk rows** so the permission check is one indexed SQL predicate.
  Admin ACL changes update the document and every chunk in one transaction.
- **Hybrid retrieval.** bge-small vectors plus Postgres `simple` full-text search plus exact
  identifier matching (loan numbers, amounts, ZIPs), fused with RRF on visible-only ranks.
- **Labels, not IDs, in prompts.** The model cites `S1..Sk`; the server maps labels to
  permitted chunks and rejects anything else (one regeneration, then "insufficient").
- **Hidden equals missing.** Hidden and nonexistent documents get identical responses.
- **Logs carry IDs, not content.** No question, answer, chunk text, loan numbers or
  secrets. The audit log stores a keyed HMAC of the question and is append-only for the app
  role.
