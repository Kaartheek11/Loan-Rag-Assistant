"""Upload worker (extension.md BE-9, §6.1): turns queued uploads into indexed documents.

    python -m app.worker

Loop:
  1. Claim up to 5 queued jobs (SELECT ... FOR UPDATE SKIP LOCKED) and mark them processing.
  2. Take the index writer lock (task.md §5.5); while the CLI holds it, wait and retry.
  3. Run the same pipeline steps as the CLI (extract, OCR, clean, chunk with the context
     prefix, embed), with the ACL taken from the uploaded document's row.
  4. Update pages_done and heartbeat_at every 5 pages.
  5. Save the index atomically once per batch, then mark documents and jobs indexed and bump
     index_state.version in one transaction.
  6. On failure: job and document `failed` with a fixed reason; the stored file is kept for
     retry; details go to server logs only.
  7. On start and every minute, jobs stuck in `processing` (heartbeat older than 5 minutes)
     go back to `queued`.
"""

from __future__ import annotations

import signal
import time
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, select, text, update

from app.db.seed import ensure_index_state
from app.db.tables import documents, upload_jobs
from app.ingestion.extract import ExtractionError
from app.ingestion.pipeline import (
    check_embedding_model,
    delete_content,
    embed_into,
    prepare_document,
    write_content,
)
from app.retrieval.embedder import Embedder
from app.retrieval.index_manager import FaissIndex, WriterLockHeld, bump_version, writer_lock
from app.security import permissions

log = structlog.get_logger(__name__)

BATCH_SIZE = 5
PROGRESS_EVERY_PAGES = 5
STALE_AFTER = "5 minutes"
RECOVER_EVERY_SECONDS = 60.0
INTERNAL_ERROR = "internal_error"

_CLAIM_SQL = text(
    """
    UPDATE upload_jobs SET status = 'processing', heartbeat_at = now()
    WHERE job_id IN (
        SELECT job_id FROM upload_jobs WHERE status = 'queued'
        ORDER BY created_at
        LIMIT :n
        FOR UPDATE SKIP LOCKED
    )
    RETURNING job_id, doc_id, pages_total
    """
)
_RECOVER_SQL = text(
    f"""
    UPDATE upload_jobs SET status = 'queued', heartbeat_at = NULL
    WHERE status = 'processing'
      AND (heartbeat_at IS NULL OR heartbeat_at < now() - interval '{STALE_AFTER}')
    RETURNING job_id
    """
)


@dataclass
class Job:
    job_id: uuid.UUID
    doc_id: uuid.UUID
    pages_total: int


class UploadWorker:
    def __init__(
        self,
        engine: Engine,
        embedder: Embedder,
        *,
        index_path: Path,
        store_dir: Path,
        lock_wait_seconds: float = 5.0,
    ) -> None:
        self.engine = engine
        self.embedder = embedder
        self.index_path = Path(index_path)
        self.store_dir = Path(store_dir)
        self.lock_wait_seconds = lock_wait_seconds
        self._stop = False

    # -- job bookkeeping ---------------------------------------------------------------
    def recover_stale(self) -> int:
        with self.engine.begin() as conn:
            ids = list(conn.execute(_RECOVER_SQL).scalars())
        if ids:
            log.warning("upload_jobs_recovered", count=len(ids))
        return len(ids)

    def claim(self) -> list[Job]:
        with self.engine.begin() as conn:
            rows = conn.execute(_CLAIM_SQL, {"n": BATCH_SIZE}).all()
        return [Job(r.job_id, r.doc_id, int(r.pages_total)) for r in rows]

    def heartbeat(self, jobs: list[Job], pages_done: dict[uuid.UUID, int] | None = None) -> None:
        with self.engine.begin() as conn:
            for job in jobs:
                values: dict[str, Any] = {"heartbeat_at": text("now()")}
                if pages_done and job.job_id in pages_done:
                    values["pages_done"] = pages_done[job.job_id]
                conn.execute(
                    update(upload_jobs).where(upload_jobs.c.job_id == job.job_id).values(**values)
                )

    def fail(self, job: Job, reason: str | None) -> None:
        with self.engine.begin() as conn:
            delete_content(conn, job.doc_id)
            conn.execute(
                update(upload_jobs)
                .where(upload_jobs.c.job_id == job.job_id)
                .values(
                    status="failed",
                    failure_reason=reason,
                    finished_at=text("now()"),
                    heartbeat_at=text("now()"),
                )
            )
            conn.execute(
                update(documents)
                .where(documents.c.doc_id == job.doc_id, documents.c.deleted_at.is_(None))
                .values(status="failed", failure_reason=reason)
            )

    # -- processing -------------------------------------------------------------------
    def run_once(self) -> int:
        """Claim and process one batch. Returns the number of jobs claimed."""
        jobs = self.claim()
        if not jobs:
            return 0
        while True:
            try:
                with writer_lock(self.engine, wait_seconds=self.lock_wait_seconds):
                    self._process_batch(jobs)
                return len(jobs)
            except WriterLockHeld:
                # The CLI (or a delete) is writing the index: keep our claim alive and wait.
                self.heartbeat(jobs)
                if self._stop:
                    return len(jobs)

    def _process_batch(self, jobs: list[Job]) -> None:
        with self.engine.begin() as conn:
            ensure_index_state(conn, self.embedder.model_name)
            check_embedding_model(conn, self.embedder.model_name)
        index = FaissIndex.load_or_new(self.index_path, self.embedder.dim)
        done: list[Job] = []
        for job in jobs:
            added: list[int] = []
            try:
                with self.engine.connect() as conn:
                    deleted = conn.execute(
                        select(documents.c.deleted_at).where(documents.c.doc_id == job.doc_id)
                    ).scalar()
                if deleted is not None:  # deleted while queued: nothing to index
                    self.fail(job, None)
                    continue
                prepared = prepare_document(
                    self.store_dir / f"{job.doc_id}.pdf",
                    self.embedder.count_tokens,
                    on_page=self._progress(job),
                )
                with self.engine.begin() as conn:
                    old_ids = delete_content(conn, job.doc_id)  # leftovers from a crash
                    acl = permissions.document_acl(conn, str(job.doc_id))
                    if acl is None or acl.is_empty:
                        raise RuntimeError("upload has no ACL")
                    added = write_content(conn, job.doc_id, prepared, acl)
                    conn.execute(
                        update(documents)
                        .where(documents.c.doc_id == job.doc_id)
                        .values(
                            page_count=len(prepared.pages),
                            ocr_pages=prepared.ocr_pages,
                            loan_number=prepared.fields.loan_number,
                            borrower_name=prepared.fields.borrower_name,
                            failure_reason=None,
                        )
                    )
                index.remove(old_ids)
                embed_into(
                    index, self.embedder, added, prepared, tick=partial(self.heartbeat, [job])
                )
                done.append(job)
            except ExtractionError as exc:
                index.remove(added)
                log.warning("upload_failed", job_id=str(job.job_id), reason=exc.reason)
                self.fail(job, exc.reason)
            except Exception as exc:  # noqa: BLE001 - details in server logs only
                index.remove(added)
                log.error(
                    "upload_failed",
                    job_id=str(job.job_id),
                    reason=INTERNAL_ERROR,
                    error_type=type(exc).__name__,
                )
                self.fail(job, INTERNAL_ERROR)
        if not done:
            return
        index.save_atomic(self.index_path)
        with self.engine.begin() as conn:
            ids = [j.doc_id for j in done]
            conn.execute(
                update(documents)
                .where(documents.c.doc_id.in_(ids), documents.c.status == "pending")
                .values(status="indexed")
            )
            conn.execute(
                update(upload_jobs)
                .where(upload_jobs.c.job_id.in_([j.job_id for j in done]))
                .values(
                    status="indexed",
                    pages_done=upload_jobs.c.pages_total,
                    finished_at=text("now()"),
                    heartbeat_at=text("now()"),
                )
            )
            version = bump_version(conn)
        log.info("uploads_indexed", count=len(done), index_version=version)

    def _progress(self, job: Job) -> Any:
        def on_page(page: int, total: int) -> None:
            if page % PROGRESS_EVERY_PAGES == 0 or page == total:
                self.heartbeat([job], {job.job_id: page})

        return on_page

    # -- main loop --------------------------------------------------------------------
    def run_forever(self, poll_seconds: float = 2.0) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        self.recover_stale()
        last_recover = time.monotonic()
        log.info("worker_started")
        while not self._stop:
            if time.monotonic() - last_recover >= RECOVER_EVERY_SECONDS:
                self.recover_stale()
                last_recover = time.monotonic()
            if self.run_once() == 0:
                time.sleep(poll_seconds)

    def stop(self) -> None:
        self._stop = True


def main() -> None:
    from app.config import get_settings
    from app.db.session import make_engine
    from app.security.redaction import configure_logging

    settings = get_settings()
    configure_logging(settings.secret_values())
    worker = UploadWorker(
        make_engine(settings.database_url, environment=settings.environment),
        Embedder(settings.embedding_model, device=settings.embedding_device),
        index_path=Path(settings.faiss_index_path),
        store_dir=Path(settings.document_store_path),
    )
    worker.run_forever()


if __name__ == "__main__":
    main()
