"""Admin operations that change documents or the index (§6.3, §6.7)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, text

from app.audit.audit_log import write_admin_event
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


def update_acl(engine: Engine, doc_id: str, acl: Acl, admin_user_id: str) -> bool:
    """Replace the ACL on the document and all of its chunks in ONE transaction (F16, T4),
    with the `acl_change` audit event (BE-13) in the same transaction."""
    with engine.begin() as conn:
        changed = permissions.replace_document_acl(conn, doc_id, acl)
        if changed:
            write_admin_event(
                conn, event="acl_change", user_id=admin_user_id, doc_id=uuid.UUID(doc_id)
            )
        return changed


def delete_document(
    engine: Engine, doc_id: str, index_path: Path, dim: int, admin_user_id: str
) -> bool:
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
            write_admin_event(conn, event="delete", user_id=admin_user_id, doc_id=parsed)
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


def document_summary(engine: Engine) -> dict[str, int]:
    """Counts for the admin summary chips: all live files, processing, needs attention."""
    sql = text(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE status = 'pending') AS processing, "
        "count(*) FILTER (WHERE status = 'failed') AS failed "
        "FROM documents WHERE deleted_at IS NULL"
    )
    with engine.connect() as conn:
        row = conn.execute(sql).one()
    return {"total": int(row.total), "processing": int(row.processing), "failed": int(row.failed)}


def list_documents(
    engine: Engine,
    *,
    q: str | None,
    status: str | None,
    after: tuple[str, str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """BE-12: metadata only (never page or chunk text)."""
    with engine.connect() as conn:
        rows = permissions.list_documents_admin(conn, q=q, status=status, after=after, limit=limit)
    return [
        {
            "doc_id": r.doc_id,
            "filename": r.filename,
            "page_count": r.page_count,
            "status": r.status,
            "failure_reason": r.failure_reason,
            "groups": list(r.groups),
            "users": list(r.users),
            "uploaded_by": r.uploaded_by,
            "ingested_at": r.ingested_at,
            "upload": None
            if r.job_id is None
            else {
                "job_id": r.job_id,
                "status": r.job_status,
                "pages_done": r.pages_done,
                "pages_total": r.pages_total,
                "failure_reason": r.job_failure_reason,
            },
        }
        for r in rows
    ]
