"""Engine creation. The API and CLI use DATABASE_URL (the restricted app role)."""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import Engine, create_engine

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def make_engine(url: str, *, environment: str = "development", pool_size: int = 10) -> Engine:
    connect_args: dict[str, str] = {}
    host = urlparse(url.replace("+psycopg", "")).hostname or ""
    # S5: TLS when the database is not on localhost (Compose service names count as remote
    # in production; in development the compose network is trusted).
    if host not in _LOCAL_HOSTS and environment == "production":
        connect_args["sslmode"] = "require"
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
