"""Admin endpoints (§9.4). The admin role grants management, never document reads."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app import admin_ops
from app.api.deps import require_admin
from app.api.errors import ApiError
from app.api.schemas import AclUpdate, DocumentStatus
from app.retrieval.index_manager import WriterLockHeld
from app.security.identity import Identity
from app.security.permissions import Acl

router = APIRouter(prefix="/v1/admin")


@router.put("/documents/{doc_id}/acl")
async def put_acl(
    request: Request, doc_id: str, body: AclUpdate, admin: Identity = Depends(require_admin)
) -> dict[str, str]:
    acl = Acl.of(body.groups, body.users)
    ok = await run_in_threadpool(admin_ops.update_acl, request.app.state.engine, doc_id, acl)
    if not ok:
        raise ApiError(404, "not_found")
    return {"status": "updated"}


@router.delete("/documents/{doc_id}")
async def delete_document(
    request: Request, doc_id: str, admin: Identity = Depends(require_admin)
) -> dict[str, str]:
    state = request.app.state
    try:
        ok = await run_in_threadpool(
            admin_ops.delete_document, state.engine, doc_id, state.index_path, state.embedder.dim
        )
    except WriterLockHeld:
        raise ApiError(503, "unavailable") from None
    if not ok:
        raise ApiError(404, "not_found")
    return {"status": "deleted"}


@router.get("/documents", response_model=list[DocumentStatus])
async def list_documents(
    request: Request,
    status: Literal["pending", "indexed", "failed"] | None = None,
    admin: Identity = Depends(require_admin),
) -> list[dict[str, Any]]:
    return await run_in_threadpool(admin_ops.list_documents, request.app.state.engine, status)
