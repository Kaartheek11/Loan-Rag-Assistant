"""Admin operations that change documents or the index (§6.3, §6.7)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, select, text

from app.db.tables import documents
from app.retrieval.index_manager import FaissIndex, bump_version, writer_lock
from app.security import permissions
from app.security.permissions import Acl

log = structlog.get_logger(__name__)
DELETE_LOCK_WAIT_SECONDS = 10.0


def _uuid(doc_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(doc_id)
    except (ValueError, TypeError):
        return None


def update_acl(engine: Engine, doc_id: str, acl: Acl) -> bool:
    """Replace the ACL on the document and all of its chunks in ONE transaction (F16, T4)."""
    with engine.begin() as conn:
        return permissions.replace_document_acl(conn, doc_id, acl)


def delete_document(engine: Engine, doc_id: str, index_path: Path, dim: int) -> bool:
    """Soft delete (the security guarantee), then remove vectors (cleanup). §6.7.

    Returns False if the document does not exist or is already deleted.
    Raises WriterLockHeld if the writer lock cannot be acquired.
    """
    parsed = _uuid(doc_id)
    if parsed is None:
        return False
    with writer_lock(engine, wait_seconds=DELETE_LOCK_WAIT_SECONDS):
        with engine.begin() as conn:
            res = conn.execute(
                text(
                    "UPDATE documents SET deleted_at = now() "
                    "WHERE doc_id = :d AND deleted_at IS NULL"
                ),
                {"d": parsed},
            )
            if res.rowcount == 0:
                return False
            ids = [
                int(r[0])
                for r in conn.execute(
                    text(
                        "UPDATE chunks SET deleted_at = now() WHERE doc_id = :d RETURNING chunk_id"
                    ),
                    {"d": parsed},
                )
            ]
        try:
            index = FaissIndex.load_or_new(index_path, dim)
            index.remove(ids)
            index.save_atomic(index_path)
            with engine.begin() as conn:
                bump_version(conn)
        except Exception as exc:  # noqa: BLE001 - soft delete already protects the data
            log.warning("vector_removal_failed", doc_id=str(parsed), error_type=type(exc).__name__)
    return True


def list_documents(engine: Engine, status: str | None) -> list[dict[str, Any]]:
    stmt = select(
        documents.c.doc_id,
        documents.c.filename,
        documents.c.status,
        documents.c.failure_reason,
        documents.c.page_count,
    ).where(documents.c.deleted_at.is_(None))
    if status:
        stmt = stmt.where(documents.c.status == status)
    with engine.connect() as conn:
        rows = conn.execute(stmt.order_by(documents.c.filename)).all()
    return [
        {
            "doc_id": str(r.doc_id),
            "filename": r.filename,
            "status": r.status,
            "failure_reason": r.failure_reason,
            "page_count": r.page_count,
        }
        for r in rows
    ]
