"""One audit row per query (§10). Stores IDs, status, latency and a keyed HMAC of the
question, never question, answer or chunk text."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import Connection, Engine, insert

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
                    event="query",
                )
            )

    def write_session_start(self, *, request_id: str, user_id: str) -> None:
        """A `session_start` event (BE-4): no question hash and empty chunk arrays."""
        with self._engine.begin() as conn:
            conn.execute(
                insert(audit_log).values(
                    request_id=uuid.UUID(request_id),
                    user_id=user_id,
                    event="session_start",
                    returned_chunk_ids=[],
                    cited_chunk_ids=[],
                )
            )


class SessionStartRecorder:
    """Writes at most one `session_start` per token (BE-4).

    Tokens are tracked by `jti` in memory with a TTL a little longer than the 15-minute token
    lifetime, so window-focus refreshes of /v1/me do not flood the audit log.
    """

    def __init__(
        self,
        audit: AuditLogger,
        ttl_seconds: float = 20 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._audit = audit
        self._ttl = ttl_seconds
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def record(self, *, token_id: str, request_id: str, user_id: str) -> bool:
        now = self._clock()
        with self._lock:
            for key in [k for k, exp in self._seen.items() if exp <= now]:
                del self._seen[key]
            if token_id in self._seen:
                return False
            self._seen[token_id] = now + self._ttl
        self._audit.write_session_start(request_id=request_id, user_id=user_id)
        return True


ADMIN_EVENTS = ("upload", "acl_change", "delete")


def write_admin_event(conn: Connection, *, event: str, user_id: str, doc_id: uuid.UUID) -> None:
    """BE-13: an admin action on a document. IDs and a timestamp only; no document content.

    Takes a connection so the event commits in the same transaction as the change itself.
    """
    if event not in ADMIN_EVENTS:
        raise ValueError(f"unknown admin event {event!r}")
    conn.execute(
        insert(audit_log).values(
            request_id=uuid.uuid4(),
            user_id=user_id,
            event=event,
            doc_id=doc_id,
            returned_chunk_ids=[],
            cited_chunk_ids=[],
        )
    )
