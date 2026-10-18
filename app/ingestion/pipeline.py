"""Ingestion orchestration (task.md §5.4, extension.md §17.2) and index rebuild.

Ordering guarantees:
  1. DB rows (document `pending`, pages, chunks with ACL copies) are written in one transaction.
  2. Vectors are added to an in-memory copy of the index.
  3. After all files: the index is saved atomically, THEN documents flip to `indexed` and
     index_state.version is bumped, in one transaction.
A crash between 1 and 3 leaves documents `pending` (invisible) and they are redone next run.

The steps are reusable: the CLI passes each file's ACL from acl_seed.yaml; the upload worker
(extension.md §6.1) passes the ACL stored on the uploaded document's row.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import structlog
from sqlalchemy import Connection, Engine, delete, insert, select, text, update

from app.db.seed import ensure_index_state, read_index_state
from app.db.tables import chunks, documents, pages, upload_jobs
from app.ingestion.acl_source import AclBackend, get_document_acl
from app.ingestion.chunk import CHUNK_TOKENS, chunk_pages
from app.ingestion.clean import clean_pages
from app.ingestion.context import (
    SECTION_TOKEN_ALLOWANCE,
    DocumentFields,
    build_prefix,
    embedding_text,
    extract_document_fields,
)
from app.ingestion.extract import ExtractionError, extract_pdf
from app.ingestion.progress import DEFAULT_INTERVAL, Progress
from app.retrieval.embedder import BATCH_SIZE, Embedder
from app.retrieval.identifiers import extract_identifiers
from app.retrieval.index_manager import FaissIndex, bump_version, writer_lock
from app.security.permissions import Acl, acl_columns

log = structlog.get_logger(__name__)
MIN_BODY_TOKENS = 100

TokenCounter = Callable[[str], int]


class EmbeddingModelMismatch(RuntimeError):
    pass


@dataclass
class PreparedChunk:
    page: int
    section: str | None
    text: str
    context_prefix: str
    token_count: int
    identifiers: list[str]


@dataclass
class PreparedDocument:
    pages: list[str]  # cleaned page text, used for the pages table and click-through
    ocr_pages: list[int]
    fields: DocumentFields
    chunks: list[PreparedChunk]


@dataclass
class IngestSummary:
    processed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    empty_acl: list[str] = field(default_factory=list)
    missing_fields: list[tuple[str, list[str]]] = field(default_factory=list)
    chunks_created: int = 0
    pages_processed: int = 0
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
        lines.append(
            f"document-field warnings (not found in first 5 pages): {len(self.missing_fields)}"
        )
        lines += [f"  - {name}: {', '.join(missing)}" for name, missing in self.missing_fields]
        lines += [
            f"pages processed: {self.pages_processed}",
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


# ---------------------------------------------------------------------------------------
# Reusable steps (CLI pipeline and upload worker)
# ---------------------------------------------------------------------------------------
def prepare_document(
    path: Path, count_tokens: TokenCounter, on_page: Callable[[int, int], None] | None = None
) -> PreparedDocument:
    """Extract, clean, find document fields, chunk, and build context prefixes.

    Raises ExtractionError with a fixed reason (unreadable_pdf, ocr_failed, no_text_found).
    """
    extracted = extract_pdf(path, on_page=on_page)
    fields = extract_document_fields(extracted.pages)  # raw text: header lines included
    cleaned = clean_pages(extracted.pages)
    # The 400-token target includes the prefix (§17.2): reserve room for it.
    reserve = (
        count_tokens(build_prefix(fields, None))
        + count_tokens(" | Section: ")
        + SECTION_TOKEN_ALLOWANCE
        + 1
    )
    body_size = max(MIN_BODY_TOKENS, CHUNK_TOKENS - reserve)
    prepared: list[PreparedChunk] = []
    for c in chunk_pages(cleaned, count_tokens, size=body_size):
        prefix = build_prefix(fields, c.section)
        idents = set(extract_identifiers(c.text))
        if fields.loan_number:
            idents.add(fields.loan_number)
        prepared.append(
            PreparedChunk(
                page=c.page,
                section=c.section,
                text=c.text,
                context_prefix=prefix,
                token_count=count_tokens(embedding_text(prefix, c.text)),
                identifiers=sorted(idents),
            )
        )
    return PreparedDocument(
        pages=cleaned, ocr_pages=extracted.ocr_pages, fields=fields, chunks=prepared
    )


# Process-pool helpers: workers load only the tokenizer (same one the embedder uses), so token
# counts match; extraction, OCR, cleaning, and chunking then run in parallel.
_worker_count: TokenCounter | None = None


def _init_prepare_worker(model_name: str) -> None:
    global _worker_count
    from transformers import AutoTokenizer

    import app  # noqa: F401  # macOS OpenMP guard before any native library loads

    tokenizer = AutoTokenizer.from_pretrained(model_name)

    def count(text: str) -> int:
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])

    _worker_count = count


def _prepare_in_worker(path: Path) -> PreparedDocument | str:
    """Returns the prepared document, or a fixed extraction failure reason."""
    assert _worker_count is not None
    try:
        return prepare_document(path, _worker_count)
    except ExtractionError as exc:
        return exc.reason


def write_content(
    conn: Connection, doc_id: uuid.UUID, prepared: PreparedDocument, acl: Acl
) -> list[int]:
    """Insert pages and chunks (with the ACL copied onto each chunk). Returns chunk IDs."""
    acl_cols = acl_columns(acl)
    if prepared.pages:
        conn.execute(
            insert(pages),
            [{"doc_id": doc_id, "page": i, "text": t} for i, t in enumerate(prepared.pages, 1)],
        )
    if not prepared.chunks:
        return []
    rows = conn.execute(
        insert(chunks).returning(chunks.c.chunk_id, sort_by_parameter_order=True),
        [
            {
                "doc_id": doc_id,
                "page": c.page,
                "section": c.section,
                "text": c.text,
                "context_prefix": c.context_prefix,
                "token_count": c.token_count,
                "identifiers": c.identifiers,
                **acl_cols,
            }
            for c in prepared.chunks
        ],
    )
    return [int(r.chunk_id) for r in rows]


def embed_into(
    index: FaissIndex,
    embedder: Embedder,
    chunk_ids: list[int],
    prepared: PreparedDocument,
    tick: Callable[[], None] | None = None,
) -> None:
    """Embed prefix + text for each chunk and add the vectors under their chunk IDs."""
    texts = [embedding_text(c.context_prefix, c.text) for c in prepared.chunks]
    for start in range(0, len(texts), BATCH_SIZE * 4):
        vectors = embedder.embed_passages(texts[start : start + BATCH_SIZE * 4], BATCH_SIZE)
        index.add(chunk_ids[start : start + BATCH_SIZE * 4], vectors)
        if tick is not None:
            tick()


def delete_content(conn: Connection, doc_id: uuid.UUID) -> list[int]:
    """Remove a document's pages and chunks (for reprocessing). Returns removed chunk IDs."""
    ids = list(conn.execute(select(chunks.c.chunk_id).where(chunks.c.doc_id == doc_id)).scalars())
    conn.execute(delete(chunks).where(chunks.c.doc_id == doc_id))
    conn.execute(delete(pages).where(pages.c.doc_id == doc_id))
    return [int(i) for i in ids]


# ---------------------------------------------------------------------------------------
# CLI pipeline
# ---------------------------------------------------------------------------------------
class IngestionPipeline:
    def __init__(
        self,
        engine: Engine,
        embedder: Embedder,
        *,
        index_path: Path,
        document_store: Path | None,
        acl_backend: AclBackend,
        progress_stream: TextIO | None = None,
        progress_interval: float | None = None,
        workers: int = 1,
    ) -> None:
        self.engine = engine
        self.embedder = embedder
        self.index_path = Path(index_path)
        self.document_store = Path(document_store) if document_store else None
        self.acl_backend = acl_backend
        self.progress_stream = progress_stream
        self.progress_interval = progress_interval
        self.workers = max(1, workers)
        self.progress: Progress | None = None

    def run(self, source_dir: Path) -> IngestSummary:
        summary = IngestSummary()
        files = sorted(Path(source_dir).glob("*.pdf"))
        interval = DEFAULT_INTERVAL if self.progress_interval is None else self.progress_interval
        self.progress = Progress(len(files), stream=self.progress_stream, interval=interval)
        with writer_lock(self.engine):
            with self.engine.begin() as conn:
                ensure_index_state(conn, self.embedder.model_name)
                check_embedding_model(conn, self.embedder.model_name)
            index = FaissIndex.load_or_new(self.index_path, self.embedder.dim)
            new_doc_ids: list[uuid.UUID] = []
            todo: list[tuple[Path, str, object, list[int]]] = []
            for pdf in files:
                found = self._lookup(pdf, summary)
                if found is None:  # already indexed
                    self.progress.doc()
                else:
                    todo.append((pdf, *found))
            for (pdf, digest, existing, old_ids), result in zip(
                todo, self._prepared(p for p, *_ in todo), strict=True
            ):
                acl = get_document_acl(pdf.name, self.acl_backend)  # CLI: ACL from YAML
                index.remove(old_ids)  # leftovers from a crashed run; harmless but tidy
                doc_id = self._store(pdf, digest, existing, result, acl, index, summary)
                if doc_id is not None:
                    new_doc_ids.append(doc_id)
                self.progress.doc()
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
        self.progress.finish()
        log.info(
            "ingest_complete",
            processed=len(summary.processed),
            skipped=len(summary.skipped),
            failed=len(summary.failed),
            chunks=summary.chunks_created,
        )
        return summary

    def _lookup(self, pdf: Path, summary: IngestSummary) -> tuple[str, object, list[int]] | None:
        """(sha256, existing row or None, old chunk IDs), or None when already indexed."""
        digest = sha256_file(pdf)
        with self.engine.connect() as conn:
            # The live row wins; a soft-deleted match is still "indexed" and stays skipped,
            # so the CLI never resurrects a deleted document (uploads may re-add it, §6.1).
            existing = conn.execute(
                select(documents.c.doc_id, documents.c.status)
                .where(documents.c.sha256 == digest)
                .order_by(documents.c.deleted_at.is_not(None), documents.c.ingested_at.desc())
            ).first()
            if existing is None:
                return digest, None, []
            if existing.status == "indexed":
                summary.skipped.append(pdf.name)
                return None
            # An uploaded file is owned by the upload worker (extension.md §6.1).
            uploaded = conn.execute(
                select(upload_jobs.c.job_id).where(upload_jobs.c.doc_id == existing.doc_id).limit(1)
            ).first()
            if uploaded is not None:
                summary.skipped.append(pdf.name)
                return None
            old_ids = list(
                conn.execute(
                    select(chunks.c.chunk_id).where(chunks.c.doc_id == existing.doc_id)
                ).scalars()
            )
        return digest, existing, [int(i) for i in old_ids]

    def _prepared(self, paths: Iterator[Path]) -> Iterator[PreparedDocument | str]:
        """Prepared documents in input order; in parallel when workers > 1."""
        progress = self.progress
        if self.workers == 1:
            for path in paths:
                try:
                    yield prepare_document(
                        path,
                        self.embedder.count_tokens,
                        on_page=(lambda i, n: progress.page()) if progress else None,
                    )
                except ExtractionError as exc:
                    yield exc.reason
            return
        # Each worker runs single-threaded (Tesseract/OpenMP, HF tokenizers): N workers with
        # many threads each oversubscribe the CPU and run ~10x slower. Spawned children
        # inherit these variables; torch in this process keeps the remaining cores.
        for var in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS"):
            os.environ[var] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        self.embedder.limit_threads(max(1, (os.cpu_count() or 2) - self.workers))
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            self.workers,
            mp_context=ctx,
            initializer=_init_prepare_worker,
            initargs=(self.embedder.model_name,),
        ) as pool:
            for result in pool.map(_prepare_in_worker, paths, chunksize=1):
                if progress is not None and isinstance(result, PreparedDocument):
                    progress.page(len(result.pages))
                yield result

    def _store(
        self,
        pdf: Path,
        digest: str,
        existing: object,
        result: PreparedDocument | str,
        acl: Acl,
        index: FaissIndex,
        summary: IngestSummary,
    ) -> uuid.UUID | None:
        doc_id: uuid.UUID = existing.doc_id if existing is not None else uuid.uuid4()  # type: ignore[attr-defined]
        if isinstance(result, str):
            self._record_failure(doc_id, pdf, digest, f"extraction_failed:{result}", existing)
            summary.failed.append((pdf.name, f"extraction_failed:{result}"))
            return None
        prepared = result
        if acl.is_empty:
            summary.empty_acl.append(pdf.name)
        if prepared.fields.missing:
            summary.missing_fields.append((pdf.name, prepared.fields.missing))

        with self.engine.begin() as conn:
            if existing is not None:
                delete_content(conn, doc_id)
                conn.execute(delete(documents).where(documents.c.doc_id == doc_id))
            conn.execute(
                insert(documents).values(
                    doc_id=doc_id,
                    filename=pdf.name,
                    sha256=digest,
                    page_count=len(prepared.pages),
                    ocr_pages=prepared.ocr_pages,
                    status="pending",
                    failure_reason=None,
                    ingested_at=text("now()"),
                    loan_number=prepared.fields.loan_number,
                    borrower_name=prepared.fields.borrower_name,
                    **acl_columns(acl),
                )
            )
            chunk_ids = write_content(conn, doc_id, prepared, acl)

        progress = self.progress
        try:
            embed_into(
                index, self.embedder, chunk_ids, prepared, tick=progress.tick if progress else None
            )
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
        summary.pages_processed += len(prepared.pages)
        summary.ocr_pages += len(prepared.ocr_pages)
        return doc_id

    def _record_failure(
        self, doc_id: uuid.UUID, pdf: Path, digest: str, reason: str, existing: object
    ) -> None:
        with self.engine.begin() as conn:
            if existing is not None:
                delete_content(conn, doc_id)
                conn.execute(delete(documents).where(documents.c.doc_id == doc_id))
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


def rebuild_index(
    engine: Engine,
    embedder: Embedder,
    index_path: Path,
    *,
    use_prefix: bool = True,
) -> tuple[int, int | None]:
    """Regenerate an index from the chunks table (Postgres is the source of truth).

    With use_prefix (the default) this rebuilds the live index: it records the embedding model
    in index_state and bumps the version. With use_prefix=False it writes an evaluation-only
    index of raw chunk text to `index_path` (extension.md §17.5) and leaves index_state alone.
    Returns (vector_count, new_version or None).
    """
    sql = text(
        """
        SELECT c.chunk_id, c.text, c.context_prefix
        FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
        WHERE d.status = 'indexed' AND d.deleted_at IS NULL AND c.deleted_at IS NULL
        ORDER BY c.chunk_id
        """
    )

    def build() -> FaissIndex:
        index = FaissIndex.new(embedder.dim)
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=1024).execute(sql)
            for part in result.partitions():
                ids = [int(r.chunk_id) for r in part]
                texts = [
                    embedding_text(str(r.context_prefix), str(r.text))
                    if use_prefix
                    else str(r.text)
                    for r in part
                ]
                index.add(ids, embedder.embed_passages(texts))
        return index

    if not use_prefix:
        index = build()
        index.save_atomic(index_path)
        return index.ntotal, None
    with writer_lock(engine):
        with engine.begin() as conn:
            ensure_index_state(conn, embedder.model_name)
        index = build()
        index.save_atomic(index_path)
        with engine.begin() as conn:
            version = bump_version(conn, embedding_model=embedder.model_name)
    return index.ntotal, version
