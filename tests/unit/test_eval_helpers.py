"""Eval runner helpers: gold-chunk location, ranking metrics, the independent ACL oracle."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eval.run_eval import Oracle, gold_chunk_ids, percentile, ranking_metrics


def test_gold_chunk_located_by_fact_text_including_ocr_noise() -> None:
    chunks = {
        1: {
            "filename": "a.pdf",
            "page": 2,
            "text": "Intro.\nThe escrow  includes $5.00\nfor radon.",
        },
        2: {"filename": "a.pdf", "page": 2, "text": "Unrelated text on the same page."},
        3: {"filename": "a.pdf", "page": 3, "text": "The escrow includes $5.00 for radon."},
        4: {"filename": "b.pdf", "page": 1, "text": "The rate lock expires on May 2, 2O21 today."},
    }
    by_page: dict[tuple[str, int], list[int]] = {}
    for cid, c in chunks.items():
        by_page.setdefault((c["filename"], c["page"]), []).append(cid)
    gold = [{"doc": "a.pdf", "page": 2, "fact": "The escrow includes $5.00 for radon."}]
    assert gold_chunk_ids(gold, by_page, chunks) == {1}  # page-bounded, whitespace-normalized
    ocr = [{"doc": "b.pdf", "page": 1, "fact": "The rate lock expires on May 2, 2021 today."}]
    assert gold_chunk_ids(ocr, by_page, chunks) == {4}  # OCR fallback (one bad token)


def test_ranking_metrics() -> None:
    assert ranking_metrics([9, 8, 7, 1], {1}) == (1.0, 1.0, 0.25)
    assert ranking_metrics([9, 8, 7, 6, 5, 1], {1}) == (0.0, 1.0, pytest.approx(1 / 6))
    assert ranking_metrics(list(range(20, 40)), {1}) == (0.0, 0.0, 0.0)
    assert percentile([1, 2, 3, 4], 0.5) == 2.5


def test_oracle(tmp_path: Path) -> None:
    acl = tmp_path / "acl.yaml"
    acl.write_text(
        yaml.safe_dump(
            {
                "documents": {
                    "a.pdf": {"groups": ["servicing"], "users": []},
                    "b.pdf": {"groups": [], "users": ["u_x"]},
                    "c.pdf": {"groups": [], "users": []},
                }
            }
        )
    )
    users = tmp_path / "users.yaml"
    users.write_text(
        yaml.safe_dump(
            {
                "users": [
                    {"user_id": "u_s", "groups": ["servicing"]},
                    {"user_id": "u_x", "groups": []},
                ]
            }
        )
    )
    o = Oracle(acl, users)
    assert o.can_read("u_s", "a.pdf") and not o.can_read("u_s", "b.pdf")
    assert o.can_read("u_x", "b.pdf") and not o.can_read("u_x", "a.pdf")
    assert not o.can_read("u_s", "c.pdf") and not o.can_read("u_x", "c.pdf")
    assert not o.can_read("u_s", "missing.pdf")
