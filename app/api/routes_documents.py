"""GET /v1/documents/{doc_id}/pages/{page} (§9.3). Enforcement point E4."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_identity
from app.api.errors import ApiError
from app.api.schemas import PageResponse
from app.security import permissions
from app.security.identity import Identity

router = APIRouter()


@router.get("/v1/documents/{doc_id}/pages/{page}", response_model=PageResponse)
async def get_page(
    request: Request, doc_id: str, page: str, identity: Identity = Depends(get_identity)
) -> dict[str, Any]:
    # Path params are parsed by hand so malformed IDs get the same 404 as hidden ones.
    if not page.isdigit() or len(page) > 6:
        raise ApiError(404, "not_found")
    engine = request.app.state.engine

    def _read() -> permissions.PageText | None:
        with engine.connect() as conn:
            return permissions.read_page(conn, identity, doc_id, int(page))

    result = await run_in_threadpool(_read)
    if result is None:
        raise ApiError(404, "not_found")
    return {
        "doc_id": result.doc_id,
        "filename": result.filename,
        "page": result.page,
        "text": result.text,
    }
