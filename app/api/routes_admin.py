"""Admin endpoints (task.md §9.4, extension.md BE-8 to BE-13).

The admin role grants management (uploads, access, deletion, status), never document reads:
every endpoint here returns metadata only, never page or chunk text.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app import admin_ops, uploads
from app.api.deps import require_admin
from app.api.errors import ApiError
from app.api.routes_me import decode_cursor, encode_cursor
from app.api.schemas import (
    AclUpdate,
    AdminDocumentsResponse,
    AdminUser,
    UploadAccepted,
    UploadJob,
    UploadJobsResponse,
)
from app.retrieval.index_manager import WriterLockHeld
from app.security.identity import Identity

UPLOAD_RATE_LIMIT = "120/hour"  # per admin (extension.md §6.1 rule 8)
_COPY_CHUNK = 1 << 20


def build_router(limiter: Limiter) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")

    # ---- documents ---------------------------------------------------------------------
    @router.put("/documents/{doc_id}/acl")
    async def put_acl(
        request: Request, doc_id: str, body: AclUpdate, admin: Identity = Depends(require_admin)
    ) -> dict[str, str]:
        engine = request.app.state.engine

        def _update() -> bool:
            with engine.connect() as conn:
                acl = uploads.validate_access(conn, body.groups, body.users)
            return admin_ops.update_acl(engine, doc_id, acl, admin.user_id)

        try:
            ok = await run_in_threadpool(_update)
        except uploads.UploadRejected as exc:
            raise ApiError(exc.status, exc.code) from None
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
                admin_ops.delete_document,
                state.engine,
                doc_id,
                state.index_path,
                state.embedder.dim,
                admin.user_id,
            )
        except WriterLockHeld:
            raise ApiError(503, "unavailable") from None
        if not ok:
            raise ApiError(404, "not_found")
        return {"status": "deleted"}

    @router.get("/documents", response_model=AdminDocumentsResponse)
    async def list_documents(
        request: Request,
        status: Literal["pending", "indexed", "failed"] | None = None,
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        admin: Identity = Depends(require_admin),
    ) -> dict[str, Any]:
        after = decode_cursor(cursor) if cursor else None
        engine = request.app.state.engine

        def _load() -> dict[str, Any]:
            rows = admin_ops.list_documents(
                engine, q=q or None, status=status, after=after, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            return {
                "items": page,
                "next_cursor": encode_cursor(page[-1]["filename"], page[-1]["doc_id"])
                if more
                else None,
                "summary": admin_ops.document_summary(engine),
            }

        return await run_in_threadpool(_load)

    # ---- uploads -----------------------------------------------------------------------
    @router.post("/uploads", status_code=202, response_model=UploadAccepted)
    @limiter.limit(UPLOAD_RATE_LIMIT)
    async def upload(request: Request, admin: Identity = Depends(require_admin)) -> dict[str, str]:
        # Rule 2: one file, at most 50 MB (Caddy also caps this route at 55 MB).
        declared = request.headers.get("content-length")
        if (
            declared
            and declared.isdigit()
            and int(declared) > uploads.MAX_UPLOAD_BYTES + _COPY_CHUNK
        ):
            raise ApiError(413, "file_too_large")
        form = await request.form(max_files=2, max_fields=200)
        files = [v for v in form.getlist("file") if isinstance(v, UploadFile)]
        if len(files) != 1:
            raise ApiError(422, "validation_error")
        upload_file = files[0]
        groups = [str(v) for v in form.getlist("groups[]") if isinstance(v, str)]
        users = [str(v) for v in form.getlist("users[]") if isinstance(v, str)]
        state = request.app.state
        store = Path(state.settings.document_store_path)
        temp = uploads.incoming_dir(store) / f"{uuid.uuid4()}.part"
        try:
            size = 0
            with open(temp, "wb") as out:
                while chunk := await upload_file.read(_COPY_CHUNK):
                    size += len(chunk)
                    if size > uploads.MAX_UPLOAD_BYTES:
                        raise ApiError(413, "file_too_large")
                    out.write(chunk)
            stored = await run_in_threadpool(
                lambda: uploads.store_upload(
                    state.engine,
                    admin_user_id=admin.user_id,
                    temp_path=temp,
                    original_name=upload_file.filename or "",
                    groups=groups,
                    user_ids=users,
                    store_dir=store,
                )
            )
        except uploads.UploadRejected as exc:
            raise ApiError(
                exc.status, exc.code, {k: str(v) for k, v in exc.extra.items()}
            ) from None
        finally:
            await upload_file.close()
            if temp.exists():
                os.unlink(temp)
        return {
            "job_id": stored.job_id,
            "doc_id": stored.doc_id,
            "filename": stored.filename,
            "status": "queued",
        }

    @router.get("/uploads", response_model=UploadJobsResponse)
    async def list_uploads(
        request: Request,
        status: Literal["queued", "processing", "indexed", "failed"] | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        admin: Identity = Depends(require_admin),
    ) -> dict[str, Any]:
        after = decode_cursor(cursor) if cursor else None
        engine = request.app.state.engine

        def _load() -> dict[str, Any]:
            rows = uploads.list_jobs(engine, status=status, after=after, limit=limit + 1)
            page, more = rows[:limit], len(rows) > limit
            last = page[-1] if page else None
            return {
                "items": page,
                "next_cursor": encode_cursor(last["created_at"].isoformat(), last["job_id"])
                if more and last
                else None,
            }

        return await run_in_threadpool(_load)

    @router.get("/uploads/{job_id}", response_model=UploadJob)
    async def get_upload(
        request: Request, job_id: str, admin: Identity = Depends(require_admin)
    ) -> dict[str, Any]:
        job = await run_in_threadpool(uploads.get_job, request.app.state.engine, job_id)
        if job is None:
            raise ApiError(404, "not_found")
        return job

    @router.post("/uploads/{job_id}/retry", status_code=202, response_model=UploadJob)
    async def retry_upload(
        request: Request, job_id: str, admin: Identity = Depends(require_admin)
    ) -> dict[str, Any]:
        engine = request.app.state.engine
        if not await run_in_threadpool(uploads.retry_job, engine, job_id):
            if await run_in_threadpool(uploads.get_job, engine, job_id) is None:
                raise ApiError(404, "not_found")
            raise ApiError(409, "conflict")  # only failed jobs can be retried
        job = await run_in_threadpool(uploads.get_job, engine, job_id)
        assert job is not None
        return job

    # ---- pickers -----------------------------------------------------------------------
    @router.get("/groups", response_model=list[str])
    async def groups(request: Request, admin: Identity = Depends(require_admin)) -> list[str]:
        engine = request.app.state.engine

        def _load() -> list[str]:
            with engine.connect() as conn:
                return uploads.known_groups(conn)

        return await run_in_threadpool(_load)

    @router.get("/users", response_model=list[AdminUser])
    async def users(
        request: Request,
        q: str | None = Query(default=None, max_length=100),
        admin: Identity = Depends(require_admin),
    ) -> list[dict[str, str]]:
        engine = request.app.state.engine

        def _load() -> list[dict[str, str]]:
            with engine.connect() as conn:
                return uploads.search_users(conn, q)

        return await run_in_threadpool(_load)

    return router
