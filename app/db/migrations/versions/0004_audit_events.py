"""Audit event types: query and session_start (extension.md BE-4).

`session_start` rows (written by GET /v1/me) have no question hash and empty chunk arrays.
The query-specific columns become nullable, and a CHECK keeps them required for `query` rows,
so version 1's guarantees for query rows are unchanged.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE audit_log
            ADD COLUMN event TEXT NOT NULL DEFAULT 'query'
                CHECK (event IN ('query', 'session_start'));
        ALTER TABLE audit_log ALTER COLUMN query_hmac DROP NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN prompt_version DROP NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN status DROP NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN retrieval_rounds DROP NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN latency_ms DROP NOT NULL;
        ALTER TABLE audit_log ADD CONSTRAINT audit_query_fields_required CHECK (
            event <> 'query' OR (
                query_hmac IS NOT NULL AND prompt_version IS NOT NULL AND status IS NOT NULL
                AND retrieval_rounds IS NOT NULL AND latency_ms IS NOT NULL
            )
        );
        ALTER TABLE audit_log ADD CONSTRAINT audit_session_start_shape CHECK (
            event <> 'session_start' OR (
                query_hmac IS NULL AND cardinality(returned_chunk_ids) = 0
                AND cardinality(cited_chunk_ids) = 0
            )
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM audit_log WHERE event <> 'query';
        ALTER TABLE audit_log DROP CONSTRAINT audit_session_start_shape;
        ALTER TABLE audit_log DROP CONSTRAINT audit_query_fields_required;
        ALTER TABLE audit_log ALTER COLUMN latency_ms SET NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN retrieval_rounds SET NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN status SET NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN prompt_version SET NOT NULL;
        ALTER TABLE audit_log ALTER COLUMN query_hmac SET NOT NULL;
        ALTER TABLE audit_log DROP COLUMN event;
        """
    )
