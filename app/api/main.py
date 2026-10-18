"""App factory and startup (§5.5, §15).

Startup loads the embedding model once, refuses to start if index_state.embedding_model
does not match EMBEDDING_MODEL, loads the FAISS index, and builds the verifier and LLM
adapter. Production refuses unsafe configs (fake LLM, static test keys, missing secrets).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from slowapi import Limiter
from sqlalchemy import Engine, text
from starlette.concurrency import run_in_threadpool

from app.api import routes_admin, routes_documents, routes_me, routes_query
from app.api.errors import install_error_handlers
from app.api.schemas import HealthResponse
from app.audit.audit_log import AuditLogger, SessionStartRecorder
from app.config import ConfigError, Settings, get_settings
from app.db.seed import ensure_index_state, read_index_state
from app.db.session import make_engine
from app.generation.llm_adapter import LLMAdapter, build_adapter
from app.query_service import QueryService
from app.retrieval.embedder import Embedder
from app.retrieval.index_manager import IndexHolder
from app.security.auth import TokenVerifier
from app.security.redaction import configure_logging

log = structlog.get_logger(__name__)


class StartupError(RuntimeError):
    pass


def _user_key(request: Request) -> str:
    identity = getattr(request.state, "identity", None)
    return f"user:{identity.user_id}" if identity is not None else "anonymous"


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    llm: LLMAdapter | None = None,
    verifier: TokenVerifier | None = None,
    configure_logs: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            settings.validate_for_startup()
        except ConfigError as exc:
            raise StartupError(str(exc)) from exc
        if configure_logs:
            configure_logging(settings.secret_values())
        db = engine or make_engine(settings.database_url, environment=settings.environment)
        emb: Embedder = (
            embedder
            if embedder is not None
            else await run_in_threadpool(
                Embedder, settings.embedding_model, settings.embedding_device
            )
        )
        if emb.model_name != settings.embedding_model:
            raise StartupError("embedder does not match EMBEDDING_MODEL")
        with db.begin() as conn:
            ensure_index_state(conn, settings.embedding_model)
            state = read_index_state(conn)
        assert state is not None
        version, built_with = state
        if built_with != settings.embedding_model:
            raise StartupError(
                f"index was built with {built_with!r} but EMBEDDING_MODEL is "
                f"{settings.embedding_model!r}; run `python -m app.cli rebuild-index`"
            )
        index_path = Path(settings.faiss_index_path)
        holder = IndexHolder(index_path, emb.dim)
        await run_in_threadpool(holder.load, version)
        key = settings.audit_hmac_key.get_secret_value() if settings.audit_hmac_key else ""
        adapter = llm or build_adapter(settings)
        app.state.settings = settings
        app.state.engine = db
        app.state.embedder = emb
        app.state.index = holder
        app.state.index_path = index_path
        app.state.llm = adapter
        app.state.verifier = verifier or TokenVerifier.from_settings(
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            jwks_url=settings.jwks_url,
            public_key_path=settings.jwt_public_key_path,
        )
        audit = AuditLogger(db, key)
        app.state.audit = audit
        app.state.session_starts = SessionStartRecorder(audit)
        app.state.query_service = QueryService(
            engine=db,
            embedder=emb,
            index=holder,
            llm=adapter,
            audit=audit,
            n_start=settings.retrieval_n_start,
            n_max=settings.retrieval_n_max,
        )
        log.info(
            "startup_complete",
            index_version=version,
            llm=adapter.name,
            environment=settings.environment,
        )
        yield
        if engine is None:
            db.dispose()

    app = FastAPI(title="Loan File RAG Assistant", version="0.1.0", lifespan=lifespan)
    limiter = Limiter(key_func=_user_key, strategy="moving-window", storage_uri="memory://")
    app.state.limiter = limiter
    install_error_handlers(app)

    @app.middleware("http")
    async def api_headers(request: Request, call_next: Any) -> Any:
        """UI-S9: API responses (answers, page text, errors) are never cached by browsers.
        Every /v1 response also carries X-Request-Id, so the UI can attach it to error cards
        for debugging (UI-F12) without adding anything to the {"error": ...} body."""
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Request-Id"] = request.state.request_id
        return response

    app.include_router(
        routes_query.build_router(limiter, f"{settings.rate_limit_per_minute}/minute")
    )
    app.include_router(routes_me.router)
    app.include_router(routes_documents.router)
    app.include_router(routes_admin.build_router(limiter))

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> dict[str, Any]:
        state = request.app.state

        def _check() -> dict[str, Any]:
            db_ok, version = "ok", None
            try:
                with state.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    row = read_index_state(conn)
                    version = row[0] if row else None
            except Exception:  # noqa: BLE001
                db_ok = "error"
            llm_ok = "ok" if state.llm.healthy() else "error"
            overall = "ok" if db_ok == "ok" and llm_ok == "ok" else "degraded"
            return {"status": overall, "db": db_ok, "index_version": version, "llm": llm_ok}

        return await run_in_threadpool(_check)

    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory app.api.main:app_factory`."""
    return create_app()
