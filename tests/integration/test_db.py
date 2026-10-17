"""Phase 2: migrations, seed-users, append-only audit_log for the app role."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from app.db.seed import SeedError, seed_users

REPO = Path(__file__).resolve().parents[2]
REALM = REPO / "docker" / "keycloak" / "realm-loanrag.json"
SEED = REPO / "config" / "users_seed.yaml"


def test_all_tables_exist(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        names = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    assert {
        "users",
        "user_groups",
        "user_roles",
        "documents",
        "pages",
        "chunks",
        "index_state",
        "audit_log",
    } <= names


def _insert_audit(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            "INSERT INTO audit_log (request_id, user_id, query_hmac, prompt_version, "
            "returned_chunk_ids, cited_chunk_ids, status, retrieval_rounds, latency_ms) "
            "VALUES (:r, 'u_x', :h, 'v1', '{}', '{}', 'insufficient', 1, 5)"
        ),
        {"r": uuid.uuid4(), "h": "0" * 64},
    )


def test_audit_log_is_append_only_for_app_role(app_engine: Engine, clean_db: Engine) -> None:
    with app_engine.begin() as conn:
        _insert_audit(conn)
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 1
    for stmt in (
        "UPDATE audit_log SET status = 'error'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with pytest.raises(ProgrammingError), app_engine.begin() as conn:
            conn.execute(text(stmt))


def test_seed_users_loads_groups_and_roles(app_engine: Engine, clean_db: Engine) -> None:
    n = seed_users(app_engine, SEED, REALM)
    assert n >= 8
    with app_engine.connect() as conn:
        groups = set(
            conn.execute(
                text("SELECT group_name FROM user_groups WHERE user_id = 'u_alice'")
            ).scalars()
        )
        roles = set(
            conn.execute(text("SELECT role FROM user_roles WHERE user_id = 'u_admin'")).scalars()
        )
        admin_groups = conn.execute(
            text("SELECT count(*) FROM user_groups WHERE user_id = 'u_admin'")
        ).scalar_one()
    assert groups == {"underwriting", "western_region"}
    assert roles == {"admin"}
    assert admin_groups == 0
    # Idempotent.
    seed_users(app_engine, SEED, REALM)


def test_seed_users_fails_when_realm_differs(
    app_engine: Engine, clean_db: Engine, tmp_path: Path
) -> None:
    realm = json.loads(REALM.read_text())
    realm["users"] = realm["users"][:-1]
    bad_realm = tmp_path / "realm.json"
    bad_realm.write_text(json.dumps(realm))
    with pytest.raises(SeedError):
        seed_users(app_engine, SEED, bad_realm)

    seed = yaml.safe_load(SEED.read_text())
    seed["users"].append({"user_id": "u_intruder", "display": "X", "groups": [], "roles": []})
    bad_seed = tmp_path / "users.yaml"
    bad_seed.write_text(yaml.safe_dump(seed))
    with pytest.raises(SeedError):
        seed_users(app_engine, bad_seed, REALM)
