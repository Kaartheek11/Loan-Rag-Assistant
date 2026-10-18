"""Request dependencies: verified identity and the admin check (§6.1, §9.4)."""

from __future__ import annotations

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import ApiError
from app.security.auth import AuthError, resolve_identity
from app.security.identity import Identity

# Declares the Bearer scheme in OpenAPI so /docs shows an "Authorize" button. auto_error is
# off: the header is still parsed and rejected below, with the exact {"error": ...} bodies.
_bearer_scheme = HTTPBearer(
    auto_error=False, description="Keycloak access token (or `python -m app.cli mint-token`)"
)


def get_identity(
    request: Request,
    _docs_only: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> Identity:
    """Sync on purpose: FastAPI runs it in the thread pool (DB lookup per request, S4)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized")
    state = request.app.state
    try:
        user_id, token_id = state.verifier.verify_with_token_id(token.strip())
    except AuthError:
        raise ApiError(401, "unauthorized") from None
    with state.engine.connect() as conn:
        identity = resolve_identity(conn, user_id)
    if identity is None:
        raise ApiError(401, "unauthorized")
    request.state.identity = identity
    request.state.token_id = token_id
    return identity


def require_admin(identity: Identity = Depends(get_identity)) -> Identity:
    if not identity.is_admin:
        raise ApiError(403, "forbidden")
    return identity
