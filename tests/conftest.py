"""Shared fixtures: real Postgres (testcontainers), migrated as owner, used as the app role."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text

APP_ROLE = "loanrag_app"
APP_PASSWORD = "app-test-password"  # throwaway, container-local


@pytest.fixture(scope="session")
def pg_urls() -> Iterator[dict[str, str]]:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.4", driver="psycopg") as pg:
        owner_url = pg.get_connection_url()
        owner = create_engine(owner_url)
        with owner.begin() as conn:
            conn.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
        owner.dispose()
        os.environ["APP_DB_ROLE"] = APP_ROLE
        from app.db.seed import run_migrations

        run_migrations(owner_url)
        host_part = owner_url.split("@", 1)[1]
        app_url = f"postgresql+psycopg://{APP_ROLE}:{APP_PASSWORD}@{host_part}"
        yield {"owner": owner_url, "app": app_url}


@pytest.fixture(scope="session")
def owner_engine(pg_urls: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(pg_urls["owner"])
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(pg_urls: dict[str, str]) -> Iterator[Engine]:
    engine = create_engine(pg_urls["app"], pool_size=10)
    yield engine
    engine.dispose()


def truncate_all(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE audit_log, chunks, pages, documents, user_groups, user_roles, users, "
                "index_state RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def clean_db(owner_engine: Engine) -> Engine:
    truncate_all(owner_engine)
    return owner_engine


@pytest.fixture(scope="session")
def embedder():  # type: ignore[no-untyped-def]
    from app.retrieval.embedder import Embedder

    return Embedder("BAAI/bge-small-en-v1.5")


@pytest.fixture(scope="session")
def keypair(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    from app.security.auth import generate_rsa_keypair

    private_pem, public_pem = generate_rsa_keypair()
    path = tmp_path_factory.mktemp("keys") / "test_public.pem"
    path.write_text(public_pem)
    return private_pem, path


@pytest.fixture
def env(tmp_path, pg_urls, app_engine, owner_engine, embedder, keypair):  # type: ignore[no-untyped-def]
    from tests.env import build_env

    with build_env(
        tmp=tmp_path,
        pg_urls=pg_urls,
        app_engine=app_engine,
        owner_engine=owner_engine,
        embedder=embedder,
        keypair=keypair,
    ) as e:
        yield e
