"""Request and response models (§9). Every request model forbids unknown fields (S9)."""

from __future__ import annotations

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


class AclUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[str] = Field(default_factory=list, max_length=100)
    users: list[str] = Field(default_factory=list, max_length=1000)


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
