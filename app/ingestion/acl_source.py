"""ACL source (spec §6.3). Every ACL lookup goes through get_document_acl().

The YAML backend can later be replaced by a real system (loan origination system,
directory service) by implementing AclBackend; callers do not change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import yaml

from app.security.permissions import Acl


class AclBackend(Protocol):
    def lookup(self, filename: str) -> Acl | None: ...


class YamlAclBackend:
    def __init__(self, path: Path) -> None:
        data = yaml.safe_load(Path(path).read_text()) or {}
        docs = data.get("documents") or {}
        self._acls = {
            str(name): Acl.of(entry.get("groups") or [], entry.get("users") or [])
            for name, entry in docs.items()
            if isinstance(entry, dict)
        }

    def lookup(self, filename: str) -> Acl | None:
        return self._acls.get(filename)


_backend: AclBackend | None = None


def configure(backend: AclBackend) -> None:
    global _backend
    _backend = backend


def get_document_acl(filename: str, backend: AclBackend | None = None) -> Acl:
    """The document's ACL, or an empty ACL (default deny) when none is found."""
    source = backend or _backend
    if source is None:
        raise RuntimeError("no ACL backend configured")
    return source.lookup(filename) or Acl()
