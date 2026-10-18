"""Document context for chunks (extension.md §17.2).

Most pages of a long loan file never mention the loan number or the borrower; those appear
on the first pages and in a page header that cleaning removes. Every chunk therefore gets a
context prefix ("Loan LN-… | Borrower … | Section: …") that is used for embedding, full-text
search and identifier search, and never shown as document content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.retrieval.identifiers import LOAN_NUMBER

FIELD_PAGES = 5
SECTION_TOKEN_ALLOWANCE = 20  # budget reserved for the per-chunk "Section: …" part

# Name words, or a middle initial like "J." (a period after a full word ends the name).
_NAME_WORD = r"(?:[A-Z][A-Za-z'\-]+|[A-Z]\.)"
BORROWER = re.compile(
    rf"\bBorrower(?:'s)?(?:\s+name)?\s*:\s*({_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{1,3}})"
)


@dataclass(frozen=True)
class DocumentFields:
    loan_number: str | None
    borrower_name: str | None

    @property
    def missing(self) -> list[str]:
        return [
            name
            for name, value in (
                ("loan_number", self.loan_number),
                ("borrower_name", self.borrower_name),
            )
            if not value
        ]


def extract_document_fields(pages: list[str]) -> DocumentFields:
    """Loan number and borrower name from the first 5 pages (None when not found)."""
    head = "\n".join(pages[:FIELD_PAGES])
    loan = LOAN_NUMBER.search(head)
    borrower = BORROWER.search(head)
    name = borrower.group(1).strip() if borrower else None
    return DocumentFields(loan.group(0).upper() if loan else None, name or None)


def build_prefix(fields: DocumentFields, section: str | None) -> str:
    """ "Loan X | Borrower Y | Section: Z", with only the parts that are known."""
    parts: list[str] = []
    if fields.loan_number:
        parts.append(f"Loan {fields.loan_number}")
    if fields.borrower_name:
        parts.append(f"Borrower {fields.borrower_name}")
    if section:
        parts.append(f"Section: {section[:80]}")
    return " | ".join(parts)


def embedding_text(prefix: str, text: str) -> str:
    """What gets embedded: the prefix and the chunk text (§17.2). Never displayed."""
    return f"{prefix}\n{text}" if prefix else text
