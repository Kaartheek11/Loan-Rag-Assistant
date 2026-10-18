"""Synthetic corpus v2 (extension.md §17.4): structure, planted facts, scanned pages."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber
import pytest
import yaml

from eval.synth.generate_corpus import (
    GROUPS,
    INJECTION_LINE,
    SECTIONS,
    USERS,
    allocate_pages,
    generate,
)

N_DOCS = 6


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _page_text(path: Path, page: int) -> str:
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[page - 1].extract_text() or ""
    if len(text.strip()) >= 20:
        return text
    import pytesseract
    from pdf2image import convert_from_path

    image = convert_from_path(path, dpi=300, first_page=page, last_page=page)[0]
    return pytesseract.image_to_string(image)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("synth")
    dirs = {"out": root / "corpus", "config": root / "config", "synth": root / "synth"}
    summary = generate(
        seed=11,
        count=N_DOCS,
        out_dir=dirs["out"],
        config_dir=dirs["config"],
        synth_dir=dirs["synth"],
        min_pages=50,
        max_pages=60,
        scanned_doc_frac=0.34,
    )
    assert summary["docs_with_scans"] == 2
    assert summary["injection"] >= 1 and summary["empty_acl"] >= 1
    return dirs


def _manifest(corpus: dict[str, Path]) -> list[dict[str, object]]:
    return [json.loads(line) for line in (corpus["synth"] / "manifest.jsonl").open()]


def _facts(corpus: dict[str, Path]) -> list[dict[str, object]]:
    return [json.loads(line) for line in (corpus["synth"] / "facts.jsonl").open()]


def test_allocate_pages_follows_shares() -> None:
    for total in (17, 50, 75, 100):
        pages = allocate_pages(total)
        assert sum(pages) == total and min(pages) >= 1 and len(pages) == len(SECTIONS)
    pages = allocate_pages(100)
    assert pages == [share for _, share, _ in SECTIONS]


def test_page_counts_sections_and_headers(corpus: dict[str, Path]) -> None:
    for row in _manifest(corpus):
        n = int(str(row["page_count"]))
        assert 50 <= n <= 60
        section_pages = row["section_pages"]
        assert isinstance(section_pages, dict)
        assert list(section_pages) == [key for _, _, key in SECTIONS]  # fixed order
        flat = [p for pages in section_pages.values() for p in pages]
        assert flat == list(range(1, n + 1)) and all(section_pages.values())
        path = corpus["out"] / str(row["filename"])
        with pdfplumber.open(path) as pdf:
            assert len(pdf.pages) == n
            first = pdf.pages[0].extract_text() or ""
        assert first.splitlines()[0] == f"Loan {row['loan_number']} | Page 1 of {n}"


def test_planted_facts_rules(corpus: dict[str, Path]) -> None:
    manifest = {str(r["filename"]): r for r in _manifest(corpus)}
    by_doc: dict[str, list[dict[str, object]]] = {}
    for f in _facts(corpus):
        assert {"doc_filename", "page", "section", "fact_text", "fact_type"} <= set(f)
        by_doc.setdefault(str(f["doc_filename"]), []).append(f)
    assert set(by_doc) == set(manifest)
    for name, facts in by_doc.items():
        n = int(str(manifest[name]["page_count"]))
        assert 5 <= len(facts) <= 8
        assert sum(int(str(f["page"])) > (2 * n) // 3 for f in facts) >= 2  # last third
        assert len({f["fact_type"] for f in facts}) == len(facts)
        assert len({f["section"] for f in facts}) >= 3  # spread across sections


def test_every_fact_found_on_its_page(corpus: dict[str, Path]) -> None:
    missing = []
    for f in _facts(corpus):
        text = _page_text(corpus["out"] / str(f["doc_filename"]), int(str(f["page"])))
        if _norm(str(f["fact_text"])) not in _norm(text):
            missing.append((f["doc_filename"], f["page"], f["fact_text"]))
    assert not missing, missing[:5]


def test_scanned_pages_have_no_text_layer(corpus: dict[str, Path]) -> None:
    rows = _manifest(corpus)
    scanned = [r for r in rows if r["scanned_pages"]]
    assert len(scanned) == 2
    for row in rows:
        pages = row["scanned_pages"]
        assert isinstance(pages, list)
        with pdfplumber.open(corpus["out"] / str(row["filename"])) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                empty = len((page.extract_text() or "").strip()) < 20
                assert empty == (i in pages), (row["filename"], i)
        if pages:
            assert 3 <= len(pages) <= 8


def test_injection_line_and_seeds(corpus: dict[str, Path]) -> None:
    for row in _manifest(corpus):
        if row["injection"]:
            uw = row["section_pages"]["underwriting"][0]  # type: ignore[index]
            text = _norm(_page_text(corpus["out"] / str(row["filename"]), uw))
            assert _norm(INJECTION_LINE[:60]) in text
    acl = yaml.safe_load((corpus["config"] / "acl_seed.yaml").read_text())["documents"]
    assert len(acl) == N_DOCS
    assert {g for e in acl.values() for g in e["groups"]} <= set(GROUPS)
    users = yaml.safe_load((corpus["config"] / "users_seed.yaml").read_text())["users"]
    assert [u["user_id"] for u in users] == [u["user_id"] for u in USERS]
