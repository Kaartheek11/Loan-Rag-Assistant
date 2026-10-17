"""Hybrid retrieval with the adaptive over-fetch loop (§5.3 step 6-7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from sqlalchemy import Connection

from app.retrieval.fusion import rrf
from app.retrieval.index_manager import IndexHolder
from app.retrieval.keyword_search import keyword_search
from app.security import permissions
from app.security.identity import Identity
from app.security.permissions import PermittedSet

Mode = Literal["hybrid", "vector", "keyword"]


@dataclass
class RetrievalResult:
    permitted: PermittedSet
    rounds: int
    final_n: int
    visible_vector: list[int] = field(default_factory=list)
    visible_keyword: list[int] = field(default_factory=list)


class Retriever:
    def __init__(self, index: IndexHolder, *, n_start: int, n_max: int) -> None:
        self.index = index
        self.n_start = n_start
        self.n_max = n_max

    def retrieve(
        self,
        conn: Connection,
        identity: Identity,
        question: str,
        query_vector: np.ndarray,
        top_k: int,
        request_id: str,
        mode: Mode = "hybrid",
    ) -> RetrievalResult:
        n = self.n_start
        rounds = 0
        while True:
            rounds += 1
            vec = self.index.search(query_vector, n) if mode != "keyword" else []
            kw = keyword_search(conn, question, n) if mode != "vector" else []
            # E1: only the visible subset survives; order within each list is preserved,
            # so ranks are recomputed on visible-only lists.
            visible = permissions.visible_ids(conn, identity, set(vec) | set(kw))
            vis_vec = [c for c in vec if c in visible]
            vis_kw = [c for c in kw if c in visible]
            enough = len(set(vis_vec) | set(vis_kw)) >= top_k
            # Extension (D11): stop early when neither retriever can return more candidates.
            exhausted = len(vec) < n and len(kw) < n
            if enough or n >= self.n_max or exhausted:
                break
            n = min(2 * n, self.n_max)
        lists = (
            [lst for lst in (vis_vec, vis_kw) if lst]
            if mode == "hybrid"
            else ([vis_vec] if mode == "vector" else [vis_kw])
        )
        fused = [chunk_id for chunk_id, _ in rrf(lists)][:top_k]
        # E1 again, loading text: the permitted set for this request.
        permitted = permissions.load_permitted(conn, identity, fused, request_id)
        return RetrievalResult(
            permitted=permitted,
            rounds=rounds,
            final_n=n,
            visible_vector=vis_vec,
            visible_keyword=vis_kw,
        )
