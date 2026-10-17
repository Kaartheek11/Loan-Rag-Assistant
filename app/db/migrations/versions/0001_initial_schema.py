"""Initial schema (spec section 10).

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            user_id     TEXT PRIMARY KEY,
            display     TEXT NOT NULL,
            disabled    BOOLEAN NOT NULL DEFAULT false
        );

        CREATE TABLE user_groups (
            user_id     TEXT NOT NULL REFERENCES users(user_id),
            group_name  TEXT NOT NULL,
            PRIMARY KEY (user_id, group_name)
        );

        CREATE TABLE user_roles (
            user_id     TEXT NOT NULL REFERENCES users(user_id),
            role        TEXT NOT NULL CHECK (role IN ('admin')),
            PRIMARY KEY (user_id, role)
        );

        CREATE TABLE documents (
            doc_id         UUID PRIMARY KEY,
            filename       TEXT NOT NULL,
            sha256         CHAR(64) NOT NULL UNIQUE,
            page_count     INTEGER NOT NULL,
            ocr_pages      INTEGER[] NOT NULL DEFAULT '{}',
            status         TEXT NOT NULL CHECK (status IN ('pending','indexed','failed')),
            failure_reason TEXT,
            acl_groups     TEXT[] NOT NULL DEFAULT '{}',
            acl_users      TEXT[] NOT NULL DEFAULT '{}',
            ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at     TIMESTAMPTZ
        );
        CREATE INDEX documents_status_idx ON documents (status);

        CREATE TABLE pages (
            doc_id      UUID NOT NULL REFERENCES documents(doc_id),
            page        INTEGER NOT NULL,
            text        TEXT NOT NULL,
            PRIMARY KEY (doc_id, page)
        );

        CREATE TABLE chunks (
            chunk_id     BIGSERIAL PRIMARY KEY,
            doc_id       UUID NOT NULL REFERENCES documents(doc_id),
            page         INTEGER NOT NULL,
            section      TEXT,
            text         TEXT NOT NULL,
            token_count  INTEGER NOT NULL,
            identifiers  TEXT[] NOT NULL DEFAULT '{}',
            acl_groups   TEXT[] NOT NULL DEFAULT '{}',
            acl_users    TEXT[] NOT NULL DEFAULT '{}',
            tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
            deleted_at   TIMESTAMPTZ
        );
        CREATE INDEX chunks_tsv_idx         ON chunks USING GIN (tsv);
        CREATE INDEX chunks_identifiers_idx ON chunks USING GIN (identifiers);
        CREATE INDEX chunks_acl_groups_idx  ON chunks USING GIN (acl_groups);
        CREATE INDEX chunks_acl_users_idx   ON chunks USING GIN (acl_users);
        CREATE INDEX chunks_doc_idx         ON chunks (doc_id);

        CREATE TABLE index_state (
            id               SMALLINT PRIMARY KEY CHECK (id = 1),
            version          BIGINT NOT NULL DEFAULT 0,
            embedding_model  TEXT NOT NULL,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE audit_log (
            id                  BIGSERIAL PRIMARY KEY,
            ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            request_id          UUID NOT NULL,
            user_id             TEXT NOT NULL,
            query_hmac          CHAR(64) NOT NULL,
            prompt_version      TEXT NOT NULL,
            returned_chunk_ids  BIGINT[] NOT NULL,
            cited_chunk_ids     BIGINT[] NOT NULL,
            status              TEXT NOT NULL CHECK (status IN ('answered','insufficient','error')),
            retrieval_rounds    SMALLINT NOT NULL,
            latency_ms          INTEGER NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit_log;
        DROP TABLE index_state;
        DROP TABLE chunks;
        DROP TABLE pages;
        DROP TABLE documents;
        DROP TABLE user_roles;
        DROP TABLE user_groups;
        DROP TABLE users;
        """
    )
