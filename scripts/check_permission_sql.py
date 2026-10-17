"""CI guard (spec §6.5, T18): ACL columns may only be referenced in the permission module.

Fails if `acl_groups` or `acl_users` appears in any Python or SQL file under the scanned
roots, except the permission module, the schema definition, and the migrations.

Usage: python scripts/check_permission_sql.py [ROOT ...]   (default: app eval)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERN = re.compile(r"\bacl_(groups|users)\b")
ALLOWED = (
    "app/security/permissions.py",
    "app/db/tables.py",  # schema definition (column declarations), not query SQL
    "app/db/migrations/",
)
SUFFIXES = {".py", ".sql"}


def _is_allowed(rel: str) -> bool:
    return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in ALLOWED)


def find_violations(repo_root: Path, roots: list[str]) -> list[str]:
    violations: list[str] = []
    for root in roots:
        base = repo_root / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            rel = path.relative_to(repo_root).as_posix()
            if _is_allowed(rel):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PATTERN.search(line):
                    violations.append(f"{rel}:{lineno}: {line.strip()}")
    return violations


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    roots = argv or ["app", "eval"]
    violations = find_violations(repo_root, roots)
    if violations:
        print("Permission SQL found outside app/security/permissions.py:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print("permission-SQL check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
