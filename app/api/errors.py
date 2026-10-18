"""Error mapping (§9): every error body is exactly {"error": "<code>"}."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.query_service import ServiceUnavailable

log = structlog.get_logger(__name__)

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    422: "validation_error",
    429: "rate_limited",
    503: "unavailable",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, extra: dict[str, str] | None = None) -> None:
        self.status = status
        self.code = code
        self.extra = extra or {}


def error_response(
    status: int, code: str | None = None, extra: dict[str, str] | None = None
) -> JSONResponse:
    # Only the duplicate-upload error carries an extra field (extension.md §6.1 rule 7).
    body = {"error": code or _CODES.get(status, "error"), **(extra or {})}
    return JSONResponse(body, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422)  # no field echo: inputs may contain sensitive text

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code)

    @app.exception_handler(RateLimitExceeded)
    async def _rate(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return error_response(429)

    @app.exception_handler(ServiceUnavailable)
    async def _unavailable(request: Request, exc: ServiceUnavailable) -> JSONResponse:
        return error_response(503)

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__, path=request.url.path)
        return error_response(500, "internal_error")
