"""Grants for the restricted application role (spec section 10 notes, S12).

The app role may read and write the working tables, but on audit_log it gets only
INSERT and SELECT, so the audit trail is append-only at the database permission level.
The role is created NOLOGIN here if it does not exist; deployments give it a password
(docker/postgres/init-app-role.sh, or the test fixtures).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17
"""

import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

WORK_TABLES = ["users", "user_groups", "user_roles", "documents", "pages", "chunks", "index_state"]


def _role() -> str:
    role = os.environ.get("APP_DB_ROLE", "loanrag_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("APP_DB_ROLE must be alphanumeric/underscore")
    return role


def upgrade() -> None:
    role = _role()
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    for table in WORK_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")
    op.execute(f"REVOKE ALL ON audit_log FROM {role}")
    op.execute(f"GRANT SELECT, INSERT ON audit_log TO {role}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE chunks_chunk_id_seq, audit_log_id_seq TO {role}")


def downgrade() -> None:
    role = _role()
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
