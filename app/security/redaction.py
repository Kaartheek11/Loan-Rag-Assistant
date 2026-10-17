"""Structured JSON logging with a redaction processor (S2, S13, T12).

Every log record (structlog and stdlib, including uvicorn/httpx/sqlalchemy) passes through
`Redactor`, which:
  - drops the values of content-bearing keys (question, answer, text, prompt, ...),
  - masks anything matching the loan-number pattern,
  - masks the literal values of known secrets.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, MutableMapping
from typing import Any, TextIO

import structlog

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset(
    {
        "question",
        "answer",
        "text",
        "chunk_text",
        "prompt",
        "system",
        "user",
        "content",
        "messages",
        "body",
        "authorization",
        "token",
        "password",
        "api_key",
        "secret",
    }
)
_LOAN_NUMBER = re.compile(r"LN-\d{4}-\d{6}", re.IGNORECASE)


class Redactor:
    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        self._secrets = sorted(
            {s for s in secret_values if s and len(s) >= 4}, key=len, reverse=True
        )

    def scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            out = _LOAN_NUMBER.sub("[LOAN_NUMBER]", value)
            for secret in self._secrets:
                out = out.replace(secret, REDACTED)
            return out
        if isinstance(value, dict):
            return {
                k: (REDACTED if str(k).lower() in SENSITIVE_KEYS else self.scrub(v))
                for k, v in value.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [self.scrub(v) for v in value]
        return value

    def __call__(
        self, logger: Any, method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        for key in list(event_dict.keys()):
            if key.lower() in SENSITIVE_KEYS:
                event_dict[key] = REDACTED
            else:
                event_dict[key] = self.scrub(event_dict[key])
        return event_dict


def _drop_exception_detail(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Exceptions are logged by type only; messages and tracebacks can carry document text."""
    exc = event_dict.pop("exc_info", None)
    event_dict.pop("exception", None)
    event_dict.pop("stack", None)
    if exc:
        if exc is True:
            exc = sys.exc_info()
        if isinstance(exc, BaseException):
            event_dict["error_type"] = type(exc).__name__
        elif isinstance(exc, tuple) and exc and exc[0] is not None:
            event_dict["error_type"] = exc[0].__name__
    return event_dict


def configure_logging(
    secret_values: Iterable[str] = (), *, stream: TextIO | None = None, level: int = logging.INFO
) -> Redactor:
    redactor = Redactor(secret_values)
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _drop_exception_detail,
        redactor,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "httpx",
        "httpcore",
        "sqlalchemy",
        "sentence_transformers",
        "alembic",
    ):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    return redactor
