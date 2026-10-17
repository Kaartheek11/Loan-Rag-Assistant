"""Reciprocal rank fusion (§8.4). Ranks are 1-based and computed on visible-only lists."""

from __future__ import annotations

from collections.abc import Sequence

RRF_K = 60


def rrf(lists: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranked in lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    # Higher score first; ties broken by lower chunk_id for determinism.
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
