"""One audit row per query (§10). Stores IDs, status, latency and a keyed HMAC of the
question, never question, answer or chunk text."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Sequence

from sqlalchemy import Engine, insert

from app.db.tables import audit_log


def question_hmac(key: str, question: str) -> str:
    return hmac.new(key.encode(), question.encode(), hashlib.sha256).hexdigest()


class AuditLogger:
    def __init__(self, engine: Engine, hmac_key: str) -> None:
        if not hmac_key:
            raise ValueError("AUDIT_HMAC_KEY is required")
        self._engine = engine
        self._key = hmac_key

    def write(
        self,
        *,
        request_id: str,
        user_id: str,
        question: str,
        prompt_version: str,
        returned_chunk_ids: Sequence[int],
        cited_chunk_ids: Sequence[int],
        status: str,
        retrieval_rounds: int,
        latency_ms: int,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(audit_log).values(
                    request_id=uuid.UUID(request_id),
                    user_id=user_id,
                    query_hmac=question_hmac(self._key, question),
                    prompt_version=prompt_version,
                    returned_chunk_ids=list(returned_chunk_ids),
                    cited_chunk_ids=list(cited_chunk_ids),
                    status=status,
                    retrieval_rounds=min(retrieval_rounds, 32767),
                    latency_ms=latency_ms,
                )
            )
