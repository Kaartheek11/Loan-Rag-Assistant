"""Token-aware, page-bounded chunking (§8.1).

- ~400 tokens per chunk with ~60 tokens of overlap, counted with the embedding tokenizer.
- Chunks never cross page boundaries.
- Splits on sentence boundaries; an over-long sentence is split between "atoms", and loan
  numbers, currency amounts, and dates are always single atoms, so they are never split.
- A trailing chunk under 20 tokens is merged into the previous chunk on the same page.
- `section` is the nearest preceding heading (may come from an earlier page).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

CHUNK_TOKENS = 400
OVERLAP_TOKENS = 60
MIN_CHUNK_TOKENS = 20
MAX_MODEL_TOKENS = 510  # bge limit 512 minus [CLS]/[SEP]

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
PROTECTED = re.compile(
    r"LN-\d{4}-\d{6}"  # loan numbers
    r"|\$\s?\d[\d,]*(?:\.\d+)?"  # currency amounts
    rf"|(?:{_MONTHS})\s+\d{{1,2}},\s+\d{{4}}"  # March 3, 2024
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}",  # ISO and US dates
    re.IGNORECASE,
)
_ATOM = re.compile(rf"(?:{PROTECTED.pattern})\S*|\S+", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")

TokenCounter = Callable[[str], int]


@dataclass
class Chunk:
    page: int
    text: str
    token_count: int
    section: str | None


@dataclass
class _Unit:
    text: str
    tokens: int
    section: str | None


@dataclass
class _Draft:
    units: list[_Unit] = field(default_factory=list)
    n_overlap: int = 0

    @property
    def tokens(self) -> int:
        return sum(u.tokens for u in self.units)


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90 or len(s.split()) > 12:
        return False
    if s.endswith(":"):
        return True
    letters = [ch for ch in s if ch.isalpha()]
    if len(letters) < 3:
        return False
    # Mostly letters: keeps table rows such as "2023-04-01 $1,200.00 PAID" from counting.
    if len(letters) / len(s.replace(" ", "")) < 0.5:
        return False
    if all(ch.isupper() for ch in letters):
        return True
    if any(ch.isdigit() for ch in s):  # title-case lines with figures are table rows
        return False
    words = [w for w in re.split(r"\s+", s) if w[:1].isalpha()]
    return bool(words) and all(w[0].isupper() for w in words) and not s.endswith(".")


def section_label(heading: str) -> str:
    """Readable section name: an all-caps heading becomes sentence case ("Bank statements")."""
    h = heading.strip().rstrip(":").strip()
    letters = [ch for ch in h if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        words = h.split()
        out = [w if any(c.isdigit() for c in w) else w.lower() for w in words]
        out[0] = out[0][:1].upper() + out[0][1:]
        return " ".join(out)
    return h


def split_sentences(block: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(block) if s.strip()]


def split_atoms(text: str, count: TokenCounter, limit: int) -> list[str]:
    """Split one over-long sentence into pieces of at most `limit` tokens, between atoms."""
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for atom in _ATOM.findall(text):
        t = count(atom)
        if current and current_tokens + t > limit:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(atom)
        current_tokens += t
    if current:
        pieces.append(" ".join(current))
    return pieces


def _page_units(
    text: str, count: TokenCounter, heading: str | None, unit_limit: int
) -> tuple[list[_Unit], str | None]:
    units: list[_Unit] = []
    block: list[str] = []

    def flush() -> None:
        if not block:
            return
        for sentence in split_sentences(" ".join(block)):
            t = count(sentence)
            if t <= unit_limit:
                units.append(_Unit(sentence, t, heading))
            else:
                for piece in split_atoms(sentence, count, unit_limit):
                    units.append(_Unit(piece, count(piece), heading))
        block.clear()

    for line in text.splitlines():
        if is_heading(line):
            flush()
            heading = section_label(line) or heading
            units.append(_Unit(line.strip(), count(line), heading))
        else:
            block.append(line.strip())
    flush()
    return units, heading


def chunk_pages(
    pages: list[str],
    count: TokenCounter,
    *,
    size: int = CHUNK_TOKENS,
    overlap: int = OVERLAP_TOKENS,
    min_tokens: int = MIN_CHUNK_TOKENS,
) -> list[Chunk]:
    out: list[Chunk] = []
    heading: str | None = None
    unit_limit = size - overlap
    for page_no, page_text in enumerate(pages, start=1):
        units, heading = _page_units(page_text, count, heading, unit_limit)
        if not units:
            continue
        drafts: list[_Draft] = []
        cur = _Draft()
        for unit in units:
            if cur.units and cur.tokens + unit.tokens > size:
                drafts.append(cur)
                tail: list[_Unit] = []
                for u in reversed(cur.units):
                    if sum(x.tokens for x in tail) + u.tokens > overlap:
                        break
                    tail.insert(0, u)
                while tail and sum(x.tokens for x in tail) + unit.tokens > size:
                    tail.pop(0)
                cur = _Draft(units=list(tail), n_overlap=len(tail))
            cur.units.append(unit)
        drafts.append(cur)
        # Merge a tiny trailing chunk into the previous one on the same page.
        if len(drafts) >= 2:
            last = drafts[-1]
            new_units = last.units[last.n_overlap :]
            if sum(u.tokens for u in new_units) < min_tokens and (
                drafts[-2].tokens + sum(u.tokens for u in new_units) <= MAX_MODEL_TOKENS
            ):
                drafts[-2].units.extend(new_units)
                drafts.pop()
        for d in drafts:
            text = "\n".join(u.text for u in d.units)
            out.append(
                Chunk(page=page_no, text=text, token_count=count(text), section=d.units[0].section)
            )
    return out
