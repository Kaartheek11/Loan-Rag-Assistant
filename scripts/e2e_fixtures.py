"""Build end-to-end test fixtures (extension.md §12.2). Synthetic data only.

Writes into OUT_DIR and prints a JSON description:
  - four 80-page loan PDFs with fresh loan numbers and content (unique per run, so reruns
    never collide as duplicates), each with one question about a planted fact (UI-T14..T21)
  - a text file renamed to .pdf, and a password-protected PDF (UI-T16)
  - a copy of a corpus file, which the backend must report as a duplicate (UI-T16)
  - a small PDF whose text contains HTML/script markup (UI-T13)
  - candidate deep questions for Alice (underwriting-only documents, page >= 50; UI-T2/T22)

Usage: uv run python scripts/e2e_fixtures.py OUT_DIR
"""

from __future__ import annotations

import io
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.synth.generate_corpus import generate  # noqa: E402
from eval.synth.generate_questions import TEMPLATES, question_for  # noqa: E402
from tests.helpers import make_pdf  # noqa: E402

ASKABLE = (
    "escrow_item",
    "reserves",
    "prepayment_penalty",
    "earnest_money",
    "insurance_premium",
    "rate_lock",
    "hoa_dues",
    "late_fee",
)
# Payment-history filler is full of late-fee rows, so a late-fee question about a fresh upload
# is ambiguous; upload fixtures ask about the other kinds (deep candidates are validated live).
UPLOAD_ASKABLE = tuple(k for k in ASKABLE if k != "late_fee")


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(time.time_ns())
    start = rng.randint(100_000, 899_999)  # loan numbers/filenames outside the corpus range
    eighty_dir, synth = out / "eighty", out / "synth"
    shutil.rmtree(eighty_dir, ignore_errors=True)
    generate(
        seed=rng.randint(1, 10**9),
        count=6,
        out_dir=eighty_dir,
        config_dir=out / "cfg",
        synth_dir=synth,
        min_pages=80,
        max_pages=80,
        scanned_doc_frac=0.0,
        start_index=start,
        write_config=False,
    )
    facts = [json.loads(line) for line in (synth / "facts.jsonl").open()]
    eighty = []
    for pdf in sorted(eighty_dir.glob("*.pdf")):
        # The deepest askable fact, so "a citation deep in the file" really is deep.
        fact = max(
            (
                f
                for f in facts
                if f["doc_filename"] == pdf.name and f["fact_type"] in UPLOAD_ASKABLE
            ),
            key=lambda f: f["page"],
        )
        eighty.append(
            {
                "path": str(pdf),
                "filename": pdf.name,
                "loanNumber": fact["loan_number"],
                "question": question_for(fact, rng),
                "page": fact["page"],
            }
        )

    text_as_pdf = out / "meeting-notes.pdf"
    text_as_pdf.write_text("These are plain-text meeting notes, not a PDF.\n" * 20)

    writer = PdfWriter()
    for page in PdfReader(eighty[0]["path"]).pages[:2]:
        writer.add_page(page)
    writer.encrypt("open-sesame")
    buf = io.BytesIO()
    writer.write(buf)
    encrypted = out / "locked-statement.pdf"
    encrypted.write_bytes(buf.getvalue())

    corpus = Path(os.environ.get("CORPUS_DIR", ROOT / "corpus"))
    dup_src = sorted(corpus.glob("loan_*.pdf"))[0]
    duplicate = out / "copy-of-existing.pdf"
    shutil.copyfile(dup_src, duplicate)

    xss_loan = f"LN-2024-{start + 7:06d}"
    xss = make_pdf(
        out / f"markup-test-{start}.pdf",
        [
            f"MARKUP TEST - LOAN {xss_loan}\nBorrower name: Mark Upton.",
            "REVIEW NOTES\nThe reviewer pasted this note into the file: "
            "<img src=x onerror=alert(1)> "
            "and also <script>alert('x')</script> while checking the escrow balance of $1,234.56.",
        ],
    )

    acls = yaml.safe_load((ROOT / "config" / "acl_seed.yaml").read_text())["documents"]
    manifest = {
        r["filename"]: r for r in map(json.loads, (ROOT / "eval/synth/manifest.jsonl").open())
    }
    corpus_facts = [json.loads(line) for line in (ROOT / "eval/synth/facts.jsonl").open()]
    deep = []
    for f in corpus_facts:
        acl = acls.get(f["doc_filename"], {})
        if (
            acl.get("groups") == ["underwriting"]
            and not acl.get("users")
            and f["page"] >= 50
            and f["fact_type"] in ASKABLE
            and manifest[f["doc_filename"]]["page_count"] >= 75
            and f["fact_type"] in TEMPLATES
        ):
            deep.append(
                {
                    "question": question_for(f, random.Random(f["page"])),
                    "filename": f["doc_filename"],
                    "page": f["page"],
                    "pageCount": manifest[f["doc_filename"]]["page_count"],
                    "loanNumber": f["loan_number"],
                }
            )
    print(
        json.dumps(
            {
                "eighty": eighty,
                "textAsPdf": str(text_as_pdf),
                "encrypted": str(encrypted),
                "duplicate": {"path": str(duplicate), "existing": dup_src.name},
                "xss": {
                    "path": str(xss),
                    "loanNumber": xss_loan,
                    "question": f"What escrow balance was in the notes for loan {xss_loan}?",
                },
                "deepCandidates": deep[:12],
            }
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
