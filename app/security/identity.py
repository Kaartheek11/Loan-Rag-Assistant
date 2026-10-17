"""The resolved, immutable identity of a request (spec §6.1)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    groups: frozenset[str]
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles
