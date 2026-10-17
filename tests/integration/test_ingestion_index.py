"""Phase 3 + 4: ingestion pipeline and FAISS index manager (§5.4, §5.5, §12.2)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import Engine, text

from app.db.seed import ensure_index_state
from app.ingestion.acl_source import YamlAclBackend
from app.ingestion.pipeline import (
    EmbeddingModelMismatch,
    IngestionPipeline,
    rebuild_index,
)
from app.retrieval.index_manager import (
    FaissIndex,
    IndexHolder,
    WriterLockHeld,
    bump_version,
    writer_lock,
)
from eval.synth.generate_corpus import generate


@pytest.fixture(scope="module")
def mini_corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("mini")
    dirs = {"corpus": root / "corpus", "config": root / "config", "synth": root / "synth"}
    generate(
        seed=5,
        count=12,
        out_dir=dirs["corpus"],
        config_dir=dirs["config"],
        synth_dir=dirs["synth"],
        scanned_frac=0.05,
    )
    return dirs


def _pipeline(engine: Engine, embedder, corpus: dict[str, Path], tmp: Path) -> IngestionPipeline:  # type: ignore[no-untyped-def]
    return IngestionPipeline(
        engine,
        embedder,
        index_path=tmp / "index.faiss",
        document_store=tmp / "store",
        acl_backend=YamlAclBackend(corpus["config"] / "acl_seed.yaml"),
    )


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
            for t in ("documents", "pages", "chunks")
        }


def test_ingest_is_idempotent(app_engine, clean_db, embedder, mini_corpus, tmp_path) -> None:  # type: ignore[no-untyped-def]
    pipe = _pipeline(app_engine, embedder, mini_corpus, tmp_path)
    first = pipe.run(mini_corpus["corpus"])
    assert len(first.processed) == 12 and not first.failed
    assert first.ocr_pages > 0
    assert len(first.empty_acl) >= 1
    counts = _counts(app_engine)
    index = FaissIndex.load(tmp_path / "index.faiss")
    assert index.ntotal == counts["chunks"]

    second = pipe.run(mini_corpus["corpus"])
    assert len(second.skipped) == 12 and not second.processed
    assert _counts(app_engine) == counts
    assert FaissIndex.load(tmp_path / "index.faiss").ntotal == counts["chunks"]
    with app_engine.connect() as conn:
        statuses = set(conn.execute(text("SELECT status FROM documents")).scalars())
        ocr = conn.execute(text("SELECT count(*) FROM documents WHERE ocr_pages <> '{}'")).scalar()
        version = conn.execute(text("SELECT version FROM index_state")).scalar()
    assert statuses == {"indexed"}
    assert ocr >= 1
    assert version == 1  # second run changed nothing


def test_crash_before_index_save_leaves_pending_then_recovers(  # type: ignore[no-untyped-def]
    app_engine, clean_db, embedder, mini_corpus, tmp_path, monkeypatch
) -> None:
    pipe = _pipeline(app_engine, embedder, mini_corpus, tmp_path)

    def boom(self, path):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(FaissIndex, "save_atomic", boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        pipe.run(mini_corpus["corpus"])
    with app_engine.connect() as conn:
        assert set(conn.execute(text("SELECT status FROM documents")).scalars()) == {"pending"}
    assert not (tmp_path / "index.faiss").exists()
    monkeypatch.undo()

    summary = pipe.run(mini_corpus["corpus"])
    assert len(summary.processed) == 12
    counts = _counts(app_engine)
    assert counts["documents"] == 12  # no duplicates
    with app_engine.connect() as conn:
        assert set(conn.execute(text("SELECT status FROM documents")).scalars()) == {"indexed"}
    index = FaissIndex.load(tmp_path / "index.faiss")
    with app_engine.connect() as conn:
        db_ids = set(conn.execute(text("SELECT chunk_id FROM chunks")).scalars())
    assert index.ids() == db_ids


def test_corrupt_pdf_is_marked_failed(app_engine, clean_db, embedder, tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "src"
    src.mkdir()
    (src / "loan_bad.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    acl = tmp_path / "acl.yaml"
    acl.write_text("documents: {}\n")
    pipe = IngestionPipeline(
        app_engine,
        embedder,
        index_path=tmp_path / "i.faiss",
        document_store=None,
        acl_backend=YamlAclBackend(acl),
    )
    summary = pipe.run(src)
    assert summary.failed and summary.failed[0][0] == "loan_bad.pdf"
    with app_engine.connect() as conn:
        row = conn.execute(text("SELECT status, failure_reason FROM documents")).one()
    assert row.status == "failed" and row.failure_reason.startswith("extraction_failed")


def test_rebuild_matches_incremental(app_engine, clean_db, embedder, mini_corpus, tmp_path) -> None:  # type: ignore[no-untyped-def]
    pipe = _pipeline(app_engine, embedder, mini_corpus, tmp_path)
    pipe.run(mini_corpus["corpus"])
    incremental = FaissIndex.load(tmp_path / "index.faiss")
    queries = [
        "escrow disbursement",
        "interest rate lock expiration",
        "HOA dues",
        "prepayment penalty",
        "conditions before funding",
    ]
    before = [incremental.search(embedder.embed_query(q), 10) for q in queries]
    shutil.copy(tmp_path / "index.faiss", tmp_path / "incremental.faiss")
    count, version = rebuild_index(app_engine, embedder, tmp_path / "index.faiss")
    rebuilt = FaissIndex.load(tmp_path / "index.faiss")
    assert count == incremental.ntotal
    assert version == 2
    after = [rebuilt.search(embedder.embed_query(q), 10) for q in queries]
    assert before == after


def test_model_mismatch_blocks_ingest(
    app_engine, clean_db, embedder, mini_corpus, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    with app_engine.begin() as conn:
        ensure_index_state(conn, "some/other-model")
    with pytest.raises(EmbeddingModelMismatch):
        _pipeline(app_engine, embedder, mini_corpus, tmp_path).run(mini_corpus["corpus"])


def test_writer_lock_is_exclusive(app_engine) -> None:  # type: ignore[no-untyped-def]
    with writer_lock(app_engine), pytest.raises(WriterLockHeld), writer_lock(app_engine):
        pass
    with writer_lock(app_engine):  # released again
        pass


def test_index_add_remove_save_reload(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(5, 8)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    idx = FaissIndex.new(8)
    idx.add([10, 11, 12, 13, 14], vecs)
    assert idx.search(vecs[2], 1) == [12]
    assert idx.remove([12]) == 1
    assert 12 not in idx.search(vecs[2], 5)
    idx.save_atomic(tmp_path / "a.faiss")
    assert not list(tmp_path.glob(".*tmp*"))
    loaded = FaissIndex.load(tmp_path / "a.faiss")
    assert loaded.ids() == {10, 11, 13, 14}


def test_holder_reloads_after_version_change(app_engine, clean_db, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "h.faiss"
    with app_engine.begin() as conn:
        ensure_index_state(conn, "BAAI/bge-small-en-v1.5")
    holder = IndexHolder(path, 8)
    with app_engine.connect() as conn:
        holder.ensure_current(conn)
    assert holder.version == 0 and holder.ntotal() == 0

    idx = FaissIndex.new(8)
    v = np.ones((1, 8), dtype=np.float32) / np.sqrt(8)
    idx.add([42], v)
    idx.save_atomic(path)
    with app_engine.begin() as conn:
        bump_version(conn)
    with app_engine.connect() as conn:
        holder.ensure_current(conn)
    assert holder.version == 1 and holder.search(v[0], 1) == [42]
    reloads = holder.reloads
    with app_engine.connect() as conn:
        holder.ensure_current(conn)
    assert holder.reloads == reloads  # no reload when unchanged
