"""LLM adapters (§8.7): Ollama (default), one OpenAI-compatible hosted adapter, Fake (tests)."""

from __future__ import annotations

import contextlib
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import Settings

TEMPERATURE = 0.0
MAX_TOKENS = 512


class LLMUnavailable(RuntimeError):
    """The model could not produce an answer (maps to 503)."""


class LLMAdapter(Protocol):
    name: str

    def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str: ...

    def healthy(self) -> bool: ...


class OllamaAdapter:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=timeout)

    def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": temperature, "num_predict": max_tokens, "seed": 0},
        }
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return str(resp.json()["message"]["content"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMUnavailable(type(exc).__name__) from exc

    def healthy(self) -> bool:
        try:
            return self._client.get(f"{self.base_url}/api/tags", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False


class HostedAdapter:
    """Any OpenAI-compatible chat completions endpoint (Groq, Gemini). No silent fallback."""

    name = "hosted"
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
        self._sleep = sleep

    def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        import openai

        for attempt in range(self.MAX_ATTEMPTS):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content or ""
            except openai.RateLimitError as exc:
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise LLMUnavailable("rate_limited") from exc
                self._sleep(_backoff(attempt, exc.response.headers.get("retry-after")))
            except openai.OpenAIError as exc:
                raise LLMUnavailable(type(exc).__name__) from exc
        raise LLMUnavailable("rate_limited")

    def healthy(self) -> bool:
        return True  # do not spend free-tier quota on health checks


def _backoff(attempt: int, retry_after: str | None) -> float:
    base = float(2**attempt)  # 1, 2, 4, 8
    if retry_after:
        with contextlib.suppress(ValueError):
            base = max(base, float(retry_after))
    return base + random.uniform(0, 0.5)  # noqa: S311 - jitter


# ---------------------------------------------------------------------------------------
# FakeAdapter: deterministic, for tests and offline eval. Never used in production.
# ---------------------------------------------------------------------------------------
_SOURCE_BLOCK = re.compile(
    r'<source label="(S\d+)" document="[^"]*" page="\d+">\n(.*?)\n</source>', re.DOTALL
)
_STOP = set(
    {
        "the",
        "a",
        "an",
        "of",
        "on",
        "in",
        "for",
        "to",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "does",
        "do",
        "did",
        "has",
        "have",
        "had",
        "be",
        "this",
        "that",
        "these",
        "those",
        "with",
        "by",
        "from",
        "at",
        "as",
        "it",
        "its",
        "loan",
        "please",
        "tell",
        "me",
        "about",
        "any",
        "there",
        "their",
        "they",
    }
)
_WORD = re.compile(r"[a-z0-9$][a-z0-9$,.%\-]*[a-z0-9%]|[a-z0-9]", re.IGNORECASE)


def _terms(text: str) -> set[str]:
    return {w.lower().strip(".,") for w in _WORD.findall(text) if w.lower() not in _STOP}


@dataclass
class FakeAdapter:
    """Scripted answers. With `responses`, returns them in order (the last one repeats).
    With `responder`, calls it. Otherwise answers extractively from the best-matching source."""

    responses: list[str] | None = None
    responder: Callable[[str, str], str] | None = None
    min_overlap: int = 2
    calls: list[tuple[str, str]] = field(default_factory=list)
    name: str = "fake"

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        with self._lock:
            self.calls.append((system, user))
            if self.responses is not None:
                idx = min(len(self.calls) - 1, len(self.responses) - 1)
                return self.responses[idx]
        if self.responder is not None:
            return self.responder(system, user)
        return self._extractive(user)

    def _extractive(self, user: str) -> str:
        question = user.rsplit("\nQUESTION\n", 1)[-1]
        q_terms = _terms(question)
        q_loans = set(re.findall(r"LN-\d{4}-\d{6}", question.upper()))
        best: tuple[int, str, str] | None = None
        for label, body in _SOURCE_BLOCK.findall(user):
            if q_loans and not q_loans & set(re.findall(r"LN-\d{4}-\d{6}", body.upper())):
                continue
            for sentence in re.split(r"(?<=[.!?])\s+|\n", body):
                score = len(q_terms & _terms(sentence))
                if best is None or score > best[0]:
                    best = (score, label, sentence.strip())
        if best is None or best[0] < self.min_overlap:
            return "INSUFFICIENT_CONTEXT"
        sentence = best[2].rstrip(".")
        return f"{sentence} [{best[1]}]."

    def healthy(self) -> bool:
        return True


def build_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_provider == "ollama":
        return OllamaAdapter(settings.ollama_base_url, settings.ollama_model)
    if settings.llm_provider == "hosted":
        if settings.hosted_llm_api_key is None or not settings.hosted_llm_model:
            raise LLMUnavailable("hosted provider not configured")
        return HostedAdapter(
            settings.hosted_llm_base_url,
            settings.hosted_llm_model,
            settings.hosted_llm_api_key.get_secret_value(),
        )
    return FakeAdapter()
