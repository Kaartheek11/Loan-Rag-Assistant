"""POST /v1/query (§9.1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_identity
from app.api.schemas import QueryRequest, QueryResponse
from app.security.identity import Identity


def build_router(limiter: Limiter, limit: str) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/query", response_model=QueryResponse)
    @limiter.limit(limit)
    async def query(
        request: Request, body: QueryRequest, identity: Identity = Depends(get_identity)
    ) -> dict[str, Any]:
        state = request.app.state
        top_k = body.top_k or state.settings.top_k_default
        outcome = await run_in_threadpool(
            state.query_service.answer, identity, body.question, top_k
        )
        response: dict[str, Any] = outcome.response_body()
        return response

    return router
