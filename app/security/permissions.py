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
    SELECT d.doc_id, d.filename, p.page, p.text
    FROM documents d
    JOIN pages p ON p.doc_id = d.doc_id
    WHERE {_DOCUMENT_PREDICATE}
      AND p.page = :page
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
        doc_id=str(row.doc_id), filename=str(row.filename), page=int(row.page), text=str(row.text)
    )


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
