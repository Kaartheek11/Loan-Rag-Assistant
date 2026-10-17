"""Ingestion orchestration (§5.4) and index rebuild.

Ordering guarantees:
  1. DB rows (document `pending`, pages, chunks with ACL copies) are written in one transaction.
  2. Vectors are added to an in-memory copy of the index.
  3. After all files: the index is saved atomically, THEN documents flip to `indexed` and
     index_state.version is bumped, in one transaction.
A crash between 1 and 3 leaves documents `pending` (invisible) and they are redone next run.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import Connection, Engine, delete, insert, select, text, update

from app.db.seed import ensure_index_state, read_index_state
from app.db.tables import chunks, documents, pages
from app.ingestion.acl_source import AclBackend, get_document_acl
from app.ingestion.chunk import chunk_pages
from app.ingestion.clean import clean_pages
from app.ingestion.extract import ExtractionError, extract_pdf
from app.retrieval.embedder import BATCH_SIZE, Embedder
from app.retrieval.identifiers import extract_identifiers
from app.retrieval.index_manager import FaissIndex, bump_version, writer_lock
from app.security.permissions import acl_columns

log = structlog.get_logger(__name__)


class EmbeddingModelMismatch(RuntimeError):
    pass


@dataclass
class IngestSummary:
    processed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    empty_acl: list[str] = field(default_factory=list)
    chunks_created: int = 0
    ocr_pages: int = 0
    index_version: int | None = None

    def render(self) -> str:
        lines = [
            f"processed: {len(self.processed)}",
            f"skipped (already indexed): {len(self.skipped)}",
            f"failed: {len(self.failed)}",
        ]
        lines += [f"  - {name}: {reason}" for name, reason in self.failed]
        lines.append(f"empty-ACL warnings (invisible to everyone): {len(self.empty_acl)}")
        lines += [f"  - {name}" for name in self.empty_acl]
        lines += [
            f"chunks created: {self.chunks_created}",
            f"OCR pages: {self.ocr_pages}",
            f"index version: {self.index_version}",
        ]
        return "\n".join(lines)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_embedding_model(conn: Connection, configured: str) -> None:
    state = read_index_state(conn)
    if state is not None and state[1] != configured:
        raise EmbeddingModelMismatch(
            f"index was built with {state[1]!r} but EMBEDDING_MODEL is {configured!r}; "
            "run `python -m app.cli rebuild-index`"
        )


class IngestionPipeline:
    def __init__(
        self,
        engine: Engine,
        embedder: Embedder,
        *,
        index_path: Path,
        document_store: Path | None,
        acl_backend: AclBackend,
    ) -> None:
        self.engine = engine
        self.embedder = embedder
        self.index_path = Path(index_path)
        self.document_store = Path(document_store) if document_store else None
        self.acl_backend = acl_backend

    def run(self, source_dir: Path) -> IngestSummary:
        summary = IngestSummary()
        with writer_lock(self.engine):
            with self.engine.begin() as conn:
                ensure_index_state(conn, self.embedder.model_name)
                check_embedding_model(conn, self.embedder.model_name)
            index = FaissIndex.load_or_new(self.index_path, self.embedder.dim)
            new_doc_ids: list[uuid.UUID] = []
            for pdf in sorted(Path(source_dir).glob("*.pdf")):
                doc_id = self._process(pdf, index, summary)
                if doc_id is not None:
                    new_doc_ids.append(doc_id)
            if new_doc_ids:
                index.save_atomic(self.index_path)
                with self.engine.begin() as conn:
                    conn.execute(
                        update(documents)
                        .where(documents.c.doc_id.in_(new_doc_ids))
                        .where(documents.c.status == "pending")
                        .values(status="indexed")
                    )
                    summary.index_version = bump_version(conn)
            else:
                with self.engine.connect() as conn:
                    state = read_index_state(conn)
                    summary.index_version = state[0] if state else None
        log.info(
            "ingest_complete",
            processed=len(summary.processed),
            skipped=len(summary.skipped),
            failed=len(summary.failed),
            chunks=summary.chunks_created,
        )
        return summary

    # -----------------------------------------------------------------------------------
    def _process(self, pdf: Path, index: FaissIndex, summary: IngestSummary) -> uuid.UUID | None:
        digest = sha256_file(pdf)
        with self.engine.connect() as conn:
            existing = conn.execute(
                select(documents.c.doc_id, documents.c.status).where(documents.c.sha256 == digest)
            ).first()
            old_chunk_ids: list[int] = []
            if existing is not None:
                if existing.status == "indexed":
                    summary.skipped.append(pdf.name)
                    return None
                old_chunk_ids = list(
                    conn.execute(
                        select(chunks.c.chunk_id).where(chunks.c.doc_id == existing.doc_id)
                    ).scalars()
                )
        doc_id = existing.doc_id if existing is not None else uuid.uuid4()
        index.remove(old_chunk_ids)  # leftovers from a crashed run; harmless but tidy

        try:
            extracted = extract_pdf(pdf)
        except ExtractionError as exc:
            self._record_failure(doc_id, pdf, digest, f"extraction_failed:{exc}", existing)
            summary.failed.append((pdf.name, f"extraction_failed:{exc}"))
            return None

        cleaned = clean_pages(extracted.pages)
        doc_chunks = chunk_pages(cleaned, self.embedder.count_tokens)
        acl = get_document_acl(pdf.name, self.acl_backend)
        if acl.is_empty:
            summary.empty_acl.append(pdf.name)
        acl_cols = acl_columns(acl)

        with self.engine.begin() as conn:
            if existing is not None:
                self._delete_rows(conn, doc_id)
            conn.execute(
                insert(documents).values(
                    doc_id=doc_id,
                    filename=pdf.name,
                    sha256=digest,
                    page_count=len(cleaned),
                    ocr_pages=extracted.ocr_pages,
                    status="pending",
                    failure_reason=None,
                    ingested_at=text("now()"),
                    **acl_cols,
                )
            )
            if cleaned:
                conn.execute(
                    insert(pages),
                    [{"doc_id": doc_id, "page": i, "text": t} for i, t in enumerate(cleaned, 1)],
                )
            chunk_ids: list[int] = []
            if doc_chunks:
                rows = conn.execute(
                    insert(chunks).returning(chunks.c.chunk_id, sort_by_parameter_order=True),
                    [
                        {
                            "doc_id": doc_id,
                            "page": c.page,
                            "section": c.section,
                            "text": c.text,
                            "token_count": c.token_count,
                            "identifiers": extract_identifiers(c.text),
                            **acl_cols,
                        }
                        for c in doc_chunks
                    ],
                )
                chunk_ids = [int(r.chunk_id) for r in rows]

        try:
            vectors = self.embedder.embed_passages([c.text for c in doc_chunks], BATCH_SIZE)
            index.add(chunk_ids, vectors)
        except Exception as exc:
            index.remove(chunk_ids)
            with self.engine.begin() as conn:
                conn.execute(
                    update(documents)
                    .where(documents.c.doc_id == doc_id)
                    .values(
                        status="failed", failure_reason=f"embedding_failed:{type(exc).__name__}"
                    )
                )
            summary.failed.append((pdf.name, f"embedding_failed:{type(exc).__name__}"))
            return None

        if self.document_store is not None:
            self.document_store.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pdf, self.document_store / f"{doc_id}.pdf")
        summary.processed.append(pdf.name)
        summary.chunks_created += len(chunk_ids)
        summary.ocr_pages += len(extracted.ocr_pages)
        return doc_id

    @staticmethod
    def _delete_rows(conn: Connection, doc_id: uuid.UUID) -> None:
        conn.execute(delete(chunks).where(chunks.c.doc_id == doc_id))
        conn.execute(delete(pages).where(pages.c.doc_id == doc_id))
        conn.execute(delete(documents).where(documents.c.doc_id == doc_id))

    def _record_failure(
        self, doc_id: uuid.UUID, pdf: Path, digest: str, reason: str, existing: object
    ) -> None:
        with self.engine.begin() as conn:
            if existing is not None:
                self._delete_rows(conn, doc_id)
            conn.execute(
                insert(documents).values(
                    doc_id=doc_id,
                    filename=pdf.name,
                    sha256=digest,
                    page_count=0,
                    ocr_pages=[],
                    status="failed",
                    failure_reason=reason[:200],
                    ingested_at=text("now()"),
                )
            )


def rebuild_index(engine: Engine, embedder: Embedder, index_path: Path) -> tuple[int, int]:
    """Regenerate the index from the chunks table (Postgres is the source of truth).

    Returns (vector_count, new_version). Also records the embedding model in index_state.
    """
    with writer_lock(engine):
        with engine.begin() as conn:
            ensure_index_state(conn, embedder.model_name)
        index = FaissIndex.new(embedder.dim)
        sql = text(
            """
            SELECT c.chunk_id, c.text
            FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.status = 'indexed' AND d.deleted_at IS NULL AND c.deleted_at IS NULL
            ORDER BY c.chunk_id
            """
        )
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=1024).execute(sql)
            for part in result.partitions():
                ids = [int(r.chunk_id) for r in part]
                index.add(ids, embedder.embed_passages([str(r.text) for r in part]))
        index.save_atomic(index_path)
        with engine.begin() as conn:
            version = bump_version(conn, embedding_model=embedder.model_name)
    return index.ntotal, version
