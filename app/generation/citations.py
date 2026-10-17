"""Citation extraction and validation (§8.6). Enforcement point E3."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.security.permissions import PermittedSet, VisibleChunk

INSUFFICIENT_TOKEN = "INSUFFICIENT_CONTEXT"  # noqa: S105 - not a secret
_LABEL_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
_LABEL = re.compile(r"S(\d+)")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
UNCITED_WORD_LIMIT = 8  # an uncited sentence this long, or with a digit, counts as a factual claim


@dataclass
class Validation:
    status: str  # "ok" | "insufficient" | "invalid"
    labels: list[str] = field(default_factory=list)  # unique, first-appearance order
    reason: str = ""


def extract_labels(answer: str) -> list[str]:
    labels: list[str] = []
    for group in _LABEL_GROUP.finditer(answer):
        for n in _LABEL.finditer(group.group(1)):
            label = f"S{int(n.group(1))}"
            if label not in labels:
                labels.append(label)
    return labels


def _sentences(answer: str) -> list[str]:
    parts = [p for p in _SENTENCE_SPLIT.split(answer.strip()) if p.strip()]
    merged: list[str] = []
    for p in parts:
        # "Fact. [S1]" style: a fragment that is only labels belongs to the sentence before it.
        if merged and _LABEL_GROUP.sub("", p).strip(" .") == "":
            merged[-1] += " " + p
        else:
            merged.append(p)
    return merged


def _is_uncited_claim(sentence: str) -> bool:
    if _LABEL_GROUP.search(sentence):
        return False
    stripped = sentence.strip().rstrip(":")
    if not stripped or stripped.endswith(":"):
        return False
    return bool(re.search(r"\d", stripped)) or len(stripped.split()) >= UNCITED_WORD_LIMIT


def validate(answer: str, k: int) -> Validation:
    text = answer.strip()
    if not text or INSUFFICIENT_TOKEN in text:
        return Validation("insufficient")
    labels = extract_labels(text)
    if not labels:
        return Validation("invalid", reason="no_citations")
    allowed = {f"S{i}" for i in range(1, k + 1)}
    if any(label not in allowed for label in labels):
        return Validation("invalid", labels, reason="unknown_label")
    if any(_is_uncited_claim(s) for s in _sentences(text)):
        return Validation("invalid", labels, reason="uncited_sentence")
    return Validation("ok", labels)


@dataclass(frozen=True)
class Citation:
    marker: int
    chunk: VisibleChunk


def render(
    answer: str, labels: list[str], label_map: dict[str, VisibleChunk], permitted: PermittedSet
) -> tuple[str, list[Citation]]:
    """Map labels to permitted chunks (E3) and rewrite [S2] markers as [1], [2], ..."""
    citations: list[Citation] = []
    marker_for: dict[str, int] = {}
    for label in labels:
        chunk = permitted.require(label_map[label])
        marker_for[label] = len(citations) + 1
        citations.append(Citation(marker_for[label], chunk))

    def replace(group: re.Match[str]) -> str:
        nums = [marker_for[f"S{int(n.group(1))}"] for n in _LABEL.finditer(group.group(1))]
        return "".join(f"[{n}]" for n in dict.fromkeys(nums))

    return _LABEL_GROUP.sub(replace, answer.strip()), citations
