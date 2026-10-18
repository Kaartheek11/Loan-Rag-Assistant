"""Build the labeled evaluation set from facts.jsonl (task.md §11.2, extension.md §17.5).

Gold answers are labeled by document + page + planted fact text, never by chunk ID.
Answerable questions are split by the depth of the gold page: about a third on pages 1-10,
a third on pages 11-50, and a third on page 51 or later. Questions whose answer page names
neither the loan number nor the borrower are marked `context_dependent` (at least 30).

Output rows (eval/questions.jsonl):
  {"id", "kind": answerable|unanswerable|probe, "user", "question", "answerable", "answer",
   "fact_type", "depth", "context_dependent", "gold": [{"doc", "page", "section", "fact"}]}

Usage:
  uv run python -m eval.synth.generate_questions --seed 7 \
      --answerable 162 --unanswerable 25 --probes 35
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import yaml

TEMPLATES: dict[str, list[str]] = {
    "credit_inquiry": ["Which creditor made the credit inquiry dated {hint} on loan {ln}?"],
    "paystub_ytd": [
        "What year-to-date gross earnings does the pay stub for the period ending {hint} show "
        "for loan {ln}?"
    ],
    "schedule_c_profit": ["What net profit does Schedule C report from {hint} for loan {ln}?"],
    "bank_ending_balance": [
        "What was the ending balance on the {h0} statement for account ending {h1} on loan {ln}?"
    ],
    "large_deposit": [
        "How much was the deposit on {h0} that was sourced to the sale of a {h1} for loan {ln}?"
    ],
    "voe_hire_date": [
        "What original hire date did the employer confirm for the borrower on loan {ln}?"
    ],
    "earnest_money": ["How much earnest money did the buyer deposit for loan {ln}?"],
    "appraisal_adjustment": ["What adjustment did the appraiser apply for {hint} on loan {ln}?"],
    "hoa_dues": [
        "What are the monthly HOA dues for loan {ln}?",
        "How much are the homeowners association dues each month on loan {ln}?",
    ],
    "title_exception": [
        "In whose favor is the unrecorded {hint} listed in the title exceptions for loan {ln}?"
    ],
    "insurance_premium": ["What is the annual hazard insurance premium for loan {ln}?"],
    "reserves": [
        "How much in post-closing reserves did the underwriter document for loan {ln}?",
        "What reserve amount was listed as a compensating factor on loan {ln}?",
    ],
    "condition": ["Before funding loan {ln}, which organization must provide the document {hint}?"],
    "escrow_item": ["What is the annual escrow disbursement for {hint} on loan {ln}?"],
    "rate_lock": ["When does the interest rate lock expire for loan {ln}?"],
    "prepayment_penalty": ["What prepayment penalty percentage applies to loan {ln}?"],
    "loe_withdrawal": [
        "How much was the withdrawal explained in the borrower's letter dated {hint} for loan {ln}?"
    ],
    "late_fee": ["What late fee was charged for the payment due {hint} on loan {ln}?"],
}
DEPTHS = [("1-10", 1, 10), ("11-50", 11, 50), ("51+", 51, 10_000)]

UNANSWERABLE_TEMPLATES = [
    "What is the annual llama grazing fee in the escrow account for loan {ln}?",
    "Which helicopter pad inspection is required before funding loan {ln}?",
    "What is the monthly fee for the private ski lift on loan {ln}?",
    "How many vineyard acres secure loan {ln}?",
    "What cryptocurrency balance did the underwriter verify for loan {ln}?",
    "What is the monthly charge for the rooftop helipad lease on loan {ln}?",
    "Which airport authority approved the hangar lease for loan {ln}?",
]
MISSING_LOAN_TEMPLATES = [
    "What is the interest rate on loan {ln}?",
    "What are the monthly HOA dues for loan {ln}?",
    "When does the rate lock expire for loan {ln}?",
]


def depth_of(page: int) -> str:
    return next(name for name, lo, hi in DEPTHS if lo <= page <= hi)


def _visible(user: dict[str, object], acl: dict[str, list[str]]) -> bool:
    groups = set(user.get("groups") or [])  # type: ignore[call-overload]
    return bool(groups & set(acl.get("groups") or [])) or user["user_id"] in (
        acl.get("users") or []
    )


def question_for(fact: dict[str, object], rng: random.Random) -> str:
    template = rng.choice(TEMPLATES[str(fact["fact_type"])])
    hint = str(fact.get("question_hint", ""))
    h0, _, h1 = hint.partition("|")
    return template.format(ln=fact["loan_number"], hint=hint, h0=h0, h1=h1)


def build(
    *,
    facts_path: Path,
    manifest_path: Path,
    acl_path: Path,
    users_path: Path,
    seed: int,
    n_answerable: int,
    n_unanswerable: int,
    n_probes: int,
    min_context_dependent: int = 30,
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    facts = [
        f for f in (json.loads(line) for line in facts_path.open()) if f["fact_type"] in TEMPLATES
    ]
    manifest = {r["filename"]: r for r in (json.loads(line) for line in manifest_path.open())}
    acls = yaml.safe_load(acl_path.read_text())["documents"]
    users = list(yaml.safe_load(users_path.read_text())["users"])
    readers = {f: [u for u in users if _visible(u, a)] for f, a in acls.items()}
    outsiders = {f: [u for u in users if not _visible(u, a)] for f, a in acls.items()}
    rng.shuffle(facts)
    used_docs: set[str] = set()

    def gold(f: dict[str, object]) -> list[dict[str, object]]:
        return [
            {
                "doc": f["doc_filename"],
                "page": f["page"],
                "section": f["section"],
                "fact": f["fact_text"],
            }
        ]

    rows: list[dict[str, object]] = []
    per_depth, extra = divmod(n_answerable, len(DEPTHS))
    for k, (name, lo, hi) in enumerate(DEPTHS):
        want = per_depth + (1 if k < extra else 0)
        taken = 0
        for f in facts:
            if taken >= want:
                break
            doc = str(f["doc_filename"])
            if doc in used_docs or not readers.get(doc) or not lo <= int(f["page"]) <= hi:
                continue
            used_docs.add(doc)
            user = rng.choice(readers[doc])
            rows.append(
                {
                    "kind": "answerable",
                    "user": user["user_id"],
                    "question": question_for(f, rng),
                    "answerable": True,
                    "answer": f["answer"],
                    "fact_type": f["fact_type"],
                    "depth": name,
                    "context_dependent": bool(f["context_dependent"]),
                    "page_count": manifest[doc]["page_count"],
                    "gold": gold(f),
                }
            )
            taken += 1
        if taken < want:
            raise SystemExit(f"not enough facts at depth {name}: {taken}/{want}")
    n_ctx = sum(bool(r["context_dependent"]) for r in rows)
    if n_ctx < min_context_dependent:
        raise SystemExit(f"only {n_ctx} context-dependent questions (< {min_context_dependent})")

    docs = [d for d in manifest if readers.get(d)]
    for i in range(n_unanswerable):
        if i % 4 == 3:
            ln = f"LN-{rng.randint(2018, 2025)}-{rng.randint(900000, 999999):06d}"
            user = rng.choice([u for u in users if u.get("groups")])
            q = rng.choice(MISSING_LOAN_TEMPLATES).format(ln=ln)
        else:
            doc = rng.choice(docs)
            user = rng.choice(readers[doc])
            q = UNANSWERABLE_TEMPLATES[i % len(UNANSWERABLE_TEMPLATES)].format(
                ln=manifest[doc]["loan_number"]
            )
        rows.append(
            {
                "kind": "unanswerable",
                "user": user["user_id"],
                "question": q,
                "answerable": False,
                "answer": None,
                "gold": [],
            }
        )

    probes = 0
    for f in facts:
        if probes >= n_probes:
            break
        doc = str(f["doc_filename"])
        if doc in used_docs or not outsiders.get(doc):
            continue
        used_docs.add(doc)
        user = rng.choice(outsiders[doc])
        rows.append(
            {
                "kind": "probe",
                "user": user["user_id"],
                "question": question_for(f, rng),
                "answerable": False,
                "answer": f["answer"],
                "fact_type": f["fact_type"],
                "depth": depth_of(int(f["page"])),
                "context_dependent": bool(f["context_dependent"]),
                "gold": gold(f),
            }
        )
        probes += 1

    return [{"id": f"q{i:03d}", **r} for i, r in enumerate(rows, start=1)]


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--synth-dir", type=Path, default=Path("eval/synth"))
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--out", type=Path, default=Path("eval/questions.jsonl"))
    p.add_argument("--answerable", type=int, default=162)
    p.add_argument("--unanswerable", type=int, default=25)
    p.add_argument("--probes", type=int, default=35)
    args = p.parse_args()
    rows = build(
        facts_path=args.synth_dir / "facts.jsonl",
        manifest_path=args.synth_dir / "manifest.jsonl",
        acl_path=args.config_dir / "acl_seed.yaml",
        users_path=args.config_dir / "users_seed.yaml",
        seed=args.seed,
        n_answerable=args.answerable,
        n_unanswerable=args.unanswerable,
        n_probes=args.probes,
    )
    with args.out.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    summary: dict[str, int] = {}
    for r in rows:
        summary[str(r["kind"])] = summary.get(str(r["kind"]), 0) + 1
        if r["kind"] == "answerable":
            summary[f"depth {r['depth']}"] = summary.get(f"depth {r['depth']}", 0) + 1
            summary["context_dependent"] = summary.get("context_dependent", 0) + int(
                bool(r["context_dependent"])
            )
    print(json.dumps({"total": len(rows), **summary}))


if __name__ == "__main__":
    main()
