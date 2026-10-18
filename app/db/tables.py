"""SQLAlchemy Core table definitions mirroring the schema in spec §10.

The schema itself is created by Alembic migrations (raw SQL), not by metadata.create_all().
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR, UUID

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("display", Text, nullable=False),
    Column("disabled", Boolean, nullable=False),
)

user_groups = Table(
    "user_groups",
    metadata,
    Column("user_id", Text, ForeignKey("users.user_id"), primary_key=True),
    Column("group_name", Text, primary_key=True),
)

user_roles = Table(
    "user_roles",
    metadata,
    Column("user_id", Text, ForeignKey("users.user_id"), primary_key=True),
    Column("role", Text, primary_key=True),
)

documents = Table(
    "documents",
    metadata,
    Column("doc_id", UUID(as_uuid=True), primary_key=True),
    Column("filename", Text, nullable=False),
    Column("sha256", String(64), nullable=False),  # unique among non-deleted rows (0005)
    Column("page_count", Integer, nullable=False),
    Column("ocr_pages", ARRAY(Integer), nullable=False),
    Column("status", Text, nullable=False),
    Column("failure_reason", Text),
    Column("acl_groups", ARRAY(Text), nullable=False),
    Column("acl_users", ARRAY(Text), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    Column("loan_number", Text),
    Column("borrower_name", Text),
)

pages = Table(
    "pages",
    metadata,
    Column("doc_id", UUID(as_uuid=True), ForeignKey("documents.doc_id"), primary_key=True),
    Column("page", Integer, primary_key=True),
    Column("text", Text, nullable=False),
)

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", BigInteger, primary_key=True, autoincrement=True),
    Column("doc_id", UUID(as_uuid=True), ForeignKey("documents.doc_id"), nullable=False),
    Column("page", Integer, nullable=False),
    Column("section", Text),
    Column("text", Text, nullable=False),
    Column("token_count", Integer, nullable=False),
    Column("identifiers", ARRAY(Text), nullable=False),
    Column("acl_groups", ARRAY(Text), nullable=False),
    Column("acl_users", ARRAY(Text), nullable=False),
    Column("context_prefix", Text, nullable=False, server_default=""),
    Column(
        "tsv",
        TSVECTOR,
        Computed("to_tsvector('simple', context_prefix || ' ' || text)", persisted=True),
    ),
    Column("deleted_at", DateTime(timezone=True)),
)

index_state = Table(
    "index_state",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("embedding_model", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("request_id", UUID(as_uuid=True), nullable=False),
    Column("user_id", Text, nullable=False),
    Column("query_hmac", String(64)),  # NULL for non-query events (BE-4)
    Column("prompt_version", Text),
    Column("returned_chunk_ids", ARRAY(BigInteger), nullable=False),
    Column("cited_chunk_ids", ARRAY(BigInteger), nullable=False),
    Column("status", Text),
    Column("retrieval_rounds", SmallInteger),
    Column("latency_ms", Integer),
    Column("event", Text, nullable=False, server_default="query"),
    Column("doc_id", UUID(as_uuid=True)),  # admin events: upload, acl_change, delete (BE-13)
)

upload_jobs = Table(
    "upload_jobs",
    metadata,
    Column("job_id", UUID(as_uuid=True), primary_key=True),
    Column("doc_id", UUID(as_uuid=True), ForeignKey("documents.doc_id"), nullable=False),
    Column("uploaded_by", Text, ForeignKey("users.user_id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("pages_done", Integer, nullable=False, server_default="0"),
    Column("pages_total", Integer, nullable=False),
    Column("failure_reason", Text),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
)
