"""Build the labeled evaluation set from facts.jsonl (spec §11.2).

Gold answers are labeled by document + page + planted fact text, never by chunk ID.

Output rows (eval/questions.jsonl):
  {"id", "kind": answerable|unanswerable|probe, "user", "question", "answerable",
   "answer", "gold": [{"doc", "page", "fact"}]}

Usage:
  uv run python -m eval.synth.generate_questions --seed 7 \
      --answerable 160 --unanswerable 25 --probes 35
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import yaml

TEMPLATES: dict[str, list[str]] = {
    "escrow_item": [
        "What is the annual escrow disbursement for {hint} on loan {ln}?",
        "How much does the escrow account pay each year for {hint} under loan {ln}?",
    ],
    "condition": [
        "Before funding loan {ln}, which organization must provide the document {hint}?",
        "Who must issue the paperwork {hint} as a condition of approval for loan {ln}?",
    ],
    "reserves": [
        "How much in post-closing reserves did the underwriter document for loan {ln}?",
        "What reserve amount was listed as a compensating factor on loan {ln}?",
    ],
    "rate_lock": [
        "When does the interest rate lock expire for loan {ln}?",
        "What is the rate lock expiration date on loan {ln}?",
    ],
    "hoa_dues": [
        "What are the monthly HOA dues for loan {ln}?",
        "How much are the homeowners association dues each month on loan {ln}?",
    ],
    "prepayment_penalty": [
        "What prepayment penalty percentage applies to loan {ln}?",
        "What percent of the balance is charged as a prepayment penalty on loan {ln}?",
    ],
    "interest_rate": ["What is the interest rate on loan {ln}?"],
    "principal": ["What is the original principal amount of loan {ln}?"],
    "term": ["What is the term of loan {ln}?"],
}
# Planted facts are the main signal; base facts (rate/principal/term) are a minority.
PLANTED = ["escrow_item", "condition", "reserves", "rate_lock", "hoa_dues", "prepayment_penalty"]
BASE = ["interest_rate", "principal", "term"]

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


def _visible(user: dict[str, object], acl: dict[str, list[str]]) -> bool:
    groups = set(user.get("groups") or [])  # type: ignore[arg-type]
    return bool(groups & set(acl.get("groups") or [])) or user["user_id"] in (
        acl.get("users") or []
    )


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
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    facts = [json.loads(line) for line in facts_path.open()]
    manifest = {r["filename"]: r for r in (json.loads(line) for line in manifest_path.open())}
    acls = yaml.safe_load(acl_path.read_text())["documents"]
    users = [u for u in yaml.safe_load(users_path.read_text())["users"]]
    readers = {f: [u for u in users if _visible(u, a)] for f, a in acls.items()}
    outsiders = {f: [u for u in users if not _visible(u, a)] for f, a in acls.items()}

    by_type: dict[str, list[dict[str, object]]] = {}
    for f in facts:
        by_type.setdefault(f["fact_type"], []).append(f)
    for lst in by_type.values():
        rng.shuffle(lst)

    rows: list[dict[str, object]] = []
    used_docs: set[str] = set()

    def take(ftype: str, need_readers: bool) -> dict[str, object] | None:
        pool = by_type.get(ftype, [])
        while pool:
            f = pool.pop()
            doc = str(f["doc_filename"])
            if doc in used_docs:
                continue
            if need_readers and not readers.get(doc):
                continue
            if not need_readers and not outsiders.get(doc):
                continue
            used_docs.add(doc)
            return f
        return None

    def question_for(f: dict[str, object]) -> str:
        template = rng.choice(TEMPLATES[str(f["fact_type"])])
        return template.format(ln=f["loan_number"], hint=f.get("question_hint", ""))

    def gold(f: dict[str, object]) -> list[dict[str, object]]:
        return [{"doc": f["doc_filename"], "page": f["page"], "fact": f["fact_text"]}]

    # Answerable: ~80% planted facts, ~20% base facts, asked by a user who can read the doc.
    n_base = n_answerable // 5
    plan = [PLANTED[i % len(PLANTED)] for i in range(n_answerable - n_base)]
    plan += [BASE[i % len(BASE)] for i in range(n_base)]
    for ftype in plan:
        f = take(ftype, need_readers=True)
        if f is None:
            continue
        user = rng.choice(readers[str(f["doc_filename"])])
        rows.append(
            {
                "kind": "answerable",
                "user": user["user_id"],
                "question": question_for(f),
                "answerable": True,
                "answer": f["answer"],
                "fact_type": f["fact_type"],
                "scanned": manifest[str(f["doc_filename"])]["scanned"],
                "gold": gold(f),
            }
        )

    # Unanswerable: facts that exist in no document, asked by a reader of a real loan,
    # plus questions about loan numbers that do not exist.
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

    # Permission probes: the gold document exists but is hidden from the asking user.
    for i in range(n_probes):
        f = take(PLANTED[i % len(PLANTED)], need_readers=False)
        if f is None:
            continue
        user = rng.choice(outsiders[str(f["doc_filename"])])
        rows.append(
            {
                "kind": "probe",
                "user": user["user_id"],
                "question": question_for(f),
                "answerable": False,
                "answer": f["answer"],
                "fact_type": f["fact_type"],
                "gold": gold(f),
            }
        )

    for i, row in enumerate(rows, start=1):
        row["id"] = f"q{i:03d}"
    return [{"id": r.pop("id"), **r} for r in rows]


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--synth-dir", type=Path, default=Path("eval/synth"))
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--out", type=Path, default=Path("eval/questions.jsonl"))
    p.add_argument("--answerable", type=int, default=160)
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
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[str(r["kind"])] = kinds.get(str(r["kind"]), 0) + 1
    print(json.dumps({"total": len(rows), **kinds}))


if __name__ == "__main__":
    main()
