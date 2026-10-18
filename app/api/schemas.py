"""Request and response models (§9). Every request model forbids unknown fields (S9)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=10)


class Citation(BaseModel):
    marker: int
    doc_id: str
    filename: str
    page: int
    excerpt: str  # the cited chunk's text, already permitted for this request (BE-3)


class QueryResponse(BaseModel):
    request_id: str
    status: Literal["answered", "insufficient"]
    answer: str
    citations: list[Citation]


class PageResponse(BaseModel):
    doc_id: str
    filename: str
    page: int
    text: str
    page_count: int  # for "Page 63 of 87" and "Go to page" (extension.md §8.7)
    section: str | None  # section of the page's first chunk (extension.md §8.7)


class MeResponse(BaseModel):
    user_id: str
    display_name: str
    groups: list[str]
    roles: list[str]
    readable_document_count: int


class MyDocument(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    ingested_at: datetime


class MyDocumentsResponse(BaseModel):
    items: list[MyDocument]
    next_cursor: str | None


class AclUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[str] = Field(default_factory=list, max_length=100)
    users: list[str] = Field(default_factory=list, max_length=1000)


class UploadProgress(BaseModel):
    job_id: str
    status: str
    pages_done: int
    pages_total: int
    failure_reason: str | None


class AdminDocument(BaseModel):
    """BE-12: metadata only, never page or chunk text."""

    doc_id: str
    filename: str
    page_count: int
    status: str
    failure_reason: str | None
    groups: list[str]
    users: list[str]
    uploaded_by: str | None
    ingested_at: datetime
    upload: UploadProgress | None


class DocumentSummary(BaseModel):
    total: int
    processing: int
    failed: int


class AdminDocumentsResponse(BaseModel):
    items: list[AdminDocument]
    next_cursor: str | None
    summary: DocumentSummary


class UploadAccepted(BaseModel):
    job_id: str
    doc_id: str
    filename: str
    status: Literal["queued"]


class UploadJob(BaseModel):
    job_id: str
    doc_id: str
    filename: str
    status: str
    pages_done: int
    pages_total: int
    failure_reason: str | None
    created_at: datetime
    uploaded_by: str


class UploadJobsResponse(BaseModel):
    items: list[UploadJob]
    next_cursor: str | None


class AdminUser(BaseModel):
    user_id: str
    display: str


class DocumentStatus(BaseModel):
    doc_id: str
    filename: str
    status: str
    failure_reason: str | None
    page_count: int


class HealthResponse(BaseModel):
    status: str
    db: str
    index_version: int | None
    llm: str
