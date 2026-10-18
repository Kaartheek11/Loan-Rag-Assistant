"""Large loan files (extension.md §17.7) and the §17.2 context prefix."""

from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.ingestion.acl_source import YamlAclBackend
from app.ingestion.chunk import CHUNK_TOKENS, MAX_MODEL_TOKENS
from app.ingestion.context import DocumentFields, build_prefix, extract_document_fields
from app.ingestion.pipeline import IngestionPipeline, prepare_document
from app.ingestion.progress import Progress
from app.retrieval.index_manager import FaissIndex, IndexHolder
from app.retrieval.retriever import Retriever
from app.security.identity import Identity
from eval.synth.generate_corpus import generate
from tests.helpers import make_pdf, write_acl

ALL_GROUPS = frozenset(
    {"underwriting", "servicing", "compliance", "western_region", "eastern_region", "executive"}
)
SEES_ALL = Identity("u_test_all", ALL_GROUPS, frozenset())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


@pytest.fixture(scope="module")
def long_corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("long")
    dirs = {"corpus": root / "corpus", "config": root / "config", "synth": root / "synth"}
    generate(
        seed=21,
        count=3,
        out_dir=dirs["corpus"],
        config_dir=dirs["config"],
        synth_dir=dirs["synth"],
        min_pages=70,
        max_pages=72,
        scanned_doc_frac=0.0,
    )
    # Every document visible to the test identity (ACLs are not what this file tests).
    names = sorted(p.name for p in dirs["corpus"].glob("*.pdf"))
    write_acl(
        dirs["config"] / "acl_seed.yaml",
        {n: {"groups": ["underwriting"], "users": []} for n in names},
    )
    return dirs


def _ingest(
    engine: Engine,
    embedder,
    corpus: dict[str, Path],
    tmp: Path,  # type: ignore[no-untyped-def]
    **kw: object,
) -> IngestionPipeline:
    pipe = IngestionPipeline(
        engine,
        embedder,
        index_path=tmp / "index.faiss",
        document_store=None,
        acl_backend=YamlAclBackend(corpus["config"] / "acl_seed.yaml"),
        **kw,
    )  # type: ignore[arg-type]
    pipe.summary = pipe.run(corpus["corpus"])  # type: ignore[attr-defined]
    return pipe


def test_context_dependent_question_finds_deep_page(  # type: ignore[no-untyped-def]
    app_engine, clean_db, embedder, long_corpus, tmp_path
) -> None:
    _ingest(app_engine, embedder, long_corpus, tmp_path)
    facts = [json.loads(line) for line in (long_corpus["synth"] / "facts.jsonl").open()]
    deep = [
        f
        for f in facts
        if f["page"] >= 60
        and f["context_dependent"]
        and f["fact_type"]
        in (
            "escrow_item",
            "rate_lock",
            "prepayment_penalty",
            "late_fee",
            "loe_withdrawal",
            "condition",
        )
    ]
    assert deep, "corpus should have deep context-dependent facts"
    questions = {
        "escrow_item": "What is the annual escrow disbursement for {hint} on loan {ln}?",
        "rate_lock": "When does the interest rate lock expire for loan {ln}?",
        "prepayment_penalty": "What prepayment penalty percentage applies to loan {ln}?",
        "late_fee": "What late fee was charged for the payment due {hint} on loan {ln}?",
        "loe_withdrawal": "How much was the withdrawal explained in the letter dated {hint} "
        "for loan {ln}?",
        "condition": "Before funding loan {ln}, which organization must provide the document "
        "{hint}?",
    }
    holder = IndexHolder(tmp_path / "index.faiss", embedder.dim)
    retriever = Retriever(holder, n_start=50, n_max=800)
    checked = 0
    for fact in deep[:4]:
        with app_engine.connect() as conn:
            holder.ensure_current(conn)
            gold = conn.execute(
                text(
                    "SELECT c.chunk_id, c.text FROM chunks c JOIN documents d USING (doc_id) "
                    "WHERE d.filename = :f AND c.page = :p"
                ),
                {"f": fact["doc_filename"], "p": fact["page"]},
            ).all()
            gold_ids = {r.chunk_id for r in gold if _norm(fact["fact_text"]) in _norm(r.text)}
            assert gold_ids
            # The answer chunk itself names neither the loan number nor the borrower.
            assert all(fact["loan_number"] not in r.text for r in gold if r.chunk_id in gold_ids)
            q = questions[fact["fact_type"]].format(
                ln=fact["loan_number"], hint=fact["question_hint"]
            )
            result = retriever.retrieve(
                conn, SEES_ALL, q, embedder.embed_query(q), 10, str(uuid.uuid4())
            )
        assert set(result.permitted.ids()) & gold_ids, (fact["fact_type"], fact["page"])
        checked += 1
    assert checked >= 1


def test_reingest_large_files_creates_no_duplicates(  # type: ignore[no-untyped-def]
    app_engine, clean_db, embedder, long_corpus, tmp_path
) -> None:
    first = _ingest(app_engine, embedder, long_corpus, tmp_path).summary  # type: ignore[attr-defined]
    with app_engine.connect() as conn:
        counts = conn.execute(
            text("SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM pages)")
        ).one()
    second = _ingest(app_engine, embedder, long_corpus, tmp_path).summary  # type: ignore[attr-defined]
    assert first.chunks_created == counts[0] and second.chunks_created == 0
    assert len(second.skipped) == 3
    with app_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT (SELECT count(*) FROM chunks), (SELECT count(*) FROM pages)")
            ).one()
            == counts
        )
        db_ids = set(conn.execute(text("SELECT chunk_id FROM chunks")).scalars())
        fields = conn.execute(text("SELECT loan_number, borrower_name FROM documents")).all()
    assert FaissIndex.load(tmp_path / "index.faiss").ids() == db_ids
    assert all(r.loan_number and r.borrower_name for r in fields)


def test_chunks_carry_prefix_identifier_and_token_budget(  # type: ignore[no-untyped-def]
    embedder, long_corpus
) -> None:
    path = sorted(long_corpus["corpus"].glob("*.pdf"))[0]
    doc = prepare_document(path, embedder.count_tokens)
    assert doc.fields.loan_number and doc.fields.borrower_name
    assert len(doc.pages) >= 70
    for c in doc.chunks:
        assert c.context_prefix.startswith(
            f"Loan {doc.fields.loan_number} | Borrower {doc.fields.borrower_name}"
        )
        assert doc.fields.loan_number in c.identifiers  # §17.2 identifier search
        assert c.token_count <= CHUNK_TOKENS + 20 <= MAX_MODEL_TOKENS  # budget incl. prefix
        assert c.context_prefix not in c.text
    sections = {c.section for c in doc.chunks}
    assert {"Bank statements", "Deed of trust", "Payment history"} <= sections


def test_missing_loan_number_warns_and_keeps_partial_prefix(  # type: ignore[no-untyped-def]
    app_engine, clean_db, embedder, tmp_path
) -> None:
    src = tmp_path / "src"
    pages = [
        "LOAN APPLICATION\nBorrower name: Pat Example. No reference number is shown.",
        "BANK STATEMENTS\nThe ending balance was $1,234.56 for the month.",
    ] + [f"APPRAISAL REPORT\nPage body {i} without identifiers." for i in range(6)]
    make_pdf(src / "loan_nofields.pdf", pages)
    acl = write_acl(
        tmp_path / "acl.yaml", {"loan_nofields.pdf": {"groups": ["servicing"], "users": []}}
    )
    summary = IngestionPipeline(
        app_engine,
        embedder,
        index_path=tmp_path / "i.faiss",
        document_store=None,
        acl_backend=YamlAclBackend(acl),
    ).run(src)
    assert summary.processed == ["loan_nofields.pdf"]
    assert summary.missing_fields == [("loan_nofields.pdf", ["loan_number"])]
    assert "document-field warnings" in summary.render()
    with app_engine.connect() as conn:
        doc = conn.execute(text("SELECT loan_number, borrower_name FROM documents")).one()
        prefixes = list(
            conn.execute(text("SELECT context_prefix FROM chunks ORDER BY page")).scalars()
        )
    assert doc.loan_number is None and doc.borrower_name == "Pat Example"
    assert prefixes[0] == "Borrower Pat Example | Section: Loan application"
    assert all(p.startswith("Borrower Pat Example | Section: ") for p in prefixes)


def test_document_fields_and_prefix_units() -> None:
    f = extract_document_fields(
        [
            "Loan ln-2023-000042 | Page 1 of 9\nBorrower name: Ana M. "
            "de Silva. Loan number: LN-2023-000042."
        ]
    )
    assert f == DocumentFields("LN-2023-000042", "Ana M.")
    assert extract_document_fields(["nothing here"]).missing == ["loan_number", "borrower_name"]
    late = extract_document_fields(["x"] * 5 + ["Borrower name: Late Person"])
    assert late.borrower_name is None  # only the first 5 pages count
    assert (
        build_prefix(DocumentFields("LN-1", "A B"), "Bank statements")
        == "Loan LN-1 | Borrower A B | Section: Bank statements"
    )
    assert build_prefix(DocumentFields(None, None), None) == ""


def test_parallel_prepare_matches_sequential(  # type: ignore[no-untyped-def]
    app_engine, clean_db, embedder, long_corpus, tmp_path
) -> None:
    seq = _ingest(app_engine, embedder, long_corpus, tmp_path / "a", workers=1)
    with app_engine.connect() as conn:
        a = conn.execute(
            text(
                "SELECT d.filename, c.page, c.text, c.context_prefix, "
                "c.token_count FROM chunks c JOIN documents d USING (doc_id) "
                "ORDER BY 1, 2, c.chunk_id"
            )
        ).all()
    from tests.conftest import truncate_all

    truncate_all(clean_db)
    par = _ingest(app_engine, embedder, long_corpus, tmp_path / "b", workers=2)
    with app_engine.connect() as conn:
        b = conn.execute(
            text(
                "SELECT d.filename, c.page, c.text, c.context_prefix, "
                "c.token_count FROM chunks c JOIN documents d USING (doc_id) "
                "ORDER BY 1, 2, c.chunk_id"
            )
        ).all()
    assert seq.summary.chunks_created == par.summary.chunks_created  # type: ignore[attr-defined]
    assert a == b


def test_progress_emits_at_least_every_30_seconds() -> None:
    now = [0.0]
    out = io.StringIO()
    progress = Progress(10, interval=10.0, stream=out, clock=lambda: now[0])
    emitted_at: list[float] = []
    for _doc in range(10):
        for _ in range(80):  # pages of 0.1-3 s each (OCR pages are the slow ones)
            now[0] += 3.0 if _ % 17 == 0 else 0.1
            before = len(progress.lines)
            progress.page()
            if len(progress.lines) > before:
                emitted_at.append(now[0])
        progress.doc()
    progress.finish()
    gaps = [b - a for a, b in zip([0.0, *emitted_at], emitted_at, strict=False)]
    assert gaps and max(gaps) <= 30.0
    last = progress.lines[-1]
    assert "10/10 documents" in last and "pages/s" in last and "ETA" in last
    assert out.getvalue().count("progress:") == len(progress.lines)


def test_pipeline_prints_progress_lines(  # type: ignore[no-untyped-def]
    app_engine, clean_db, embedder, long_corpus, tmp_path
) -> None:
    out = io.StringIO()
    _ingest(app_engine, embedder, long_corpus, tmp_path, progress_stream=out, progress_interval=0.0)
    lines = out.getvalue().splitlines()
    assert lines and all(line.startswith("progress: ") for line in lines)
    assert "3/3 documents" in lines[-1]
