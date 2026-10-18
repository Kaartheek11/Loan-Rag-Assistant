"""T1-T5, T15-T18: the retrieval layer enforces permissions in code."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.retrieval.fusion import rrf
from app.retrieval.index_manager import FaissIndex
from app.retrieval.keyword_search import keyword_search
from tests.env import DOCS, USERS, random_doc_id

BEEKEEPING = "Tell me about the beekeeping forbearance agreement."
ZEPPELIN = "What did the board approve for the zeppelin hangar conversion?"
ORCHARD = "Who holds harvest rights under the orchard lease with the cidery?"
VOLCANIC = "What is the annual escrow disbursement for volcanic ash roof cleaning?"


def _visible(user: str, filename: str) -> bool:
    _, groups, users = DOCS[filename]
    return bool(set(groups) & set(USERS[user]["groups"])) or user in users


def test_t1_hidden_group_chunks_never_reach_llm_response_or_audit(env) -> None:  # type: ignore[no-untyped-def]
    hidden_ids = env.chunk_ids["loan_900002.pdf"]
    r = env.query("u_alice", BEEKEEPING)
    assert r.status_code == 200
    body = r.json()
    assert all(c["filename"] != "loan_900002.pdf" for c in body["citations"])
    assert "beekeeping" not in body["answer"].lower() or body["status"] == "insufficient"
    for _system, user in env.llm.calls:
        assert "loan_900002.pdf" not in user
        assert "apiary" not in user
    row = env.audit_rows()[-1]
    assert not set(row.returned_chunk_ids) & hidden_ids
    assert not set(row.cited_chunk_ids) & hidden_ids
    # The servicing user does get it.
    r = env.query("u_bob", BEEKEEPING)
    assert r.json()["status"] == "answered"
    assert {c["filename"] for c in r.json()["citations"]} == {"loan_900002.pdf"}


def test_t2_difference_between_users_matches_acls(env, embedder) -> None:  # type: ignore[no-untyped-def]
    question = "escrow statement, income verification and borrower payments"
    holder = env.app.state.index
    qvec = embedder.embed_query(question)
    with env.app_engine.connect() as conn:
        vec = holder.search(qvec, 50)
        kw = keyword_search(conn, question, 50)
    doc_of = env.doc_of_chunk()
    for user in ("u_alice", "u_bob", "u_carol", "u_dana"):
        r = env.query(user, question, top_k=6)
        assert r.status_code == 200
        returned = list(env.audit_rows()[-1].returned_chunk_ids)
        # Independent oracle: ACLs from the test fixture, not from the permission module.
        vis_vec = [c for c in vec if _visible(user, doc_of[c])]
        vis_kw = [c for c in kw if _visible(user, doc_of[c])]
        expected = [c for c, _ in rrf([lst for lst in (vis_vec, vis_kw) if lst])][:6]
        assert returned == expected, user
        assert all(_visible(user, doc_of[c]) for c in returned)


def test_t3_group_removal_takes_effect_on_next_request(env) -> None:  # type: ignore[no-untyped-def]
    headers = env.headers("u_bob")  # the same, unexpired token is reused
    env.query("u_bob", BEEKEEPING, headers=headers)
    assert "loan_900002.pdf" in env.returned_docs(env.audit_rows()[-1])
    with env.owner_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM user_groups WHERE user_id = 'u_bob' AND group_name = 'servicing'")
        )
    r = env.query("u_bob", BEEKEEPING, headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "insufficient"
    assert env.returned_docs(env.audit_rows()[-1]) == set()


def test_t4_acl_change_updates_chunk_copies_and_next_query(env) -> None:  # type: ignore[no-untyped-def]
    doc = env.doc_ids["loan_900006.pdf"]
    admin = env.headers("u_admin")
    env.query("u_alice", ZEPPELIN)
    assert "loan_900006.pdf" not in env.returned_docs(env.audit_rows()[-1])

    assert (
        env.client.put(
            f"/v1/admin/documents/{doc}/acl",
            json={"groups": ["underwriting"], "users": []},
            headers=env.headers("u_alice"),
        ).status_code
        == 403
    )
    r = env.client.put(
        f"/v1/admin/documents/{doc}/acl",
        json={"groups": ["underwriting"], "users": ["u_henry"]},
        headers=admin,
    )
    assert r.status_code == 200
    with env.owner_engine.connect() as conn:
        d = conn.execute(
            text("SELECT acl_groups, acl_users FROM documents WHERE doc_id = :d"), {"d": doc}
        ).one()
        chunks = conn.execute(
            text("SELECT acl_groups, acl_users FROM chunks WHERE doc_id = :d"), {"d": doc}
        ).all()
    assert d.acl_groups == ["underwriting"] and d.acl_users == ["u_henry"]
    assert chunks and all(
        c.acl_groups == ["underwriting"] and c.acl_users == ["u_henry"] for c in chunks
    )
    r = env.query("u_alice", ZEPPELIN)
    assert {c["filename"] for c in r.json()["citations"]} == {"loan_900006.pdf"}
    env.query("u_erin", ZEPPELIN)  # executive lost access
    assert "loan_900006.pdf" not in env.returned_docs(env.audit_rows()[-1])
    r = env.client.put(
        f"/v1/admin/documents/{random_doc_id()}/acl", json={"groups": ["servicing"]}, headers=admin
    )
    assert r.status_code == 404 and r.json() == {"error": "not_found"}


def test_t4_acl_update_is_one_transaction(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.security import permissions

    doc = env.doc_ids["loan_900006.pdf"]
    monkeypatch.setattr(
        permissions, "_UPDATE_CHUNK_ACL_SQL", text("UPDATE chunks SET no_such_column = 1")
    )
    r = env.client.put(
        f"/v1/admin/documents/{doc}/acl",
        json={"groups": ["underwriting"], "users": []},
        headers=env.headers("u_admin"),
    )
    assert r.status_code == 500 and r.json() == {"error": "internal_error"}
    with env.owner_engine.connect() as conn:
        groups = conn.execute(
            text("SELECT acl_groups FROM documents WHERE doc_id = :d"), {"d": doc}
        ).scalar_one()
    assert groups == ["executive"]  # rolled back with the failed chunk update


def test_t5_delete_excludes_immediately_and_removes_vectors(env) -> None:  # type: ignore[no-untyped-def]
    doc = env.doc_ids["loan_900002.pdf"]
    ids = env.chunk_ids["loan_900002.pdf"]
    version_before = env.app.state.index.version
    assert (
        env.client.delete(f"/v1/admin/documents/{doc}", headers=env.headers("u_bob")).status_code
        == 403
    )
    r = env.client.delete(f"/v1/admin/documents/{doc}", headers=env.headers("u_admin"))
    assert r.status_code == 200
    r = env.query("u_bob", BEEKEEPING)
    assert "loan_900002.pdf" not in env.returned_docs(env.audit_rows()[-1])
    assert r.json()["status"] in ("insufficient", "answered")
    assert all(c["filename"] != "loan_900002.pdf" for c in r.json()["citations"])
    on_disk = FaissIndex.load(Path(env.settings.faiss_index_path)).ids()
    assert not on_disk & ids
    assert env.app.state.index.version == version_before + 1  # API reloaded the new index
    assert (
        env.client.get(f"/v1/documents/{doc}/pages/1", headers=env.headers("u_bob")).status_code
        == 404
    )
    # Deleting again: not found.
    assert (
        env.client.delete(f"/v1/admin/documents/{doc}", headers=env.headers("u_admin")).status_code
        == 404
    )


def test_t5_excluded_even_if_vector_removal_fails(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(self, ids):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated FAISS failure")

    monkeypatch.setattr(FaissIndex, "remove", boom)
    doc = env.doc_ids["loan_900002.pdf"]
    ids = env.chunk_ids["loan_900002.pdf"]
    r = env.client.delete(f"/v1/admin/documents/{doc}", headers=env.headers("u_admin"))
    assert r.status_code == 200
    monkeypatch.undo()
    assert ids <= FaissIndex.load(Path(env.settings.faiss_index_path)).ids()  # still there
    env.query("u_bob", BEEKEEPING)
    assert not set(env.audit_rows()[-1].returned_chunk_ids) & ids


def test_t15_empty_acl_invisible_to_everyone(env) -> None:  # type: ignore[no-untyped-def]
    hidden = env.chunk_ids["loan_900004.pdf"]
    doc = env.doc_ids["loan_900004.pdf"]
    for user in USERS:
        r = env.query(user, ORCHARD)
        assert r.status_code == 200
        assert not set(env.audit_rows()[-1].returned_chunk_ids) & hidden, user
        assert (
            env.client.get(f"/v1/documents/{doc}/pages/1", headers=env.headers(user)).status_code
            == 404
        )


def test_t16_pending_document_with_vectors_is_never_returned(env) -> None:  # type: ignore[no-untyped-def]
    doc = env.doc_ids["loan_900001.pdf"]
    ids = env.chunk_ids["loan_900001.pdf"]
    with env.owner_engine.begin() as conn:
        conn.execute(text("UPDATE documents SET status = 'pending' WHERE doc_id = :d"), {"d": doc})
    assert ids <= FaissIndex.load(Path(env.settings.faiss_index_path)).ids()
    r = env.query("u_alice", VOLCANIC)
    assert not set(env.audit_rows()[-1].returned_chunk_ids) & ids
    assert all(c["filename"] != "loan_900001.pdf" for c in r.json()["citations"])
    assert (
        env.client.get(f"/v1/documents/{doc}/pages/1", headers=env.headers("u_alice")).status_code
        == 404
    )


def test_t17_admin_without_groups_sees_nothing(env) -> None:  # type: ignore[no-untyped-def]
    for question in (BEEKEEPING, ZEPPELIN, ORCHARD, VOLCANIC):
        r = env.query("u_admin", question)
        assert r.status_code == 200
        assert r.json()["status"] == "insufficient"
        assert list(env.audit_rows()[-1].returned_chunk_ids) == []
    for doc in env.doc_ids.values():
        assert (
            env.client.get(
                f"/v1/documents/{doc}/pages/1", headers=env.headers("u_admin")
            ).status_code
            == 404
        )
    assert env.llm.calls == []
    # Admin powers are management only.
    r = env.client.get("/v1/admin/documents?status=indexed", headers=env.headers("u_admin"))
    assert r.status_code == 200
    rows = r.json()["items"]
    assert len(rows) == len(DOCS)
    # Metadata only (BE-12): no page or chunk text.
    assert all(
        set(row)
        == {
            "doc_id",
            "filename",
            "page_count",
            "status",
            "failure_reason",
            "groups",
            "users",
            "uploaded_by",
            "ingested_at",
            "upload",
        }
        for row in rows
    )


def test_t18_permission_sql_check(tmp_path: Path) -> None:
    from scripts.check_permission_sql import find_violations, main

    repo = Path(__file__).resolve().parents[2]
    assert find_violations(repo, ["app", "eval"]) == []
    assert main([]) == 0
    bad = tmp_path / "app" / "retrieval"
    bad.mkdir(parents=True)
    (bad / "sneaky.py").write_text('SQL = "SELECT * FROM chunks WHERE acl_groups && :g"\n')
    (tmp_path / "app" / "security").mkdir()
    (tmp_path / "app" / "security" / "permissions.py").write_text('X = "acl_users"\n')
    found = find_violations(tmp_path, ["app"])
    assert len(found) == 1 and "sneaky.py" in found[0]


@pytest.mark.parametrize("user", ["u_dana"])
def test_explicit_user_grant(env, user) -> None:  # type: ignore[no-untyped-def]
    q = "What did the fair lending review flag for the lighthouse restoration loan?"
    r = env.query(user, q)
    assert {c["filename"] for c in r.json()["citations"]} == {"loan_900003.pdf"}
    env.query("u_bob", q)  # same group as dana, but no explicit grant
    assert "loan_900003.pdf" not in env.returned_docs(env.audit_rows()[-1])
