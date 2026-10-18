"""The ONLY module that builds permission SQL (spec §6.5).

A user may read a chunk only if its document is `indexed`, neither the chunk nor the
document is soft-deleted, and the user's groups overlap the chunk's ACL groups or the
user is listed in the chunk's ACL users. Default deny: an empty ACL matches nobody.
There is no admin bypass (§6.2).

`scripts/check_permission_sql.py` fails CI if ACL columns are referenced anywhere else.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, text

from app.security.identity import Identity


class PermissionViolation(RuntimeError):
    """Raised when code tries to use a chunk that did not pass E1 for this request."""


@dataclass(frozen=True, slots=True)
class Acl:
    groups: tuple[str, ...] = ()
    users: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.groups and not self.users

    @classmethod
    def of(cls, groups: Iterable[str] = (), users: Iterable[str] = ()) -> Acl:
        return cls(tuple(sorted({str(g) for g in groups})), tuple(sorted({str(u) for u in users})))


@dataclass(frozen=True, slots=True, eq=False)
class VisibleChunk:
    """A chunk that passed the permission predicate (E1). Constructed only in this module."""

    chunk_id: int
    doc_id: str
    filename: str
    page: int
    section: str | None
    text: str
    request_id: str


@dataclass(slots=True)
class PermittedSet:
    """The chunks a request is allowed to use, in fused rank order (E1 output, E2/E3 input)."""

    request_id: str
    chunks: list[VisibleChunk] = field(default_factory=list)
    _by_id: dict[int, VisibleChunk] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._by_id = {c.chunk_id: c for c in self.chunks}

    def __contains__(self, chunk: object) -> bool:
        if not isinstance(chunk, VisibleChunk):
            return False
        # Identity check: only the exact objects returned by E1 for this request count.
        return self._by_id.get(chunk.chunk_id) is chunk and chunk.request_id == self.request_id

    def require(self, chunk: object) -> VisibleChunk:
        if chunk not in self:
            raise PermissionViolation("chunk is not in this request's permitted set")
        assert isinstance(chunk, VisibleChunk)
        return chunk

    def ids(self) -> list[int]:
        return [c.chunk_id for c in self.chunks]


@dataclass(frozen=True, slots=True)
class PageText:
    doc_id: str
    filename: str
    page: int
    text: str
    page_count: int = 0
    section: str | None = None  # section of the page's first chunk (extension.md §8.7)


@dataclass(frozen=True, slots=True)
class ReadableDocument:
    doc_id: str
    filename: str
    page_count: int
    ingested_at: datetime


# ---------------------------------------------------------------------------------------
# The predicate. Identical in meaning to spec §6.5; written once and reused.
# ---------------------------------------------------------------------------------------
_CHUNK_PREDICATE = """
    c.chunk_id = ANY(CAST(:candidate_ids AS bigint[]))
    AND d.status = 'indexed'
    AND d.deleted_at IS NULL
    AND c.deleted_at IS NULL
    AND (
          c.acl_groups && CAST(:user_groups AS text[])
       OR CAST(:user_id AS text) = ANY(c.acl_users)
    )
"""

_VISIBLE_IDS_SQL = text(
    f"""
    SELECT c.chunk_id
    FROM chunks c
    JOIN documents d ON d.doc_id = c.doc_id
    WHERE {_CHUNK_PREDICATE}
    """
)

_VISIBLE_CHUNKS_SQL = text(
    f"""
    SELECT c.chunk_id, c.doc_id, d.filename, c.page, c.section, c.text
    FROM chunks c
    JOIN documents d ON d.doc_id = c.doc_id
    WHERE {_CHUNK_PREDICATE}
    """
)

_DOCUMENT_PREDICATE = """
    d.doc_id = CAST(:doc_id AS uuid)
    AND d.status = 'indexed'
    AND d.deleted_at IS NULL
    AND (
          d.acl_groups && CAST(:user_groups AS text[])
       OR CAST(:user_id AS text) = ANY(d.acl_users)
    )
"""

_READ_PAGE_SQL = text(
    f"""
    SELECT d.doc_id, d.filename, d.page_count, p.page, p.text,
           (SELECT c.section FROM chunks c
            WHERE c.doc_id = d.doc_id AND c.page = p.page AND c.deleted_at IS NULL
            ORDER BY c.chunk_id LIMIT 1) AS section
    FROM documents d
    JOIN pages p ON p.doc_id = d.doc_id
    WHERE {_DOCUMENT_PREDICATE}
      AND p.page = :page
    """
)

# Readable documents for an identity (BE-1, BE-2): the same document-level predicate,
# without the doc_id filter.
_READABLE_DOCS_WHERE = """
    d.status = 'indexed'
    AND d.deleted_at IS NULL
    AND (
          d.acl_groups && CAST(:user_groups AS text[])
       OR CAST(:user_id AS text) = ANY(d.acl_users)
    )
"""

_COUNT_READABLE_SQL = text(f"SELECT count(*) FROM documents d WHERE {_READABLE_DOCS_WHERE}")

_LIST_READABLE_SQL = text(
    f"""
    SELECT d.doc_id, d.filename, d.page_count, d.ingested_at
    FROM documents d
    WHERE {_READABLE_DOCS_WHERE}
      AND (CAST(:after_name AS text) IS NULL
           OR (d.filename, d.doc_id) > (CAST(:after_name AS text), CAST(:after_id AS uuid)))
    ORDER BY d.filename, d.doc_id
    LIMIT :limit
    """
)

_UPDATE_DOC_ACL_SQL = text(
    """
    UPDATE documents
    SET acl_groups = CAST(:groups AS text[]), acl_users = CAST(:users AS text[])
    WHERE doc_id = CAST(:doc_id AS uuid) AND deleted_at IS NULL
    """
)

_UPDATE_CHUNK_ACL_SQL = text(
    """
    UPDATE chunks
    SET acl_groups = CAST(:groups AS text[]), acl_users = CAST(:users AS text[])
    WHERE doc_id = CAST(:doc_id AS uuid)
    """
)


def _identity_params(identity: Identity) -> dict[str, object]:
    return {"user_groups": sorted(identity.groups), "user_id": identity.user_id}


def visible_ids(conn: Connection, identity: Identity, candidate_ids: Iterable[int]) -> set[int]:
    """E1: the subset of candidate chunk IDs this identity may read."""
    ids = sorted({int(i) for i in candidate_ids})
    if not ids:
        return set()
    rows = conn.execute(_VISIBLE_IDS_SQL, {"candidate_ids": ids, **_identity_params(identity)})
    return {int(r[0]) for r in rows}


def load_permitted(
    conn: Connection, identity: Identity, ordered_ids: Sequence[int], request_id: str
) -> PermittedSet:
    """E1 again, with text: re-check and load the final chunks, keeping the given order."""
    ids = [int(i) for i in ordered_ids]
    if not ids:
        return PermittedSet(request_id=request_id)
    rows = conn.execute(
        _VISIBLE_CHUNKS_SQL, {"candidate_ids": ids, **_identity_params(identity)}
    ).all()
    by_id = {
        int(r.chunk_id): VisibleChunk(
            chunk_id=int(r.chunk_id),
            doc_id=str(r.doc_id),
            filename=str(r.filename),
            page=int(r.page),
            section=r.section,
            text=str(r.text),
            request_id=request_id,
        )
        for r in rows
    }
    return PermittedSet(request_id=request_id, chunks=[by_id[i] for i in ids if i in by_id])


def _parse_uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def read_page(conn: Connection, identity: Identity, doc_id: str, page: int) -> PageText | None:
    """E4: one page of a document, only if the caller can read the document."""
    parsed = _parse_uuid(doc_id)
    if parsed is None:
        return None
    row = conn.execute(
        _READ_PAGE_SQL, {"doc_id": parsed, "page": int(page), **_identity_params(identity)}
    ).first()
    if row is None:
        return None
    return PageText(
        doc_id=str(row.doc_id),
        filename=str(row.filename),
        page=int(row.page),
        text=str(row.text),
        page_count=int(row.page_count),
        section=None if row.section is None else str(row.section),
    )


def count_readable_documents(conn: Connection, identity: Identity) -> int:
    """BE-1: how many documents this identity can read (indexed, not deleted, ACL match)."""
    return int(conn.execute(_COUNT_READABLE_SQL, _identity_params(identity)).scalar_one())


def list_readable_documents(
    conn: Connection, identity: Identity, *, after: tuple[str, str] | None, limit: int
) -> list[ReadableDocument]:
    """BE-2: readable documents sorted by filename, keyset-paginated after (filename, doc_id)."""
    params: dict[str, object] = {
        "after_name": after[0] if after else None,
        "after_id": _parse_uuid(after[1]) if after else None,
        "limit": int(limit),
        **_identity_params(identity),
    }
    if after is not None and params["after_id"] is None:
        return []
    rows = conn.execute(_LIST_READABLE_SQL, params).all()
    return [
        ReadableDocument(
            doc_id=str(r.doc_id),
            filename=str(r.filename),
            page_count=int(r.page_count),
            ingested_at=r.ingested_at,
        )
        for r in rows
    ]


def acl_columns(acl: Acl) -> dict[str, list[str]]:
    """Column values for writing an ACL onto a documents or chunks row at ingestion."""
    return {"acl_groups": list(acl.groups), "acl_users": list(acl.users)}


def replace_document_acl(conn: Connection, doc_id: str, acl: Acl) -> bool:
    """Replace a document's ACL and every chunk copy. Call inside one transaction (F16).

    Returns False if the document does not exist or is deleted.
    """
    parsed = _parse_uuid(doc_id)
    if parsed is None:
        return False
    params = {"doc_id": parsed, "groups": list(acl.groups), "users": list(acl.users)}
    result = conn.execute(_UPDATE_DOC_ACL_SQL, params)
    if result.rowcount == 0:
        return False
    conn.execute(_UPDATE_CHUNK_ACL_SQL, params)
    return True


# ---------------------------------------------------------------------------------------
# Admin metadata (extension.md BE-9, BE-12). Never returns page or chunk text.
# ---------------------------------------------------------------------------------------
_DOCUMENT_ACL_SQL = text(
    "SELECT acl_groups, acl_users FROM documents WHERE doc_id = CAST(:doc_id AS uuid)"
)

_ADMIN_LIST_SQL = text(
    """
    SELECT d.doc_id, d.filename, d.page_count, d.status, d.failure_reason,
           d.acl_groups, d.acl_users, d.ingested_at,
           j.job_id, j.status AS job_status, j.pages_done, j.pages_total,
           j.failure_reason AS job_failure_reason, j.uploaded_by
    FROM documents d
    LEFT JOIN LATERAL (
        SELECT u.* FROM upload_jobs u WHERE u.doc_id = d.doc_id
        ORDER BY u.created_at DESC LIMIT 1
    ) j ON true
    WHERE d.deleted_at IS NULL
      AND (CAST(:status AS text) IS NULL OR d.status = CAST(:status AS text))
      AND (CAST(:pattern AS text) IS NULL OR d.filename ILIKE CAST(:pattern AS text) ESCAPE '\\')
      AND (CAST(:after_name AS text) IS NULL
           OR (d.filename, d.doc_id) > (CAST(:after_name AS text), CAST(:after_id AS uuid)))
    ORDER BY d.filename, d.doc_id
    LIMIT :limit
    """
)


@dataclass(frozen=True, slots=True)
class AdminDocument:
    doc_id: str
    filename: str
    page_count: int
    status: str
    failure_reason: str | None
    groups: tuple[str, ...]
    users: tuple[str, ...]
    ingested_at: datetime
    job_id: str | None
    job_status: str | None
    pages_done: int | None
    pages_total: int | None
    job_failure_reason: str | None
    uploaded_by: str | None


def document_acl(conn: Connection, doc_id: str) -> Acl | None:
    """The ACL stored on a document row (the upload worker copies it onto chunks)."""
    parsed = _parse_uuid(doc_id)
    if parsed is None:
        return None
    row = conn.execute(_DOCUMENT_ACL_SQL, {"doc_id": parsed}).first()
    if row is None:
        return None
    return Acl.of(row.acl_groups or [], row.acl_users or [])


def list_documents_admin(
    conn: Connection,
    *,
    q: str | None,
    status: str | None,
    after: tuple[str, str] | None,
    limit: int,
) -> list[AdminDocument]:
    """BE-12: file metadata for the admin files page, keyset-paginated by filename."""
    pattern = None
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
    after_id = _parse_uuid(after[1]) if after else None
    if after is not None and after_id is None:
        return []
    rows = conn.execute(
        _ADMIN_LIST_SQL,
        {
            "status": status,
            "pattern": pattern,
            "after_name": after[0] if after else None,
            "after_id": after_id,
            "limit": int(limit),
        },
    ).all()
    return [
        AdminDocument(
            doc_id=str(r.doc_id),
            filename=str(r.filename),
            page_count=int(r.page_count),
            status=str(r.status),
            failure_reason=r.failure_reason,
            groups=tuple(r.acl_groups or ()),
            users=tuple(r.acl_users or ()),
            ingested_at=r.ingested_at,
            job_id=None if r.job_id is None else str(r.job_id),
            job_status=r.job_status,
            pages_done=r.pages_done,
            pages_total=r.pages_total,
            job_failure_reason=r.job_failure_reason,
            uploaded_by=r.uploaded_by,
        )
        for r in rows
    ]
