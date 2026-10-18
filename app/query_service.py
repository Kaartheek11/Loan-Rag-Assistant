"""The query service (§5.3): retrieval, generation, citation validation, audit.

Blocking work (DB, FAISS, embedding, model calls) runs here, synchronously; the API calls
`answer()` through a thread pool so the event loop is never blocked.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import Engine

from app.audit.audit_log import AuditLogger
from app.generation.citations import render, validate
from app.generation.llm_adapter import MAX_TOKENS, TEMPERATURE, LLMAdapter, LLMUnavailable
from app.generation.prompts import PROMPT_VERSION, build_prompt
from app.retrieval.embedder import Embedder
from app.retrieval.index_manager import IndexHolder, IndexUnavailable
from app.retrieval.retriever import Mode, Retriever
from app.security.identity import Identity
from app.security.permissions import PermittedSet

log = structlog.get_logger(__name__)

# One fixed wording for every insufficient case (nothing visible matched, the model found no
# answer, or citations failed twice), so it never reveals whether a hidden file matched (§6.6).
# Wording changed from task.md §9.2 at the owner's request (architecture D58).
INSUFFICIENT_ANSWER = "I couldn't find information to answer that in the files you have access to."


class ServiceUnavailable(RuntimeError):
    """Index or LLM unavailable (503). The client is not told which."""


@dataclass
class QueryOutcome:
    request_id: str
    status: str  # answered | insufficient
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    returned_chunk_ids: list[int] = field(default_factory=list)
    cited_chunk_ids: list[int] = field(default_factory=list)
    retrieval_rounds: int = 0
    llm_calls: int = 0
    ranked_chunk_ids: list[int] = field(default_factory=list)  # eval only, never returned
    latency_ms: int = 0

    def response_body(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "answer": self.answer,
            "citations": self.citations,
        }


class QueryService:
    def __init__(
        self,
        *,
        engine: Engine,
        embedder: Embedder,
        index: IndexHolder,
        llm: LLMAdapter,
        audit: AuditLogger,
        n_start: int,
        n_max: int,
    ) -> None:
        self.engine = engine
        self.embedder = embedder
        self.index = index
        self.llm = llm
        self.audit = audit
        self.retriever = Retriever(index, n_start=n_start, n_max=n_max)

    def answer(
        self,
        identity: Identity,
        question: str,
        top_k: int,
        *,
        retrieval_k: int | None = None,
        mode: Mode = "hybrid",
        request_id: str | None = None,
    ) -> QueryOutcome:
        started = time.perf_counter()
        request_id = request_id or str(uuid.uuid4())
        outcome = QueryOutcome(request_id, "insufficient", INSUFFICIENT_ANSWER)
        status_for_audit = "error"
        try:
            with self.engine.connect() as conn:
                self.index.ensure_current(conn)
                qvec = self.embedder.embed_query(question)
                result = self.retriever.retrieve(
                    conn,
                    identity,
                    question,
                    qvec,
                    max(top_k, retrieval_k or 0),
                    request_id,
                    mode=mode,
                )
            outcome.retrieval_rounds = result.rounds
            outcome.ranked_chunk_ids = result.permitted.ids()
            permitted = PermittedSet(request_id, result.permitted.chunks[:top_k])
            outcome.returned_chunk_ids = permitted.ids()
            if permitted.chunks:
                self._generate(permitted, question, outcome)
            status_for_audit = outcome.status
            return outcome
        except (IndexUnavailable, LLMUnavailable) as exc:
            log.warning("query_unavailable", request_id=request_id, error_type=type(exc).__name__)
            raise ServiceUnavailable() from exc
        finally:
            outcome.latency_ms = int((time.perf_counter() - started) * 1000)
            self.audit.write(
                request_id=request_id,
                user_id=identity.user_id,
                question=question,
                prompt_version=PROMPT_VERSION,
                returned_chunk_ids=outcome.returned_chunk_ids,
                cited_chunk_ids=outcome.cited_chunk_ids,
                status=status_for_audit,
                retrieval_rounds=outcome.retrieval_rounds,
                latency_ms=outcome.latency_ms,
            )
            log.info(
                "query",
                request_id=request_id,
                user_id=identity.user_id,
                status=status_for_audit,
                rounds=outcome.retrieval_rounds,
                returned=len(outcome.returned_chunk_ids),
                cited=len(outcome.cited_chunk_ids),
                llm_calls=outcome.llm_calls,
                latency_ms=outcome.latency_ms,
            )

    def _generate(self, permitted: PermittedSet, question: str, outcome: QueryOutcome) -> None:
        prompt = build_prompt(permitted, permitted.chunks, question)  # E2
        for attempt in range(2):
            p = prompt if attempt == 0 else prompt.with_retry()
            outcome.llm_calls += 1
            raw = self.llm.generate(
                p.system, p.user, temperature=TEMPERATURE, max_tokens=MAX_TOKENS
            )
            check = validate(raw, len(prompt.labels))
            if check.status == "insufficient":
                return
            if check.status == "ok":
                text, citations = render(raw, check.labels, prompt.labels, permitted)  # E3
                outcome.status = "answered"
                outcome.answer = text
                outcome.citations = [
                    {
                        "marker": c.marker,
                        "doc_id": c.chunk.doc_id,
                        "filename": c.chunk.filename,
                        "page": c.chunk.page,
                        # Raw chunk text, never the context prefix (BE-3, §17.2).
                        "excerpt": c.chunk.text,
                    }
                    for c in citations
                ]
                outcome.cited_chunk_ids = [c.chunk.chunk_id for c in citations]
                return
            log.info(
                "citation_retry",
                request_id=permitted.request_id,
                reason=check.reason,
                attempt=attempt + 1,
            )
        # Second failure: never return invalid citations.
