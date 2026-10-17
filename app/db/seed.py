"""Database bootstrap helpers: migrations, user seeding, index_state row."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.tables import index_state, user_groups, user_roles, users

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class SeedError(RuntimeError):
    pass


def run_migrations(url: str) -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "app" / "db" / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


def load_users_seed(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("users") or []
    for u in entries:
        if not u.get("user_id"):
            raise SeedError("every user needs a user_id")
    return list(entries)


def realm_user_ids(realm_path: Path) -> set[str]:
    realm = json.loads(realm_path.read_text())
    return {str(u["id"]) for u in realm.get("users", [])}


def check_realm_matches(seed_users: list[dict[str, Any]], realm_path: Path) -> None:
    seed_ids = {str(u["user_id"]) for u in seed_users}
    kc_ids = realm_user_ids(realm_path)
    if seed_ids != kc_ids:
        only_seed = sorted(seed_ids - kc_ids)
        only_kc = sorted(kc_ids - seed_ids)
        raise SeedError(
            f"users_seed.yaml and the Keycloak realm list different users "
            f"(only in seed: {only_seed}; only in realm: {only_kc})"
        )


def seed_users(engine: Engine, seed_path: Path, realm_path: Path) -> int:
    """Upsert users and replace their groups and roles. Fails if IDs differ from the realm."""
    entries = load_users_seed(seed_path)
    check_realm_matches(entries, realm_path)
    with engine.begin() as conn:
        for u in entries:
            uid = str(u["user_id"])
            stmt = insert(users).values(
                user_id=uid, display=str(u.get("display") or uid), disabled=bool(u.get("disabled"))
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[users.c.user_id],
                    set_={"display": stmt.excluded.display, "disabled": stmt.excluded.disabled},
                )
            )
            conn.execute(delete(user_groups).where(user_groups.c.user_id == uid))
            conn.execute(delete(user_roles).where(user_roles.c.user_id == uid))
            for g in u.get("groups") or []:
                conn.execute(insert(user_groups).values(user_id=uid, group_name=str(g)))
            for r in u.get("roles") or []:
                conn.execute(insert(user_roles).values(user_id=uid, role=str(r)))
    return len(entries)


def ensure_index_state(conn: Connection, embedding_model: str) -> None:
    conn.execute(
        insert(index_state)
        .values(id=1, version=0, embedding_model=embedding_model, updated_at=text("now()"))
        .on_conflict_do_nothing(index_elements=[index_state.c.id])
    )


def read_index_state(conn: Connection) -> tuple[int, str] | None:
    row = conn.execute(
        select(index_state.c.version, index_state.c.embedding_model).where(index_state.c.id == 1)
    ).first()
    return (int(row.version), str(row.embedding_model)) if row else None
