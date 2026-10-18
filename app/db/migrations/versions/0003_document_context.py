"""Document fields and chunk context prefix (extension.md §17.3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
            ADD COLUMN loan_number   TEXT,
            ADD COLUMN borrower_name TEXT;

        ALTER TABLE chunks
            ADD COLUMN context_prefix TEXT NOT NULL DEFAULT '';

        -- Redefine the generated full-text column to include the prefix.
        -- Dropping the column also drops its index, so recreate both.
        ALTER TABLE chunks DROP COLUMN tsv;
        ALTER TABLE chunks
            ADD COLUMN tsv TSVECTOR
            GENERATED ALWAYS AS (to_tsvector('simple', context_prefix || ' ' || text)) STORED;
        CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE chunks DROP COLUMN tsv;
        ALTER TABLE chunks
            ADD COLUMN tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED;
        CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv);
        ALTER TABLE chunks DROP COLUMN context_prefix;
        ALTER TABLE documents DROP COLUMN borrower_name, DROP COLUMN loan_number;
        """
    )
