"""Text cleaning (§5.4 step 5): NFKC, whitespace, header/footer removal, page-number lines."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

REPEAT_FRACTION = 0.60
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.IGNORECASE)
_SPACES = re.compile(r"[ \t ]+")


def _normalize_line(line: str) -> str:
    return _SPACES.sub(" ", line).strip()


_PAGE_REF = re.compile(r"\bpage\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\b", re.IGNORECASE)


def _repeat_key(line: str) -> str:
    # Page references are masked so "Confidential | Page 2 of 7" and "... Page 3 of 7"
    # count as the same repeated footer. Other digits are significant.
    return _PAGE_REF.sub("page #", line).casefold()


def clean_pages(pages: list[str]) -> list[str]:
    normalized = [
        [
            ln
            for ln in (_normalize_line(x) for x in unicodedata.normalize("NFKC", p).splitlines())
            if ln
        ]
        for p in pages
    ]
    n = len(normalized)
    repeated: set[str] = set()
    if n >= 2:
        counts: Counter[str] = Counter()
        for lines in normalized:
            counts.update({_repeat_key(ln) for ln in lines})
        repeated = {k for k, c in counts.items() if c / n > REPEAT_FRACTION}
    cleaned = []
    for lines in normalized:
        kept = [
            ln for ln in lines if _repeat_key(ln) not in repeated and not _PAGE_NUMBER.match(ln)
        ]
        cleaned.append("\n".join(kept))
    return cleaned
