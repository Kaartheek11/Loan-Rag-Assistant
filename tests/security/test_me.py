"""extension.md Phase A: /v1/me, /v1/me/documents, citation excerpts, session audit, no-store."""

from __future__ import annotations

from sqlalchemy import text

from tests.env import DOCS, USERS

BEEKEEPING = "Tell me about the beekeeping forbearance agreement."
VOLCANIC = "What is the annual escrow disbursement for volcanic ash roof cleaning?"


def _visible_docs(user: str) -> set[str]:
    groups = set(USERS[user]["groups"])
    return {name for name, (_, g, u) in DOCS.items() if set(g) & groups or user in u}


def _all_my_docs(env, user: str, limit: int = 2) -> list[dict]:  # type: ignore[no-untyped-def]
    headers = env.headers(user)
    items, cursor = [], None
    while True:
        url = f"/v1/me/documents?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        r = env.client.get(url, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            return items


def test_me_count_excludes_documents_only_alice_can_read(env) -> None:  # type: ignore[no-untyped-def]
    bob = env.client.get("/v1/me", headers=env.headers("u_bob")).json()
    alice = env.client.get("/v1/me", headers=env.headers("u_alice")).json()
    assert bob == {
        "user_id": "u_bob",
        "display_name": "u_bob",
        "groups": ["servicing"],
        "roles": [],
        "readable_document_count": len(_visible_docs("u_bob")),
    }
    alice_only = _visible_docs("u_alice") - _visible_docs("u_bob")
    assert "loan_900001.pdf" in alice_only
    assert alice["readable_document_count"] == len(_visible_docs("u_alice"))
    admin = env.client.get("/v1/me", headers=env.headers("u_admin")).json()
    assert admin["roles"] == ["admin"] and admin["readable_document_count"] == 0  # no bypass


def test_me_documents_never_lists_unreadable(env) -> None:  # type: ignore[no-untyped-def]
    listed = [i["filename"] for i in _all_my_docs(env, "u_bob", limit=1)]
    assert listed == sorted(_visible_docs("u_bob"))  # sorted by filename, cursor paging works
    assert "loan_900004.pdf" not in listed  # empty ACL
    with env.owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE documents SET status = 'pending' WHERE filename = 'loan_900007.pdf'")
        )
    env.client.delete(
        f"/v1/admin/documents/{env.doc_ids['loan_900002.pdf']}", headers=env.headers("u_admin")
    )
    listed = [i["filename"] for i in _all_my_docs(env, "u_bob")]
    assert listed == ["loan_900005.pdf"]  # pending and soft-deleted documents disappear
    bob = env.client.get("/v1/me", headers=env.headers("u_bob")).json()
    assert bob["readable_document_count"] == 1
    for user in USERS:
        names = {i["filename"] for i in _all_my_docs(env, user)}
        assert names <= _visible_docs(user)
        assert "loan_900004.pdf" not in names


def test_me_reflects_group_removal_with_same_token(env) -> None:  # type: ignore[no-untyped-def]
    headers = env.headers("u_bob")
    assert env.client.get("/v1/me", headers=headers).json()["groups"] == ["servicing"]
    with env.owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM user_groups WHERE user_id = 'u_bob'"))
    me = env.client.get("/v1/me", headers=headers).json()
    assert me["groups"] == [] and me["readable_document_count"] == 0
    r = env.client.get("/v1/me/documents", headers=headers)
    assert r.json() == {"items": [], "next_cursor": None}


def test_citation_excerpts_are_permitted_chunk_text(env) -> None:  # type: ignore[no-untyped-def]
    for user, question in (("u_alice", VOLCANIC), ("u_bob", BEEKEEPING)):
        body = env.query(user, question).json()
        assert body["status"] == "answered" and body["citations"]
        row = env.audit_rows()[-1]
        with env.owner_engine.connect() as conn:
            permitted = {
                r.text
                for r in conn.execute(
                    text("SELECT text FROM chunks WHERE chunk_id = ANY(:ids)"),
                    {"ids": list(row.returned_chunk_ids)},
                )
            }
        for c in body["citations"]:
            assert c["excerpt"] in permitted
            assert set(c) == {"marker", "doc_id", "filename", "page", "excerpt"}


def test_context_prefix_never_shown(env) -> None:  # type: ignore[no-untyped-def]
    with env.owner_engine.connect() as conn:
        prefixes = {p for p in conn.execute(text("SELECT context_prefix FROM chunks")).scalars()}
    assert prefixes and all(prefixes)
    body = env.query("u_alice", VOLCANIC).json()
    _, prompt = env.llm.calls[-1]
    page = env.client.get(
        f"/v1/documents/{body['citations'][0]['doc_id']}/pages/{body['citations'][0]['page']}",
        headers=env.headers("u_alice"),
    ).json()
    for prefix in prefixes:
        assert prefix not in prompt
        assert prefix not in page["text"]
        assert all(prefix not in c["excerpt"] for c in body["citations"])
    assert "| Section: " not in prompt and "| Section: " not in page["text"]


def test_page_endpoint_has_section_and_page_count(env) -> None:  # type: ignore[no-untyped-def]
    doc = env.doc_ids["loan_900007.pdf"]
    r = env.client.get(f"/v1/documents/{doc}/pages/2", headers=env.headers("u_bob")).json()
    assert r["page_count"] == 3
    assert r["section"] == "Correspondence - loan LN-2024-900007"


def test_session_start_written_once_per_token(env) -> None:  # type: ignore[no-untyped-def]
    def session_rows() -> list:  # type: ignore[type-arg]
        with env.owner_engine.connect() as conn:
            return conn.execute(text("SELECT * FROM audit_log WHERE event = 'session_start'")).all()

    headers = env.headers("u_alice")
    for _ in range(3):  # window-focus refreshes reuse the token
        assert env.client.get("/v1/me", headers=headers).status_code == 200
    rows = session_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == "u_alice" and row.query_hmac is None
    assert list(row.returned_chunk_ids) == [] and list(row.cited_chunk_ids) == []
    env.client.get("/v1/me", headers=env.headers("u_alice"))  # new token, new session
    assert len(session_rows()) == 2
    env.query("u_alice", VOLCANIC)
    with env.owner_engine.connect() as conn:
        q = conn.execute(
            text("SELECT event, query_hmac FROM audit_log ORDER BY id DESC LIMIT 1")
        ).one()
    assert q.event == "query" and q.query_hmac


def test_me_errors(env) -> None:  # type: ignore[no-untyped-def]
    assert env.client.get("/v1/me").json() == {"error": "unauthorized"}
    r = env.client.get("/v1/me", headers=env.headers("u_ghost"))
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    for bad in ("limit=0", "limit=101", "cursor=not-a-cursor", "cursor=e30"):
        r = env.client.get(f"/v1/me/documents?{bad}", headers=env.headers("u_bob"))
        assert r.status_code == 422 and r.json() == {"error": "validation_error"}, bad


def test_api_responses_are_no_store(env) -> None:  # type: ignore[no-untyped-def]
    h = env.headers("u_bob")
    for r in (
        env.client.get("/v1/me", headers=h),
        env.client.get("/v1/me/documents", headers=h),
        env.query("u_bob", BEEKEEPING),
        env.client.get("/v1/me"),  # 401
        env.client.get(f"/v1/documents/{env.doc_ids['loan_900001.pdf']}/pages/1", headers=h),
    ):
        assert r.headers["cache-control"] == "no-store"
    assert "cache-control" not in env.client.get("/health").headers


def test_request_id_header_matches_answer_and_audit(env) -> None:  # type: ignore[no-untyped-def]
    r = env.query("u_alice", VOLCANIC)
    assert r.headers["x-request-id"] == r.json()["request_id"]
    assert str(env.audit_rows()[-1].request_id) == r.headers["x-request-id"]
    err = env.client.get("/v1/me")  # errors carry the header, never extra body fields
    assert err.headers["x-request-id"] and err.json() == {"error": "unauthorized"}
