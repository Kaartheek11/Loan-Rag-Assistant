"""Write the Keycloak realm import files from the fixed user list (spec §6.1.1).

  docker/keycloak/realm-loanrag.json       development: Direct Access Grants ON (curl logins),
                                           dev passwords from ${DEV_USER_PASSWORD}
  docker/keycloak/realm-loanrag.prod.json  production: Direct Access Grants OFF, no credentials

Usage: uv run python scripts/make_realm.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.synth.generate_corpus import USERS  # noqa: E402


def realm(*, dev: bool) -> dict[str, object]:
    users = []
    for u in USERS:
        uid = str(u["user_id"])
        first, _, last = str(u["display"]).partition(" ")
        entry: dict[str, object] = {
            "id": uid,
            "username": uid.removeprefix("u_"),
            "enabled": True,
            "email": f"{uid.removeprefix('u_')}@example.test",
            "emailVerified": True,
            "firstName": first,
            "lastName": last or first,
            # Keycloak's standard default roles (account console: view/manage own profile).
            # These are Keycloak-only; the API ignores token roles and reads Postgres (§6.1).
            "realmRoles": ["default-roles-loanrag"],
        }
        if dev:
            # Keycloak substitutes ${ENV} placeholders at import time; no secret in git.
            entry["credentials"] = [
                {"type": "password", "value": "${DEV_USER_PASSWORD}", "temporary": False}
            ]
        users.append(entry)
    return {
        "realm": "loanrag",
        "enabled": True,
        "sslRequired": "external",
        "accessTokenLifespan": 900,  # 15 minutes (spec §6.1)
        "registrationAllowed": False,
        "clients": [
            {
                "clientId": "loanrag-api",
                "name": "Loan RAG API",
                "enabled": True,
                "publicClient": True,
                "protocol": "openid-connect",
                "standardFlowEnabled": True,
                "implicitFlowEnabled": False,
                "directAccessGrantsEnabled": dev,
                "serviceAccountsEnabled": False,
                "redirectUris": ["http://localhost:8000/*", "https://localhost/*"],
                "webOrigins": ["+"],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "protocolMappers": [
                    {
                        "name": "audience-loanrag-api",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.client.audience": "loanrag-api",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    }
                ],
            }
        ],
        "users": users,
    }


def main() -> None:
    out = ROOT / "docker" / "keycloak"
    out.mkdir(parents=True, exist_ok=True)
    (out / "realm-loanrag.json").write_text(json.dumps(realm(dev=True), indent=2) + "\n")
    (out / "realm-loanrag.prod.json").write_text(json.dumps(realm(dev=False), indent=2) + "\n")
    print("wrote", out / "realm-loanrag.json", "and", out / "realm-loanrag.prod.json")


if __name__ == "__main__":
    main()
