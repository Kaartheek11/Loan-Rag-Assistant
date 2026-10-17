from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    url = config.attributes.get("url") or os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        from app.config import get_settings

        url = get_settings().migrations_url
    return str(url)


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
