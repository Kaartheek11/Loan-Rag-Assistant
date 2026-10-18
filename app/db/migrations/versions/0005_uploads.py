"""Admin uploads (extension.md §6.1 BE-8/BE-9) and admin audit events (BE-13).

- upload_jobs table, exactly as specified in §6.1.
- audit_log: events upload / acl_change / delete with the document they concern.
- documents.sha256: unique among non-deleted documents only, so the content of a deleted file
  can be uploaded again (§6.1 rule 7 checks duplicates "excluding soft-deleted documents").

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""

import os

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _role() -> str:
    role = os.environ.get("APP_DB_ROLE", "loanrag_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("APP_DB_ROLE must be alphanumeric/underscore")
    return role


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE upload_jobs (
            job_id          UUID PRIMARY KEY,
            doc_id          UUID NOT NULL REFERENCES documents(doc_id),
            uploaded_by     TEXT NOT NULL REFERENCES users(user_id),
            status          TEXT NOT NULL CHECK (status IN ('queued','processing','indexed','failed')),
            pages_done      INTEGER NOT NULL DEFAULT 0,
            pages_total     INTEGER NOT NULL,
            failure_reason  TEXT CHECK (failure_reason IN
                              ('unreadable_pdf','ocr_failed','no_text_found','internal_error')),
            heartbeat_at    TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at     TIMESTAMPTZ
        );
        CREATE INDEX upload_jobs_status_idx ON upload_jobs (status, created_at);
        CREATE INDEX upload_jobs_doc_idx ON upload_jobs (doc_id);

        ALTER TABLE documents DROP CONSTRAINT documents_sha256_key;
        CREATE UNIQUE INDEX documents_sha256_live_key ON documents (sha256)
            WHERE deleted_at IS NULL;
        CREATE INDEX documents_sha256_idx ON documents (sha256);

        ALTER TABLE audit_log ADD COLUMN doc_id UUID;
        ALTER TABLE audit_log DROP CONSTRAINT audit_log_event_check;
        ALTER TABLE audit_log ADD CONSTRAINT audit_log_event_check CHECK (
            event IN ('query', 'session_start', 'upload', 'acl_change', 'delete')
        );
        ALTER TABLE audit_log ADD CONSTRAINT audit_admin_event_shape CHECK (
            event NOT IN ('upload', 'acl_change', 'delete') OR (
                doc_id IS NOT NULL AND query_hmac IS NULL
                AND cardinality(returned_chunk_ids) = 0 AND cardinality(cited_chunk_ids) = 0
            )
        );
        """
    )
    role = _role()
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON upload_jobs TO {role}")


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM audit_log WHERE event IN ('upload', 'acl_change', 'delete');
        ALTER TABLE audit_log DROP CONSTRAINT audit_admin_event_shape;
        ALTER TABLE audit_log DROP CONSTRAINT audit_log_event_check;
        ALTER TABLE audit_log ADD CONSTRAINT audit_log_event_check CHECK (
            event IN ('query', 'session_start')
        );
        ALTER TABLE audit_log DROP COLUMN doc_id;
        DROP INDEX documents_sha256_idx;
        DROP INDEX documents_sha256_live_key;
        ALTER TABLE documents ADD CONSTRAINT documents_sha256_key UNIQUE (sha256);
        DROP TABLE upload_jobs;
        """
    )
