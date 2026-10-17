"""Keyword search (§8.3): exact identifier match first, then `simple` full-text search.

Returns ranked candidate chunk IDs only. Permission filtering happens afterwards, in
app/security/permissions.py (E1); this module never touches ACL columns.
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, text

from app.retrieval.identifiers import extract_identifiers

# websearch_to_tsquery ANDs bare words, so a natural-language question would only match
# chunks containing every word. We pass content words joined with the websearch `or`
# operator instead (decision D12 in CLAUDE.md / docs/architecture.md).
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "show",
        "so",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "any",
        "about",
        "give",
        "find",
        "list",
    }
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9$%.,\-]*[A-Za-z0-9%]|[A-Za-z0-9]")
MAX_TERMS = 24

_IDENT_SQL = text(
    """
    SELECT c.chunk_id
    FROM chunks c
    LEFT JOIN LATERAL (SELECT websearch_to_tsquery('simple', :q) AS query) q ON true
    WHERE c.identifiers && CAST(:idents AS text[])
      AND c.deleted_at IS NULL
    ORDER BY cardinality(ARRAY(
                 SELECT unnest(c.identifiers) INTERSECT SELECT unnest(CAST(:idents AS text[]))
             )) DESC,
             ts_rank_cd(c.tsv, q.query) DESC,
             c.chunk_id
    LIMIT :n
    """
)

_FTS_SQL = text(
    """
    SELECT c.chunk_id
    FROM chunks c, websearch_to_tsquery('simple', :q) AS query
    WHERE c.tsv @@ query
      AND c.deleted_at IS NULL
    ORDER BY ts_rank_cd(c.tsv, query) DESC, c.chunk_id
    LIMIT :n
    """
)


def fulltext_query(question: str) -> str:
    terms: list[str] = []
    for raw in _TOKEN.findall(question):
        term = raw.lower().strip(".,")
        if not term or term in STOPWORDS or term in terms:
            continue
        terms.append(term)
    return " or ".join(terms[:MAX_TERMS])


def keyword_search(conn: Connection, question: str, n: int) -> list[int]:
    q = fulltext_query(question)
    ranked: list[int] = []
    idents = extract_identifiers(question)
    if idents:
        ranked.extend(
            int(r[0]) for r in conn.execute(_IDENT_SQL, {"idents": idents, "q": q, "n": n})
        )
    if q and len(ranked) < n:
        seen = set(ranked)
        for r in conn.execute(_FTS_SQL, {"q": q, "n": n}):
            cid = int(r[0])
            if cid not in seen:
                ranked.append(cid)
                seen.add(cid)
    return ranked[:n]
