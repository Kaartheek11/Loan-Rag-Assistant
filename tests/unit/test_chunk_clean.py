"""Phase 3 unit tests: chunking (§8.1), cleaning (§5.4), identifiers (§8.3)."""

from __future__ import annotations

import re

import pytest

from app.ingestion.chunk import CHUNK_TOKENS, MAX_MODEL_TOKENS, chunk_pages, is_heading
from app.ingestion.clean import clean_pages
from app.retrieval.identifiers import extract_identifiers

LOAN = re.compile(r"LN-\d{4}-\d{6}")


@pytest.fixture(scope="module")
def count(embedder):  # type: ignore[no-untyped-def]
    return embedder.count_tokens


def _long_page(marker: str, sentences: int) -> str:
    body = " ".join(
        f"Payment {i} for loan LN-2024-{i:06d} of $1,{i:03d}.25 was received on "
        f"March {i % 28 + 1}, "
        f"2024 and posted to escrow per marker {marker}."
        for i in range(sentences)
    )
    return f"PAYMENT HISTORY\n{body}"


def test_chunks_never_cross_pages(count) -> None:  # type: ignore[no-untyped-def]
    pages = [_long_page("alpha", 60), _long_page("bravo", 5), _long_page("charlie", 40)]
    chunks = chunk_pages(pages, count)
    assert {c.page for c in chunks} == {1, 2, 3}
    markers = {1: "alpha", 2: "bravo", 3: "charlie"}
    for c in chunks:
        others = [m for p, m in markers.items() if p != c.page]
        assert markers[c.page] in c.text
        assert not any(m in c.text for m in others)


def test_token_limit_and_short_page_single_chunk(count) -> None:  # type: ignore[no-untyped-def]
    chunks = chunk_pages([_long_page("x", 80), "SUMMARY\nA short page about one loan."], count)
    for c in chunks:
        assert c.token_count <= CHUNK_TOKENS + 20 <= MAX_MODEL_TOKENS
        assert c.token_count == count(c.text)
    assert len([c for c in chunks if c.page == 2]) == 1
    assert len([c for c in chunks if c.page == 1]) > 1


def test_overlap_between_consecutive_chunks(count) -> None:  # type: ignore[no-untyped-def]
    chunks = [c for c in chunk_pages([_long_page("x", 80)], count)]
    assert len(chunks) >= 3
    for a, b in zip(chunks, chunks[1:], strict=False):
        first_line_of_b = b.text.splitlines()[0]
        assert first_line_of_b in a.text  # b starts with the tail of a


def test_never_splits_loan_numbers_amounts_dates(count) -> None:  # type: ignore[no-untyped-def]
    # One giant "sentence" with no punctuation forces splitting between atoms.
    atoms = " ".join(
        f"LN-2023-{i:06d} $12,{i:03d}.50 June {i % 28 + 1}, 2023 2023-06-{i % 28 + 1:02d}"
        for i in range(300)
    )
    page = f"LEDGER\n{atoms}"
    chunks = chunk_pages([page], count)
    assert len(chunks) > 3
    expected_loans = set(LOAN.findall(page))
    seen: set[str] = set()
    for c in chunks:
        for frag in re.findall(r"LN-[\d-]*", c.text):
            assert LOAN.fullmatch(frag), f"split loan number fragment {frag!r}"
        for frag in re.findall(r"\$[\d,.]+", c.text):
            assert re.fullmatch(r"\$\d{1,3}(,\d{3})*\.\d{2}", frag), frag
        lines_and_words = c.text.split()
        assert lines_and_words[-1] != "June" and not lines_and_words[-1].endswith("June")
        assert not re.match(r"^\d{1,2}, \d{4}", c.text.splitlines()[0])
        seen |= set(LOAN.findall(c.text))
    assert seen == expected_loans


def test_tiny_trailing_chunk_is_merged(count) -> None:  # type: ignore[no-untyped-def]
    # Fill ~one chunk, then add a tiny trailing sentence.
    sentences = []
    while count(" ".join(sentences)) < CHUNK_TOKENS - 5:
        sentences.append("The borrower provided an updated bank statement for review.")
    page = " ".join(sentences) + " Done now."
    chunks = chunk_pages([page], count)
    assert all(c.token_count >= 20 for c in chunks)
    assert chunks[-1].text.endswith("Done now.")


def test_section_heading_detection(count) -> None:  # type: ignore[no-untyped-def]
    pages = [
        "ESCROW ANALYSIS - LOAN LN-2024-000001\nTaxes are escrowed.",
        "More text without a heading on page two.",
        "Conditions of Approval:\nThe title must be updated.",
    ]
    chunks = chunk_pages(pages, count)
    assert chunks[0].section == "Escrow analysis - loan LN-2024-000001"
    assert chunks[1].section == "Escrow analysis - loan LN-2024-000001"  # carried over
    assert chunks[2].section == "Conditions of Approval"
    assert is_heading("Underwriting Notes")
    assert not is_heading("The borrower provided an updated bank statement for review.")


def test_clean_removes_headers_footers_and_page_numbers() -> None:
    pages = [
        f"ACME Corp | Confidential\nSECTION {i}\nBody text {i} with ﬁ ligature.\nPage {i} of 4"
        for i in range(1, 5)
    ]
    cleaned = clean_pages(pages)
    for i, text in enumerate(cleaned, 1):
        assert "ACME" not in text
        assert "Page" not in text
        assert f"SECTION {i}" in text
        assert "fi ligature" in text  # NFKC


def test_identifier_extraction() -> None:
    text = (
        "Loan ln-2024-004512 has $1,234.56 due and $632,500 principal; ZIP 94110 "
        "and 10001-1234. Not a zip: 2024 or 004512 or $12345."
    )
    ids = extract_identifiers(text)
    assert "LN-2024-004512" in ids
    assert "$1234.56" in ids and "$632500.00" in ids and "$12345.00" in ids
    assert "94110" in ids and "10001-1234" in ids
    assert "12345" not in ids and "004512" not in ids
    assert extract_identifiers("what about LN-2024-004512?") == ["LN-2024-004512"]
