"""Evaluation harness (spec §11.3). Synthetic data only.

  uv run python -m app.cli eval --provider fake
  uv run python -m app.cli eval --provider ollama
  uv run python -m app.cli eval --provider hosted      # throttled by EVAL_REQUESTS_PER_MINUTE

Metrics: Recall@5/@10 and MRR (vector, keyword, hybrid), citation validity, groundedness,
answer accuracy, refusal accuracy, permission-probe results, permission leaks, latency.

Permission leaks are checked against an independent oracle built from config/acl_seed.yaml
and config/users_seed.yaml, not against the permission module under test.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, text

import app  # noqa: F401  # sets the macOS OpenMP guard before torch/faiss load
from app.audit.audit_log import AuditLogger
from app.config import get_settings
from app.db.seed import read_index_state
from app.db.session import make_engine
from app.generation.llm_adapter import build_adapter
from app.query_service import QueryService
from app.retrieval.embedder import Embedder
from app.retrieval.index_manager import IndexHolder
from app.retrieval.retriever import Mode
from app.security.auth import resolve_identity
from app.security.identity import Identity

_WS = re.compile(r"\s+")
_FACT_TOKENS = re.compile(
    r"LN-\d{4}-\d{6}"
    r"|\$\d[\d,]*(?:\.\d+)?"
    r"|\d+(?:\.\d+)?%"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+\d{1,2},\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\b\d[\d,]*(?:\.\d+)?\b"
)
_MARKER = re.compile(r"\[\d+\]")


def norm(s: str) -> str:
    return _WS.sub(" ", s).strip().casefold()


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def hardware() -> str:
    cpu = platform.processor() or platform.machine()
    try:
        cpu = (
            subprocess.run(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            or cpu
        )
        mem = (
            int(
                subprocess.run(
                    ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
            )
            // 2**30
        )
        return f"{cpu}, {mem} GB RAM, {platform.system()} {platform.release()}"
    except (OSError, subprocess.CalledProcessError, ValueError):
        return f"{cpu}, {platform.system()} {platform.release()}"


class Oracle:
    """Who may read what, computed from the seed files (independent of app.security)."""

    def __init__(self, acl_path: Path, users_path: Path) -> None:
        docs = yaml.safe_load(acl_path.read_text())["documents"]
        self.docs = {
            f: (set(e.get("groups") or []), set(e.get("users") or [])) for f, e in docs.items()
        }
        self.users = {
            u["user_id"]: set(u.get("groups") or [])
            for u in yaml.safe_load(users_path.read_text())["users"]
        }

    def can_read(self, user: str, filename: str) -> bool:
        groups, users = self.docs.get(filename, (set(), set()))
        return bool(groups & self.users.get(user, set())) or user in users


def load_chunks(engine: Engine) -> dict[int, dict[str, Any]]:
    sql = text(
        "SELECT c.chunk_id, d.filename, c.page, c.text FROM chunks c "
        "JOIN documents d ON d.doc_id = c.doc_id "
        "WHERE d.status = 'indexed' AND d.deleted_at IS NULL AND c.deleted_at IS NULL"
    )
    with engine.connect() as conn:
        return {
            int(r.chunk_id): {"filename": r.filename, "page": int(r.page), "text": r.text}
            for r in conn.execute(sql)
        }


def gold_chunk_ids(
    gold: list[dict[str, Any]],
    by_page: dict[tuple[str, int], list[int]],
    chunks: dict[int, dict[str, Any]],
) -> set[int]:
    """Locate the planted fact text inside the chunks of the gold document and page."""
    found: set[int] = set()
    for g in gold:
        fact = norm(g["fact"])
        candidates = by_page.get((g["doc"], int(g["page"])), [])
        exact = {c for c in candidates if fact in norm(chunks[c]["text"])}
        if exact:
            found |= exact
            continue
        # OCR fallback: most of the fact's tokens present in the chunk.
        fact_tokens = set(fact.split())
        for c in candidates:
            tokens = set(norm(chunks[c]["text"]).split())
            if fact_tokens and len(fact_tokens & tokens) / len(fact_tokens) >= 0.8:
                found.add(c)
    return found


def ranking_metrics(ranked: list[int], gold: set[int]) -> tuple[float, float, float]:
    r5 = float(bool(set(ranked[:5]) & gold))
    r10 = float(bool(set(ranked[:10]) & gold))
    rr = next((1.0 / i for i, c in enumerate(ranked[:10], 1) if c in gold), 0.0)
    return r5, r10, rr


_NP_IDENT_SQL = text(
    """
    SELECT c.chunk_id FROM chunks c
    WHERE c.deleted_at IS NULL AND (
        c.identifiers && CAST(:other AS text[])
        OR (c.identifiers && CAST(:loans AS text[]) AND EXISTS (
            SELECT 1 FROM unnest(CAST(:loans AS text[])) l WHERE strpos(upper(c.text), l) > 0))
    )
    ORDER BY ts_rank_cd(to_tsvector('simple', c.text), websearch_to_tsquery('simple', :q)) DESC,
             c.chunk_id
    LIMIT :n
    """
)
_NP_FTS_SQL = text(
    """
    SELECT c.chunk_id FROM chunks c, websearch_to_tsquery('simple', :q) AS query
    WHERE c.deleted_at IS NULL AND to_tsvector('simple', c.text) @@ query
    ORDER BY ts_rank_cd(to_tsvector('simple', c.text), query) DESC, c.chunk_id
    LIMIT :n
    """
)


def keyword_search_no_prefix(conn: Any, question: str, n: int) -> list[int]:
    """Keyword search as if chunks had no context prefix (extension.md §17.5 comparison):
    full text over the raw chunk text only, and a loan number matches only where it appears
    in the chunk's own text (the prefix is what adds it to every chunk's identifiers)."""
    from app.retrieval.identifiers import LOAN_NUMBER, extract_identifiers
    from app.retrieval.keyword_search import fulltext_query

    q = fulltext_query(question)
    idents = extract_identifiers(question)
    loans = [i for i in idents if LOAN_NUMBER.fullmatch(i)]
    other = [i for i in idents if i not in loans]
    ranked: list[int] = []
    if idents:
        ranked += [
            int(r[0])
            for r in conn.execute(_NP_IDENT_SQL, {"other": other, "loans": loans, "q": q, "n": n})
        ]
    if q and len(ranked) < n:
        seen = set(ranked)
        for r in conn.execute(_NP_FTS_SQL, {"q": q, "n": n}):
            if int(r[0]) not in seen:
                ranked.append(int(r[0]))
                seen.add(int(r[0]))
    return ranked[:n]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--questions", type=Path, default=Path("eval/questions.jsonl"))
    p.add_argument("--provider", choices=["fake", "ollama", "hosted"], default=None)
    p.add_argument("--acl", type=Path, default=Path("config/acl_seed.yaml"))
    p.add_argument("--users", type=Path, default=Path("config/users_seed.yaml"))
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--top-k", type=int, default=6)
    p.add_argument("--latency-sample", type=int, default=100)
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument(
        "--no-prefix-index",
        type=Path,
        default=None,
        help="index built with `rebuild-index --no-context-prefix` (§17.5)",
    )
    p.add_argument(
        "--latency-runs",
        type=int,
        default=100,
        help="retrievals timed for the narrowest-access users (§17.6)",
    )
    p.add_argument("--out", type=Path, default=Path("eval/results"))
    args = p.parse_args(argv)

    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    engine = make_engine(settings.database_url, environment=settings.environment)
    embedder = Embedder(settings.embedding_model, device=settings.embedding_device)
    with engine.connect() as conn:
        state = read_index_state(conn)
    if state is None:
        raise SystemExit("index_state missing: run migrate + ingest first")
    holder = IndexHolder(Path(settings.faiss_index_path), embedder.dim)
    holder.load(state[0])
    key = settings.audit_hmac_key.get_secret_value() if settings.audit_hmac_key else ""
    llm = build_adapter(settings)
    service = QueryService(
        engine=engine,
        embedder=embedder,
        index=holder,
        llm=llm,
        audit=AuditLogger(engine, key),
        n_start=settings.retrieval_n_start,
        n_max=settings.retrieval_n_max,
    )
    oracle = Oracle(args.acl, args.users)
    chunks = load_chunks(engine)
    # The leak oracle knows only the seed ACLs, so documents added later (admin uploads,
    # including the E2E fixtures) would be reported as leaks, or skew the metrics. Refuse.
    extra = sorted({c["filename"] for c in chunks.values()} - set(oracle.docs))
    if extra:
        raise SystemExit(
            f"{len(extra)} live document(s) are not in the seed corpus (e.g. {extra[0]}): "
            "delete uploaded documents (admin Files page or DELETE /v1/admin/documents/{id}) "
            "before running the evaluation"
        )
    by_page: dict[tuple[str, int], list[int]] = {}
    for cid, c in chunks.items():
        by_page.setdefault((c["filename"], c["page"]), []).append(cid)

    questions = [json.loads(line) for line in args.questions.open()]
    if args.limit:
        questions = questions[: args.limit]
    identities: dict[str, Identity] = {}
    with engine.connect() as conn:
        for uid in {q["user"] for q in questions}:
            identity = resolve_identity(conn, uid)
            if identity is None:
                raise SystemExit(f"eval user {uid!r} is unknown or disabled: run seed-users")
            identities[uid] = identity

    # ---- Retrieval-only comparison (no LLM) -------------------------------------------
    from app.retrieval.retriever import Retriever

    retrievers: dict[str, Retriever] = {"with_prefix": service.retriever}
    if args.no_prefix_index:
        np_holder = IndexHolder(args.no_prefix_index, embedder.dim)
        np_holder.load(state[0])
        retrievers["without_prefix"] = Retriever(
            np_holder,
            n_start=settings.retrieval_n_start,
            n_max=settings.retrieval_n_max,
            keyword_fn=keyword_search_no_prefix,
        )
    modes: tuple[Mode, ...] = ("vector", "keyword", "hybrid")
    metrics: dict[str, dict[str, list[tuple[float, float, float]]]] = {
        v: {m: [] for m in modes} for v in retrievers
    }
    groups_of: list[tuple[str, bool]] = []  # (depth, context_dependent) per answerable question
    gold_missing = 0
    per_question: list[dict[str, Any]] = []
    for q in questions:
        if q["kind"] != "answerable":
            continue
        gold = gold_chunk_ids(q["gold"], by_page, chunks)
        if not gold:
            gold_missing += 1
        groups_of.append((q.get("depth", "-"), bool(q.get("context_dependent"))))
        qvec = embedder.embed_query(q["question"])
        for variant, retriever in retrievers.items():
            for mode in modes:
                with engine.connect() as conn:
                    res = retriever.retrieve(
                        conn,
                        identities[q["user"]],
                        q["question"],
                        qvec,
                        10,
                        str(uuid.uuid4()),
                        mode=mode,
                    )
                metrics[variant][mode].append(ranking_metrics(res.permitted.ids(), gold))
    retrieval = metrics["with_prefix"]

    def agg(rows: list[tuple[float, float, float]]) -> dict[str, float]:
        n = len(rows) or 1
        return {
            "recall@5": sum(r[0] for r in rows) / n,
            "recall@10": sum(r[1] for r in rows) / n,
            "mrr": sum(r[2] for r in rows) / n,
        }

    report: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "hardware": hardware(),
        "provider": settings.llm_provider,
        "model": {
            "ollama": settings.ollama_model,
            "hosted": settings.hosted_llm_model,
            "fake": "FakeAdapter (extractive)",
        }[settings.llm_provider],
        "embedding_model": settings.embedding_model,
        "index_version": state[0],
        "chunks": len(chunks),
        "questions": {
            k: sum(q["kind"] == k for q in questions)
            for k in ("answerable", "unanswerable", "probe")
        },
        "gold_not_locatable": gold_missing,
        "retrieval": {mode: agg(rows) for mode, rows in retrieval.items()},
    }
    if "without_prefix" in metrics:
        report["retrieval_without_prefix"] = {
            mode: agg(rows) for mode, rows in metrics["without_prefix"].items()
        }

    def recall10(rows: list[tuple[float, float, float]], keep: list[bool]) -> dict[str, float]:
        picked = [r[1] for r, k in zip(rows, keep, strict=True) if k]
        return {"recall@10": sum(picked) / (len(picked) or 1), "n": len(picked)}

    depths = sorted({d for d, _ in groups_of})
    report["recall_at_10_breakdown"] = {
        variant: {
            mode: {
                **{f"depth {d}": recall10(rows, [g[0] == d for g in groups_of]) for d in depths},
                "context_dependent": recall10(rows, [g[1] for g in groups_of]),
            }
            for mode, rows in by_mode.items()
        }
        for variant, by_mode in metrics.items()
    }

    # §17.6: p50/p95 retrieval latency for the users with the narrowest access.
    from app.security import permissions as perm

    with engine.connect() as conn:
        counts = {
            uid: perm.count_readable_documents(conn, ident)
            for uid, ident in identities.items()
            if ident is not None
        }
    narrow_some = min((u for u, c in counts.items() if c > 0), key=lambda u: counts[u])
    narrow_none = next((u for u, c in counts.items() if c == 0), None)
    latency: dict[str, Any] = {}
    sample = [q["question"] for q in questions if q["kind"] == "answerable"][: args.latency_runs]
    for label, uid in (("narrowest_with_access", narrow_some), ("no_access", narrow_none)):
        if uid is None:
            continue
        times: list[float] = []
        rounds: list[int] = []
        for question in sample:
            t0 = time.perf_counter()
            qv = embedder.embed_query(question)
            with engine.connect() as conn:
                res = service.retriever.retrieve(
                    conn, identities[uid], question, qv, args.top_k, str(uuid.uuid4())
                )
            times.append((time.perf_counter() - t0) * 1000)
            rounds.append(res.rounds)
        latency[label] = {
            "user": uid,
            "readable_documents": counts[uid],
            "n": len(times),
            "p50_ms": round(percentile(times, 0.5), 1),
            "p95_ms": round(percentile(times, 0.95), 1),
            "max_rounds": max(rounds),
            "mean_rounds": round(statistics.fmean(rounds), 2),
        }
    report["retrieval_latency_narrowest"] = latency

    if not args.retrieval_only:
        # Throttle only the hosted free tier (§8.7); latency is measured after the wait.
        interval = (
            60.0 / settings.eval_requests_per_minute if settings.llm_provider == "hosted" else 0.0
        )
        latencies: list[float] = []
        cit_total = cit_valid = 0
        grounded_total = grounded_ok = 0
        correct = answerable_n = 0
        refused = unanswerable_n = 0
        probe_ok = probe_n = 0
        leaks: list[dict[str, Any]] = []
        request_ids: list[str] = []
        last = 0.0
        for q in questions:
            if interval:
                wait = last + interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                last = time.monotonic()
            t0 = time.perf_counter()
            out = service.answer(identities[q["user"]], q["question"], args.top_k)
            elapsed = (time.perf_counter() - t0) * 1000
            request_ids.append(out.request_id)
            if len(latencies) < args.latency_sample:
                latencies.append(elapsed)
            returned = set(out.returned_chunk_ids)
            for cid in returned | set(out.cited_chunk_ids):
                fn = chunks.get(cid, {}).get("filename")
                if fn is None or not oracle.can_read(q["user"], fn):
                    leaks.append({"id": q["id"], "chunk_id": cid, "filename": fn})
            cit_total += len(out.cited_chunk_ids)
            cit_valid += sum(1 for c in out.cited_chunk_ids if c in returned)
            if out.status == "answered":
                cited_text = " ".join(chunks[c]["text"] for c in out.cited_chunk_ids if c in chunks)
                for tok in _FACT_TOKENS.findall(_MARKER.sub(" ", out.answer)):
                    grounded_total += 1
                    grounded_ok += tok in cited_text
            ok: bool | None = None
            if q["kind"] == "answerable":
                answerable_n += 1
                ok = out.status == "answered" and norm(q["answer"]) in norm(out.answer)
                correct += ok
            elif q["kind"] == "unanswerable":
                unanswerable_n += 1
                ok = out.status == "insufficient"
                refused += ok
            else:
                probe_n += 1
                gold_hidden = {c for c in gold_chunk_ids(q["gold"], by_page, chunks)}
                ok = out.status == "insufficient" and not (returned & gold_hidden)
                probe_ok += ok
            per_question.append(
                {
                    "id": q["id"],
                    "kind": q["kind"],
                    "user": q["user"],
                    "status": out.status,
                    "correct": ok,
                    "llm_calls": out.llm_calls,
                    "latency_ms": round(elapsed, 1),
                    "answer": out.answer,
                }
            )
        # Audit rows for this run's requests (not other traffic on a live stack): hidden
        # chunks must not appear there either.
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT user_id, returned_chunk_ids FROM audit_log "
                    "WHERE request_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": request_ids},
            ).all()
        for row in rows:
            for cid in row.returned_chunk_ids:
                fn = chunks.get(int(cid), {}).get("filename")
                if fn is None or not oracle.can_read(row.user_id, fn):
                    leaks.append({"audit_user": row.user_id, "chunk_id": int(cid)})
        report["generation"] = {
            "answer_accuracy": correct / (answerable_n or 1),
            "refusal_accuracy": refused / (unanswerable_n or 1),
            "probe_insufficient_and_no_gold": probe_ok / (probe_n or 1),
            "citation_validity": cit_valid / cit_total if cit_total else 1.0,
            "citations": cit_total,
            "groundedness": grounded_ok / grounded_total if grounded_total else 1.0,
            "grounded_tokens": grounded_total,
            "permission_leaks": len(leaks),
            "audit_rows_checked": len(rows),
            "latency_ms": {
                "n": len(latencies),
                "p50": round(percentile(latencies, 0.5), 1),
                "p95": round(percentile(latencies, 0.95), 1),
                "mean": round(statistics.fmean(latencies), 1) if latencies else 0,
            },
            "llm_calls": sum(r["llm_calls"] for r in per_question),
        }
        report["leaks"] = leaks[:50]

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = args.out / f"{settings.llm_provider}_{stamp}.json"
    path.write_text(json.dumps({**report, "per_question": per_question}, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nwrote {path}")
    leaked = report.get("generation", {}).get("permission_leaks", 0)
    return 1 if leaked else 0


if __name__ == "__main__":
    raise SystemExit(main())
