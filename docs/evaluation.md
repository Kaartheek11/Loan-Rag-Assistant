# Evaluation

All numbers below were measured on this build on **2026-10-17**. Synthetic data only.
Raw per-question results: `eval/results/*.json` (git-ignored; regenerate with the commands
at the end).

## Setup

| Item | Value |
|---|---|
| Hardware | Apple M5, 16 GB RAM, macOS (Darwin 25.6.0). Postgres 16.4 in Docker Desktop on the same machine. |
| Corpus | `generate_corpus.py --seed 42 --count 1000`: 1,000 PDFs, 3–12 pages, 50 image-only scans, 10 prompt-injection docs, 10 empty-ACL docs, ~30 near-duplicate borrowers |
| Ingestion | one command, 1,000 processed / 0 failed, **7,654 chunks**, **418 OCR pages**, 10 empty-ACL warnings |
| Embeddings | `BAAI/bge-small-en-v1.5`, CPU, single-threaded on macOS (architecture D6) |
| Retrieval | hybrid, `RETRIEVAL_N_START=50`, `RETRIEVAL_N_MAX=800`, RRF k=60, `top_k=6` for generation, top 10 for retrieval metrics |
| Prompt | `v1` (`app/generation/prompts.py`) |
| Generators | Ollama `llama3.1:8b` (Ollama 0.x via Homebrew, Metal), and the deterministic `FakeAdapter` as a pipeline baseline. Hosted: **not run yet** (see below). |

### Question set (`eval/questions.jsonl`, `generate_questions.py --seed 7`)

| Kind | Count | Notes |
|---|---|---|
| Answerable | 160 | 128 planted facts (escrow item, condition of approval, reserves, rate lock, HOA dues, prepayment penalty) + 32 base facts (rate, principal, term). Every question is asked by a user who can read the gold document. |
| Unanswerable | 25 | Facts in no document (e.g. "llama grazing fee", "helicopter pad inspection") on real loans, plus loan numbers that do not exist |
| Permission probes | 35 | The gold document exists but is hidden from the asking user. Expected: `insufficient`, no hidden chunk returned |

Gold labels are document + page + planted fact text. At eval time the gold chunk is found
by locating the fact text in chunks of that page (with a token-overlap fallback for OCR).
All 160 gold chunks were locatable.

**Hand review (30 random questions).** Two ambiguous unanswerable templates were found and
fixed before the runs. "Annual boat slip fee in the escrow account" could match documents
with a real *dock permit renewal* escrow item, and "which marina association collects dues"
could match real "… Harbor Homeowners Association" HOA facts. They were replaced by "llama
grazing fee" and "airport authority … hangar lease". The other 28 were unambiguous. Note:
some rate-lock dates fall before the loan's origination year. That is unrealistic, but
unambiguous.

## Results

### Retrieval-only comparison (answerable questions, permission-filtered)

| Retriever | Recall@5 | Recall@10 | MRR |
|---|---|---|---|
| Vector only | 0.263 | 0.319 | 0.139 |
| Keyword only (identifier + full-text) | **1.000** | **1.000** | **0.800** |
| Hybrid (RRF) | 0.919 | **0.988** | 0.588 |

Target Recall@10 ≥ 0.80: **met** (0.988).

### Generation (same retrieved context for every generator)

| Metric | FakeAdapter (baseline) | Ollama llama3.1:8b | Hosted |
|---|---|---|---|
| Answer accuracy (160) | 0.856 | **0.894** | not run |
| Refusal accuracy (25) | 0.880 | **1.000** | not run |
| Probes → insufficient with no gold chunk (35) | 1.000 | 0.971 (34/35) | not run |
| Citation validity | 1.000 (141 citations) | **1.000** (144) | not run |
| Groundedness (numbers/dates/loan numbers verbatim in cited chunks) | 1.000 (141 tokens) | **1.000** (199 tokens) | not run |
| **Permission leaks** (responses, citations, audit rows) | **0** (220 audit rows) | **0** (222 audit rows) | not run |
| Latency p50 / p95, end to end, first 100 queries | 76 ms / 129 ms | **5.66 s / 7.96 s** | not run |
| LLM calls (incl. regenerations) | 208 | 209 | |

Permission leaks are checked against an independent oracle built from the seed YAML files,
not the permission module under test. The check covers every returned and cited chunk for
all 220 questions, plus every `audit_log.returned_chunk_ids` written during the run.

## Analysis

**Keyword search does the heavy lifting on this question set.** Every question names a loan
number, and the identifier match pins the right document exactly. The corpus is built from
templates, so vector search sees near-identical chunks across 1,000 loans and cannot tell
which loan is meant: vector-only Recall@10 is 0.32. Hybrid still clears the target, but it
scores **below keyword-only** (MRR 0.59 vs 0.80). Hybrid search does not earn its place on
this question set, though it would on questions without identifiers, which this set does not
contain.

**Why hybrid loses to keyword here (RRF dilution).** A chunk ranked #1 by the identifier
match but missing from the vector top-N scores 1/61. A chunk from a *different* loan that
appears in both lists (for example #3 by vector and #8 by full text) scores about
1/63 + 1/68 ≈ 0.031 and wins. This accounts for most of the misses below.

**Error breakdown (Ollama, 17 answerable misses).** Every miss was a refusal
(`insufficient`), never a wrong answer.

| Cause | Count | Detail |
|---|---|---|
| Gold not in top-6 (retrieval) | 12 | 10 HOA-dues facts, which sit on the page-1 *LOAN SUMMARY* chunk when a document has no PROPERTY DETAILS page: the identifier match ranks it #1 but RRF demotes it. 2 conditions of approval. |
| Gold in top-6, model refused | 5 | 2 rate-lock, 1 condition, 1 reserves, 1 prepayment |

All 143 correct answers had the gold chunk in the top 6.

**The failed probe (q187) is not a leak.** u_bob asked about a condition on a loan hidden
from him. The model answered from a *different* loan that bob can see, which carries the
same "HVAC system was replaced" condition. The citation correctly names that other file.
The answer is grounded and permitted, but it ignores the loan-number mismatch. That is a
generator weakness the FakeAdapter does not have (it requires the loan number to appear in
the source).

**Llama 3.1 8B versus the fake baseline.** Refusal accuracy improves from 0.88 to 1.00,
because the fake adapter answers any word overlap. Answer accuracy improves from 0.86 to
0.89. Citation discipline was strong: only one regeneration was needed (209 calls for 208
prompts that reached the model).

## Recommendations (open decisions, §18)

1. **Fusion.** Before adding a reranker (§18.6), try promoting identifier-matched chunks
   in fusion: give the keyword list a higher RRF weight when the question contains an
   identifier, or fuse within the identified documents first. On this set that should
   recover most of the 12 retrieval misses. It is a spec change, so record it in `task.md`
   and the architecture doc first.
2. **Over-fetch ceiling (§18.4).** Users who can see at least one group's documents always
   finished in **one round** (N=50). Users with zero visibility (u_henry, u_admin) ran all
   **5 rounds** (50 → 800). In the FakeAdapter run, `insufficient` answers had a median
   latency of **51 ms after 1 round versus 186 ms after 5 rounds**. That is the timing side
   channel documented in `docs/security.md` §4.1, here measured: response time reveals that
   the caller sees little of the corpus. At this corpus size the 800 ceiling costs about
   0.14 s, so there is no need to tune it yet.
3. **Question set coverage.** Add questions without loan numbers (borrower name or address
   only) so vector search is exercised, and so the hybrid-versus-keyword comparison is fair.

## Hosted model (Groq / Gemini): pending

Not run: no API key is configured on this machine. Before the first run, record the
provider's free-tier data terms in `docs/security.md` §3 (S8). Then:

```bash
# .env: LLM_PROVIDER=hosted, HOSTED_LLM_BASE_URL=https://api.groq.com/openai/v1,
#       HOSTED_LLM_MODEL=<current model name>, HOSTED_LLM_API_KEY=<key>
uv run python -m app.cli eval --provider hosted     # throttled to EVAL_REQUESTS_PER_MINUTE=20
```

Record the exact model name and the run date here, and fill in the "Hosted" column.

## Reproduce

```bash
uv run python -m eval.synth.generate_corpus --seed 42 --count 1000 --out ./corpus
uv run python -m app.cli migrate && uv run python -m app.cli seed-users
uv run python -m app.cli ingest --source ./corpus --acl-config ./config/acl_seed.yaml
uv run python -m eval.synth.generate_questions --seed 7
uv run python -m app.cli eval --provider fake
uv run python -m app.cli eval --provider ollama     # needs `ollama serve` + llama3.1:8b
```
