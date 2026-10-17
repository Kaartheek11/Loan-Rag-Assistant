"""T6, T7, T7b, T13: identity comes only from a verified RS256 token plus Postgres."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import text

from app.security.auth import JwksCache, TokenVerifier, generate_rsa_keypair, mint_token
from tests.env import AUDIENCE, ISSUER

UNAUTHORIZED = {"error": "unauthorized"}
BEEKEEPING = "Tell me about the beekeeping forbearance agreement."


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims(sub: str = "u_alice", **kw: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": sub,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
    }
    claims.update(kw)
    return claims


def _hs256_with_public_key(public_pem: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(_claims()).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(public_pem.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(sig)}"


def _alg_none() -> str:
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(_claims()).encode())
    return f"{header}.{payload}."


def test_t7_bad_tokens_get_401(env, keypair) -> None:  # type: ignore[no-untyped-def]
    other_private, _ = generate_rsa_keypair()
    public_pem = keypair[1].read_text()
    bad_headers = {
        "missing": {},
        "not_bearer": {"Authorization": "Basic dTpw"},
        "garbage": {"Authorization": "Bearer not.a.jwt"},
        "forged": {
            "Authorization": "Bearer "
            + mint_token(other_private, sub="u_alice", issuer=ISSUER, audience=AUDIENCE)
        },
        "expired": {"Authorization": "Bearer " + env.token("u_alice", lifetime_seconds=-30)},
        "wrong_issuer": {
            "Authorization": "Bearer "
            + mint_token(
                env.private_pem,
                sub="u_alice",
                issuer="http://evil/realms/loanrag",
                audience=AUDIENCE,
            )
        },
        "wrong_audience": {
            "Authorization": "Bearer "
            + mint_token(env.private_pem, sub="u_alice", issuer=ISSUER, audience="account")
        },
        "hs256_alg_confusion": {"Authorization": "Bearer " + _hs256_with_public_key(public_pem)},
        "alg_none": {"Authorization": "Bearer " + _alg_none()},
        "unknown_user": {"Authorization": "Bearer " + env.token("u_ghost")},
    }
    for name, headers in bad_headers.items():
        r = env.client.post("/v1/query", json={"question": BEEKEEPING}, headers=headers)
        assert r.status_code == 401, name
        assert r.json() == UNAUTHORIZED, name
        r = env.client.get(
            f"/v1/documents/{env.doc_ids['loan_900002.pdf']}/pages/1", headers=headers
        )
        assert r.status_code == 401 and r.json() == UNAUTHORIZED, name
    assert env.llm.calls == []
    assert env.audit_rows() == []


def test_t7_disabled_user_gets_401(env) -> None:  # type: ignore[no-untyped-def]
    headers = env.headers("u_bob")
    assert env.query("u_bob", BEEKEEPING, headers=headers).status_code == 200
    with env.owner_engine.begin() as conn:
        conn.execute(text("UPDATE users SET disabled = true WHERE user_id = 'u_bob'"))
    r = env.query("u_bob", BEEKEEPING, headers=headers)
    assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_t7b_group_claims_in_token_are_ignored(env) -> None:  # type: ignore[no-untyped-def]
    headers = env.headers(
        "u_alice",
        extra_claims={
            "groups": ["servicing", "executive"],
            "roles": ["admin"],
            "realm_access": {"roles": ["admin"]},
        },
    )
    r = env.query("u_alice", BEEKEEPING, headers=headers)
    assert r.status_code == 200
    row = env.audit_rows()[-1]
    assert "loan_900002.pdf" not in env.returned_docs(row)
    assert env.returned_docs(row) <= {"loan_900001.pdf", "loan_900005.pdf"}
    # And the admin claim grants nothing.
    r = env.client.get("/v1/admin/documents", headers=headers)
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}


def test_t6_identity_fields_in_request_are_rejected(env) -> None:  # type: ignore[no-untyped-def]
    for extra in ({"user_id": "u_bob"}, {"groups": ["servicing"]}, {"role": "admin"}):
        r = env.client.post(
            "/v1/query", json={"question": BEEKEEPING, **extra}, headers=env.headers("u_alice")
        )
        assert r.status_code == 422
        assert r.json() == {"error": "validation_error"}
    # Query string and extra headers are ignored, not trusted.
    r = env.client.post(
        "/v1/query?user_id=u_bob&groups=servicing",
        json={"question": BEEKEEPING},
        headers={**env.headers("u_alice"), "X-User-Id": "u_bob", "X-Groups": "servicing"},
    )
    assert r.status_code == 200
    row = env.audit_rows()[-1]
    assert row.user_id == "u_alice"
    assert "loan_900002.pdf" not in env.returned_docs(row)


def test_t13_question_length_cap(env) -> None:  # type: ignore[no-untyped-def]
    r = env.query("u_alice", "x" * 1001)
    assert r.status_code == 422 and r.json() == {"error": "validation_error"}
    assert env.query("u_alice", "x" * 1000).status_code == 200
    assert env.query("u_alice", "").status_code == 422
    r = env.client.post(
        "/v1/query", json={"question": "hi", "top_k": 11}, headers=env.headers("u_alice")
    )
    assert r.status_code == 422


def test_jwks_refetch_on_unknown_kid_is_throttled() -> None:
    from jwt.algorithms import RSAAlgorithm

    priv1, pub1 = generate_rsa_keypair()
    priv2, pub2 = generate_rsa_keypair()

    def jwk(pub: str, kid: str) -> dict[str, object]:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        d = json.loads(RSAAlgorithm.to_jwk(load_pem_public_key(pub.encode())))
        d.update({"kid": kid, "use": "sig", "alg": "RS256"})
        return d

    published = {"keys": [jwk(pub1, "k1")]}
    now = [1000.0]
    cache = JwksCache("http://unused", fetch=lambda: published, clock=lambda: now[0])
    verifier = TokenVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=cache)
    t1 = mint_token(priv1, sub="u_alice", issuer=ISSUER, audience=AUDIENCE, kid="k1")
    assert verifier.verify(t1) == "u_alice"
    assert cache.fetch_count == 1
    # Key rotation: k2 appears. A token with unknown kid within the minute is rejected
    # without refetching; after a minute the cache refetches and accepts it.
    published = {"keys": [jwk(pub1, "k1"), jwk(pub2, "k2")]}
    cache._fetch = lambda: published  # type: ignore[method-assign]
    t2 = mint_token(priv2, sub="u_bob", issuer=ISSUER, audience=AUDIENCE, kid="k2")
    now[0] += 10
    from app.security.auth import AuthError

    with pytest.raises(AuthError):
        verifier.verify(t2)
    assert cache.fetch_count == 1
    now[0] += 60
    assert verifier.verify(t2) == "u_bob"
    assert cache.fetch_count == 2
    assert verifier.verify(t1) == "u_alice"  # known kid: no refetch
    assert cache.fetch_count == 2
