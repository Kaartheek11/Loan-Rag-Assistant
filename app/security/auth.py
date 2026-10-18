"""JWT verification and identity resolution (spec §6.1).

Keycloak authenticates; Postgres authorizes. The token contributes only `sub`. Groups and
roles are loaded from Postgres on every request; any group/role claims in the token are
ignored. The algorithm is pinned to RS256 everywhere (no HS256, no `none`).
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import Connection, text

from app.security.identity import Identity

ALGORITHM = "RS256"
JWKS_MIN_REFETCH_SECONDS = 60.0


class AuthError(Exception):
    """Any authentication failure. Callers map it to 401 {"error": "unauthorized"}."""


class JwksCache:
    """Caches Keycloak's signing keys by `kid`.

    An unknown kid triggers a refetch, at most once a minute (key rotation without restart).
    """

    def __init__(
        self,
        url: str,
        *,
        min_refetch_seconds: float = JWKS_MIN_REFETCH_SECONDS,
        fetch: Callable[[], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = jwt.PyJWKClient(url, cache_jwk_set=False, cache_keys=False, timeout=5)
        self._fetch = fetch or self._client.fetch_data
        self._min_refetch = min_refetch_seconds
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._last_fetch: float | None = None
        self._lock = threading.Lock()
        self.fetch_count = 0

    def _refresh(self) -> None:
        data = self._fetch()
        jwk_set = jwt.PyJWKSet.from_dict(data)
        keys: dict[str, Any] = {}
        for k in jwk_set.keys:
            if k.key_id and (k.public_key_use in (None, "sig")):
                keys[k.key_id] = k.key
        self._keys = keys
        self._last_fetch = self._clock()
        self.fetch_count += 1

    def get(self, kid: str) -> Any:
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            now = self._clock()
            if self._last_fetch is None or now - self._last_fetch >= self._min_refetch:
                try:
                    self._refresh()
                except Exception as exc:  # network errors, malformed JWKS
                    raise AuthError("jwks_unavailable") from exc
            if kid in self._keys:
                return self._keys[kid]
            raise AuthError("unknown_kid")


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_key_pem: str | None = None,
        jwks: JwksCache | None = None,
    ) -> None:
        if (public_key_pem is None) == (jwks is None):
            raise ValueError("configure exactly one of public_key_pem or jwks")
        self._issuer = issuer
        self._audience = audience
        self._static_key = (
            jwt.algorithms.RSAAlgorithm.from_jwk(_pem_to_jwk(public_key_pem))
            if public_key_pem
            else None
        )
        self._jwks = jwks

    @classmethod
    def from_settings(
        cls, *, issuer: str, audience: str, jwks_url: str | None, public_key_path: str | None
    ) -> TokenVerifier:
        if jwks_url:
            return cls(issuer=issuer, audience=audience, jwks=JwksCache(jwks_url))
        if public_key_path:
            pem = Path(public_key_path).read_text()
            return cls(issuer=issuer, audience=audience, public_key_pem=pem)
        raise ValueError("no JWT key source configured")

    def verify(self, token: str) -> str:
        """Return the verified `sub` (user_id) or raise AuthError."""
        return self.verify_with_token_id(token)[0]

    def verify_with_token_id(self, token: str) -> tuple[str, str]:
        """Return (sub, token_id). token_id is the `jti` claim, or a hash of the token."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("malformed") from exc
        if header.get("alg") != ALGORITHM:
            raise AuthError("bad_alg")
        if self._static_key is not None:
            key: Any = self._static_key
        else:
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise AuthError("missing_kid")
            assert self._jwks is not None
            key = self._jwks.get(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"], "verify_signature": True},
            )
        except jwt.PyJWTError as exc:
            raise AuthError("invalid") from exc
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthError("missing_sub")
        jti = claims.get("jti")
        token_id = (
            jti if isinstance(jti, str) and jti else hashlib.sha256(token.encode()).hexdigest()
        )
        return sub, token_id


def _pem_to_jwk(pem: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(pem.encode())
    if not isinstance(key, RSAPublicKey):
        raise ValueError("JWT public key must be RSA")
    return str(jwt.algorithms.RSAAlgorithm.to_jwk(key))


_USER_SQL = text("SELECT disabled FROM users WHERE user_id = :u")
_GROUPS_SQL = text("SELECT group_name FROM user_groups WHERE user_id = :u")
_ROLES_SQL = text("SELECT role FROM user_roles WHERE user_id = :u")


def resolve_identity(conn: Connection, user_id: str) -> Identity | None:
    """Load groups and roles from Postgres. None for unknown or disabled users (-> 401)."""
    disabled = conn.execute(_USER_SQL, {"u": user_id}).scalar()
    if disabled is None or bool(disabled):
        return None
    groups = frozenset(str(g) for g in conn.execute(_GROUPS_SQL, {"u": user_id}).scalars())
    roles = frozenset(str(r) for r in conn.execute(_ROLES_SQL, {"u": user_id}).scalars())
    return Identity(user_id=user_id, groups=groups, roles=roles)


def mint_token(
    private_key_pem: str,
    *,
    sub: str,
    issuer: str,
    audience: str,
    lifetime_seconds: int = 900,
    extra_claims: dict[str, Any] | None = None,
    kid: str = "test-key",
) -> str:
    """Sign a test token with the local RSA key (tests, dev, CI only)."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + lifetime_seconds,
        "jti": str(uuid.uuid4()),
    }
    claims.update(extra_claims or {})
    return jwt.encode(claims, private_key_pem, algorithm=ALGORITHM, headers={"kid": kid})


def generate_rsa_keypair() -> tuple[str, str]:
    """(private_pem, public_pem) for local test tokens."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem
