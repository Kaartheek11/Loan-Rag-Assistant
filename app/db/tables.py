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
    Column("sha256", String(64), nullable=False, unique=True),
    Column("page_count", Integer, nullable=False),
    Column("ocr_pages", ARRAY(Integer), nullable=False),
    Column("status", Text, nullable=False),
    Column("failure_reason", Text),
    Column("acl_groups", ARRAY(Text), nullable=False),
    Column("acl_users", ARRAY(Text), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
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
    Column("tsv", TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True)),
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
    Column("query_hmac", String(64), nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("returned_chunk_ids", ARRAY(BigInteger), nullable=False),
    Column("cited_chunk_ids", ARRAY(BigInteger), nullable=False),
    Column("status", Text, nullable=False),
    Column("retrieval_rounds", SmallInteger, nullable=False),
    Column("latency_ms", Integer, nullable=False),
)
