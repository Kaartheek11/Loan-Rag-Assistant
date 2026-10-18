"""Phase B check (extension.md §13): a browser-style sign-in through Caddy reaches the app page,
and /v1/me returns 200 with the resulting token.

Performs the same steps as keycloak-js: authorization code flow with PKCE (S256) against the
`loanrag-web` client, form login on Keycloak's sign-in page, redirect back to the app origin,
code exchange. Also checks that the client refuses what BE-5 forbids.

Usage: uv run python scripts/check_web_login.py [username]   (password from DEV_USER_PASSWORD)
Development only: TLS verification is off for Caddy's local certificate.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ORIGIN = "https://localhost"
REALM = f"{ORIGIN}/auth/realms/loanrag/protocol/openid-connect"
CLIENT = "loanrag-web"


def dev_password() -> str:
    if os.environ.get("DEV_USER_PASSWORD"):
        return os.environ["DEV_USER_PASSWORD"]
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("DEV_USER_PASSWORD="):
            return line.split("=", 1)[1]
    sys.exit("DEV_USER_PASSWORD not set")


def claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def check(ok: bool, label: str) -> None:
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        sys.exit(1)


def main(username: str) -> None:
    client = httpx.Client(verify=False, follow_redirects=False, timeout=15)  # noqa: S501
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    challenge = challenge.rstrip("=")
    params = {
        "client_id": CLIENT,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": f"{ORIGIN}/",
        "state": secrets.token_urlsafe(8),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    login_page = client.get(f"{REALM}/auth?{urlencode(params)}")
    check(login_page.status_code == 200, "Keycloak sign-in page is served under /auth")
    action = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', login_page.text)
    check(action is not None, "sign-in form found")
    assert action is not None
    resp = client.post(
        unescape(action.group(1)), data={"username": username, "password": dev_password()}
    )
    location = resp.headers.get("location", "")
    check(
        resp.status_code == 302 and location.startswith(f"{ORIGIN}/"),
        "sign-in redirects back to the app origin",
    )
    code = parse_qs(urlparse(location).query).get("code", [""])[0]
    check(bool(code), "authorization code returned")
    page = client.get(location)
    check(
        page.status_code == 200 and "<html" in page.text.lower(), "redirect lands on the app page"
    )

    token = client.post(
        f"{REALM}/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT,
            "code": code,
            "redirect_uri": f"{ORIGIN}/",
            "code_verifier": verifier,
        },
    )
    check(token.status_code == 200, "code exchange with PKCE verifier succeeds")
    access = token.json()["access_token"]
    c = claims(access)
    aud = c["aud"] if isinstance(c["aud"], list) else [c["aud"]]
    check(c["iss"] == f"{ORIGIN}/auth/realms/loanrag", f"issuer is {c['iss']}")
    check("loanrag-api" in aud and c["azp"] == CLIENT, f"aud {aud} includes loanrag-api")

    me = client.get(f"{ORIGIN}/v1/me", headers={"Authorization": f"Bearer {access}"})
    check(me.status_code == 200, f"/v1/me returns 200: {me.text}")
    check(me.headers.get("cache-control") == "no-store", "/v1/me is Cache-Control: no-store")

    # What BE-5 forbids.
    no_pkce = {k: v for k, v in params.items() if not k.startswith("code_challenge")}
    r = client.get(f"{REALM}/auth?{urlencode(no_pkce)}")
    check(
        "code_challenge" in r.text or "error" in r.headers.get("location", ""),
        "authorization without PKCE is refused",
    )
    r = client.get(f"{REALM}/auth?{urlencode({**params, 'redirect_uri': 'https://evil.test/'})}")
    check(
        r.status_code == 400 or "redirect_uri" in r.text,
        "a redirect URI outside https://localhost is refused",
    )
    r = client.post(
        f"{REALM}/token",
        data={
            "grant_type": "password",
            "client_id": CLIENT,
            "username": username,
            "password": dev_password(),
        },
    )
    check(
        r.status_code in (400, 401) and r.json().get("error") == "unauthorized_client",
        "direct access grant is disabled on loanrag-web",
    )
    print("Phase B check passed.")


if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore")
    main(sys.argv[1] if len(sys.argv) > 1 else "alice")
