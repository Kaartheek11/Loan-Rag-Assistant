"""GET /v1/me and GET /v1/me/documents (extension.md BE-1, BE-2, BE-4).

Both resolve access from Postgres on every call through the permission module, so group
changes show up on the next call with the same token.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_identity
from app.api.errors import ApiError
from app.api.schemas import MeResponse, MyDocumentsResponse
from app.security import permissions
from app.security.identity import Identity

router = APIRouter()

_DISPLAY_SQL = text("SELECT display FROM users WHERE user_id = :u")


def encode_cursor(filename: str, doc_id: str) -> str:
    raw = json.dumps([filename, doc_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        name, doc_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(name, str) or not isinstance(doc_id, str):
            raise ValueError("bad cursor")
        uuid.UUID(doc_id)
        return name, doc_id
    except (ValueError, binascii.Error, TypeError, json.JSONDecodeError):
        raise ApiError(422, "validation_error") from None


@router.get("/v1/me", response_model=MeResponse)
async def me(request: Request, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    state = request.app.state

    def _load() -> dict[str, Any]:
        with state.engine.connect() as conn:
            display = conn.execute(_DISPLAY_SQL, {"u": identity.user_id}).scalar()
            count = permissions.count_readable_documents(conn, identity)
        state.session_starts.record(
            token_id=request.state.token_id,
            request_id=str(uuid.uuid4()),
            user_id=identity.user_id,
        )
        return {
            "user_id": identity.user_id,
            "display_name": display or identity.user_id,
            "groups": sorted(identity.groups),
            "roles": sorted(identity.roles),
            "readable_document_count": count,
        }

    return await run_in_threadpool(_load)


@router.get("/v1/me/documents", response_model=MyDocumentsResponse)
async def my_documents(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    after = decode_cursor(cursor) if cursor else None
    engine = request.app.state.engine

    def _load() -> dict[str, Any]:
        with engine.connect() as conn:
            rows = permissions.list_readable_documents(conn, identity, after=after, limit=limit + 1)
        page, more = rows[:limit], len(rows) > limit
        return {
            "items": [
                {
                    "doc_id": r.doc_id,
                    "filename": r.filename,
                    "page_count": r.page_count,
                    "ingested_at": r.ingested_at,
                }
                for r in page
            ],
            "next_cursor": encode_cursor(page[-1].filename, page[-1].doc_id) if more else None,
        }

    return await run_in_threadpool(_load)
