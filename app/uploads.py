"""Admin uploads (extension.md BE-8, BE-10, BE-11, §6.1).

Validation happens in the API before anything is stored permanently. The PDF is moved into
the document store under a generated name ({doc_id}.pdf); the original filename is kept for
display only, sanitized. The documents row (pending, ACL from the request), the upload job
(queued), and the `upload` audit event are written in one transaction.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from app.audit.audit_log import write_admin_event
from app.db.tables import documents, upload_jobs, user_groups, users
from app.security.permissions import Acl, acl_columns

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_FILENAME = 200


class UploadRejected(Exception):
    def __init__(self, status: int, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = extra


@dataclass(frozen=True)
class StoredUpload:
    job_id: str
    doc_id: str
    filename: str


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_filename(name: str) -> str:
    """Display name only (never a path): drop path parts and control characters, max 200."""
    name = unicodedata.normalize("NFKC", name or "")
    name = re.split(r"[\\/]", name)[-1]
    name = _CONTROL.sub("", name).strip().lstrip(".").strip()
    name = name[:MAX_FILENAME]
    return name or "upload.pdf"


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_pdf(path: Path) -> int:
    """§6.1 rules 3-5. Returns the page count."""
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise UploadRejected(422, "not_a_pdf")
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        encrypted = reader.is_encrypted
    except Exception:  # noqa: BLE001 - anything pypdf cannot open is not a usable PDF
        raise UploadRejected(422, "not_a_pdf") from None
    if encrypted:
        raise UploadRejected(422, "encrypted_pdf")
    try:
        pages = len(reader.pages)
    except Exception:  # noqa: BLE001
        raise UploadRejected(422, "not_a_pdf") from None
    if not 1 <= pages <= MAX_PAGES:
        raise UploadRejected(422, "too_many_pages")
    return pages


def known_groups(conn: Connection) -> list[str]:
    """BE-11: group names that exist (distinct values from user_groups)."""
    return sorted(
        str(g) for g in conn.execute(select(user_groups.c.group_name).distinct()).scalars()
    )


def search_users(conn: Connection, q: str | None, limit: int = 20) -> list[dict[str, str]]:
    """BE-11: up to 20 enabled users matching q (user ID or display name)."""
    stmt = select(users.c.user_id, users.c.display).where(users.c.disabled.is_(False))
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            users.c.user_id.ilike(pattern, escape="\\")
            | users.c.display.ilike(pattern, escape="\\")
        )
    rows = conn.execute(stmt.order_by(users.c.display, users.c.user_id).limit(limit)).all()
    return [{"user_id": str(r.user_id), "display": str(r.display)} for r in rows]


def validate_access(conn: Connection, groups: list[str], user_ids: list[str]) -> Acl:
    """§6.1 rule 6: at least one group or user; all must exist. No access is never stored."""
    acl = Acl.of(
        [g.strip() for g in groups if g.strip()], [u.strip() for u in user_ids if u.strip()]
    )
    if acl.is_empty:
        raise UploadRejected(422, "invalid_access")
    if not set(acl.groups) <= set(known_groups(conn)):
        raise UploadRejected(422, "invalid_access")
    if acl.users:
        found = set(
            conn.execute(select(users.c.user_id).where(users.c.user_id.in_(acl.users))).scalars()
        )
        if found != set(acl.users):
            raise UploadRejected(422, "invalid_access")
    return acl


def find_duplicate(conn: Connection, digest: str) -> str | None:
    """§6.1 rule 7: filename of a non-deleted document with the same SHA-256."""
    return conn.execute(
        select(documents.c.filename)
        .where(documents.c.sha256 == digest)
        .where(documents.c.deleted_at.is_(None))
        .limit(1)
    ).scalar()


def store_upload(
    engine: Engine,
    *,
    admin_user_id: str,
    temp_path: Path,
    original_name: str,
    groups: list[str],
    user_ids: list[str],
    store_dir: Path,
) -> StoredUpload:
    """Validate a received file and queue it. The caller enforces size (rule 2)."""
    pages = check_pdf(temp_path)
    digest = sha256_path(temp_path)
    with engine.connect() as conn:
        acl = validate_access(conn, groups, user_ids)
        existing = find_duplicate(conn, digest)
    if existing is not None:
        raise UploadRejected(409, "duplicate", existing_filename=existing)

    doc_id = uuid.uuid4()
    job_id = uuid.uuid4()
    filename = sanitize_filename(original_name)
    store_dir.mkdir(parents=True, exist_ok=True)
    final = store_dir / f"{doc_id}.pdf"  # generated name; never the uploaded filename
    shutil.move(str(temp_path), final)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(documents).values(
                    doc_id=doc_id,
                    filename=filename,
                    sha256=digest,
                    page_count=pages,
                    ocr_pages=[],
                    status="pending",
                    failure_reason=None,
                    ingested_at=text("now()"),
                    **acl_columns(acl),
                )
            )
            conn.execute(
                insert(upload_jobs).values(
                    job_id=job_id,
                    doc_id=doc_id,
                    uploaded_by=admin_user_id,
                    status="queued",
                    pages_done=0,
                    pages_total=pages,
                )
            )
            write_admin_event(conn, event="upload", user_id=admin_user_id, doc_id=doc_id)
    except IntegrityError:
        final.unlink(missing_ok=True)
        with engine.connect() as conn:
            existing = find_duplicate(conn, digest)
        raise UploadRejected(409, "duplicate", existing_filename=existing or "") from None
    except Exception:
        final.unlink(missing_ok=True)
        raise
    return StoredUpload(job_id=str(job_id), doc_id=str(doc_id), filename=filename)


_JOB_COLUMNS = (
    upload_jobs.c.job_id,
    upload_jobs.c.doc_id,
    documents.c.filename,
    upload_jobs.c.status,
    upload_jobs.c.pages_done,
    upload_jobs.c.pages_total,
    upload_jobs.c.failure_reason,
    upload_jobs.c.created_at,
    upload_jobs.c.uploaded_by,
)


def _job_dict(r: Any) -> dict[str, Any]:
    return {
        "job_id": str(r.job_id),
        "doc_id": str(r.doc_id),
        "filename": r.filename,
        "status": r.status,
        "pages_done": r.pages_done,
        "pages_total": r.pages_total,
        "failure_reason": r.failure_reason,
        "created_at": r.created_at,
        "uploaded_by": r.uploaded_by,
    }


def get_job(engine: Engine, job_id: str) -> dict[str, Any] | None:
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(*_JOB_COLUMNS)
            .join(documents, documents.c.doc_id == upload_jobs.c.doc_id)
            .where(upload_jobs.c.job_id == parsed)
        ).first()
    return _job_dict(row) if row else None


def list_jobs(
    engine: Engine, *, status: str | None, after: tuple[str, str] | None, limit: int
) -> list[dict[str, Any]]:
    """Newest first, keyset-paginated by (created_at, job_id)."""
    stmt = select(*_JOB_COLUMNS).join(documents, documents.c.doc_id == upload_jobs.c.doc_id)
    if status:
        stmt = stmt.where(upload_jobs.c.status == status)
    if after:
        stmt = stmt.where(
            text(
                "(upload_jobs.created_at, upload_jobs.job_id) < "
                "(CAST(:a_ts AS timestamptz), CAST(:a_id AS uuid))"
            )
        ).params(a_ts=after[0], a_id=after[1])
    stmt = stmt.order_by(upload_jobs.c.created_at.desc(), upload_jobs.c.job_id.desc()).limit(limit)
    with engine.connect() as conn:
        return [_job_dict(r) for r in conn.execute(stmt)]


def retry_job(engine: Engine, job_id: str) -> bool:
    """BE-10: re-queue a failed job. False when the job is missing or not failed."""
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        return False
    with engine.begin() as conn:
        row = conn.execute(
            update(upload_jobs)
            .where(upload_jobs.c.job_id == parsed, upload_jobs.c.status == "failed")
            .values(
                status="queued",
                pages_done=0,
                failure_reason=None,
                heartbeat_at=None,
                finished_at=None,
            )
            .returning(upload_jobs.c.doc_id)
        ).first()
        if row is None:
            return False
        conn.execute(
            update(documents)
            .where(documents.c.doc_id == row.doc_id, documents.c.deleted_at.is_(None))
            .values(status="pending", failure_reason=None)
        )
    return True


def incoming_dir(store_dir: Path) -> Path:
    """Temporary uploads live next to the store, so the final move is a rename."""
    path = store_dir / ".incoming"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path
