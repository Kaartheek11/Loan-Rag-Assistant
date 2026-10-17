"""Identifier extraction shared by ingestion and query time (§8.3).

Both sides use the same regexes and normalization, so an exact array match works.
"""

from __future__ import annotations

import re

LOAN_NUMBER = re.compile(r"\bLN-\d{4}-\d{6}\b", re.IGNORECASE)
ZIP_CODE = re.compile(r"(?<![\d$,.\-])\b\d{5}(?:-\d{4})?\b(?![,.]\d|-\d)")
AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?!\d)")


def _norm_amount(whole: str, cents: str | None) -> str:
    return f"${int(whole.replace(',', ''))}.{(cents or '0').ljust(2, '0')}"


def extract_identifiers(text: str) -> list[str]:
    found: list[str] = []
    for m in LOAN_NUMBER.finditer(text):
        found.append(m.group(0).upper())
    stripped = LOAN_NUMBER.sub(" ", text)
    for m in AMOUNT.finditer(stripped):
        found.append(_norm_amount(m.group(1), m.group(2)))
    for m in ZIP_CODE.finditer(AMOUNT.sub(" ", stripped)):
        found.append(m.group(0))
    return sorted(set(found))


def mask_loan_numbers(text: str) -> str:
    return LOAN_NUMBER.sub("[LOAN_NUMBER]", text)
