"""extension.md §6.1 backend upload tests, plus BE-10 to BE-13."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import text

from app.ingestion.acl_source import YamlAclBackend
from app.ingestion.pipeline import IngestionPipeline
from app.retrieval.index_manager import FaissIndex
from app.worker import UploadWorker
from tests.helpers import make_pdf, write_acl

ZEBRA = "What does the file say about the zebra crossing permit fee?"
ADMIN_ENDPOINTS = [
    ("get", "/v1/admin/documents"),
    ("get", "/v1/admin/uploads"),
    ("get", "/v1/admin/uploads/00000000-0000-0000-0000-000000000000"),
    ("post", "/v1/admin/uploads/00000000-0000-0000-0000-000000000000/retry"),
    ("get", "/v1/admin/groups"),
    ("get", "/v1/admin/users?q=a"),
    ("post", "/v1/admin/uploads"),
    ("put", "/v1/admin/documents/00000000-0000-0000-0000-000000000000/acl"),
    ("delete", "/v1/admin/documents/00000000-0000-0000-0000-000000000000"),
]


def pdf_bytes(tmp: Path, name: str, pages: list[str]) -> bytes:
    return make_pdf(tmp / name, pages).read_bytes()


def zebra_pdf(tmp: Path) -> bytes:
    return pdf_bytes(
        tmp,
        "zebra.pdf",
        [
            "UPLOADED FILE - LOAN LN-2024-990001\nBorrower name: Zara Upload.",
            "PERMITS\nThe zebra crossing permit fee was $612.34 for the driveway.",
        ],
    )


def upload(
    env,
    data: bytes,
    name: str = "zebra.pdf",
    groups=("underwriting",),
    users=(),  # type: ignore[no-untyped-def]
    user: str = "u_admin",
):
    files = [("file", (name, data, "application/pdf"))]
    form = {"groups[]": list(groups), "users[]": list(users)}
    return env.client.post("/v1/admin/uploads", files=files, data=form, headers=env.headers(user))


def worker(env) -> UploadWorker:  # type: ignore[no-untyped-def]
    return UploadWorker(
        env.app_engine,
        env.app.state.embedder,
        index_path=Path(env.settings.faiss_index_path),
        store_dir=Path(env.settings.document_store_path),
        lock_wait_seconds=0.2,
    )


def test_non_admin_gets_403_on_every_admin_endpoint(env) -> None:  # type: ignore[no-untyped-def]
    for method, path in ADMIN_ENDPOINTS:
        r = getattr(env.client, method)(path, headers=env.headers("u_alice"))
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}, path


def test_rejects_non_pdf_encrypted_and_oversized(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = upload(env, b"just some text, not a pdf\n" * 10, name="notes.pdf")
    assert r.status_code == 422 and r.json() == {"error": "not_a_pdf"}
    r = upload(env, b"%PDF-1.4 broken body")
    assert r.status_code == 422 and r.json() == {"error": "not_a_pdf"}

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(zebra_pdf(tmp_path))).pages:
        writer.add_page(page)
    writer.encrypt("secret-password")
    buf = io.BytesIO()
    writer.write(buf)
    r = upload(env, buf.getvalue(), name="locked.pdf")
    assert r.status_code == 422 and r.json() == {"error": "encrypted_pdf"}

    big = b"%PDF-1.4\n" + b"0" * (51 * 1024 * 1024)
    r = upload(env, big, name="big.pdf")
    assert r.status_code == 413 and r.json() == {"error": "file_too_large"}
    with env.owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM upload_jobs")).scalar_one() == 0
    store = Path(env.settings.document_store_path)
    assert not list((store / ".incoming").glob("*")) if (store / ".incoming").exists() else True


def test_rejects_too_many_pages(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    writer = PdfWriter()
    for _ in range(501):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    r = upload(env, buf.getvalue(), name="huge.pdf")
    assert r.status_code == 422 and r.json() == {"error": "too_many_pages"}


def test_rejects_missing_or_unknown_access(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    data = zebra_pdf(tmp_path)
    for groups, users in (
        ((), ()),
        (("no_such_group",), ()),
        ((), ("u_nobody",)),
        (("underwriting", "made_up"), ()),
    ):
        r = upload(env, data, groups=groups, users=users)
        assert r.status_code == 422 and r.json() == {"error": "invalid_access"}, (groups, users)
    assert upload(env, data, groups=(), users=("u_dana",)).status_code == 202  # a person suffices


def test_duplicate_upload_gets_409(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    data = zebra_pdf(tmp_path)
    first = upload(env, data, name="first_name.pdf")
    assert first.status_code == 202
    r = upload(env, data, name="second_name.pdf")
    assert r.status_code == 409
    assert r.json() == {"error": "duplicate", "existing_filename": "first_name.pdf"}
    # A corpus file uploaded again is a duplicate too.
    corpus_pdf = Path(env.settings.document_store_path) / f"{env.doc_ids['loan_900001.pdf']}.pdf"
    r = upload(env, corpus_pdf.read_bytes(), name="again.pdf")
    assert r.status_code == 409 and r.json()["existing_filename"] == "loan_900001.pdf"


def test_queued_upload_is_invisible_then_visible_to_its_groups_only(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = upload(env, zebra_pdf(tmp_path))
    assert r.status_code == 202
    body = r.json()
    assert set(body) == {"job_id", "doc_id", "filename", "status"} and body["status"] == "queued"
    doc_id = body["doc_id"]

    def visible_to(user: str) -> tuple[bool, bool, int]:
        q = env.query(user, ZEBRA).json()
        in_answer = any(c["doc_id"] == doc_id for c in q["citations"])
        listed = any(
            d["doc_id"] == doc_id
            for d in env.client.get("/v1/me/documents?limit=100", headers=env.headers(user)).json()[
                "items"
            ]
        )
        page = env.client.get(f"/v1/documents/{doc_id}/pages/1", headers=env.headers(user))
        return in_answer, listed, page.status_code

    assert visible_to("u_alice") == (False, False, 404)  # queued
    with env.owner_engine.begin() as conn:
        conn.execute(text("UPDATE upload_jobs SET status = 'processing'"))
    assert visible_to("u_alice") == (False, False, 404)  # processing
    with env.owner_engine.begin() as conn:
        conn.execute(text("UPDATE upload_jobs SET status = 'queued'"))

    assert worker(env).run_once() == 1
    job = env.client.get(f"/v1/admin/uploads/{body['job_id']}", headers=env.headers("u_admin"))
    assert job.json()["status"] == "indexed"
    assert job.json()["pages_done"] == job.json()["pages_total"] == 2
    assert visible_to("u_alice") == (True, True, 200)  # underwriting
    assert visible_to("u_bob")[1:] == (False, 404)  # servicing only
    assert visible_to("u_admin")[1:] == (False, 404)  # the uploading admin is in no group
    with env.owner_engine.connect() as conn:
        doc = conn.execute(
            text("SELECT loan_number, borrower_name, status FROM documents WHERE doc_id = :d"),
            {"d": doc_id},
        ).one()
    assert doc == ("LN-2024-990001", "Zara Upload", "indexed")


def test_killed_worker_job_is_recovered_without_duplicates(env, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    doc_id = upload(env, zebra_pdf(tmp_path)).json()["doc_id"]

    def crash(self, path):  # type: ignore[no-untyped-def]
        raise KeyboardInterrupt("worker killed")

    monkeypatch.setattr(FaissIndex, "save_atomic", crash)
    with pytest.raises(KeyboardInterrupt):
        worker(env).run_once()
    monkeypatch.undo()
    with env.owner_engine.begin() as conn:
        assert conn.execute(text("SELECT status FROM upload_jobs")).scalar_one() == "processing"
        conn.execute(text("UPDATE upload_jobs SET heartbeat_at = now() - interval '6 minutes'"))
    w = worker(env)
    assert w.recover_stale() == 1
    assert w.run_once() == 1
    with env.owner_engine.connect() as conn:
        chunks = conn.execute(
            text("SELECT count(*), count(DISTINCT (page, text)) FROM chunks WHERE doc_id = :d"),
            {"d": doc_id},
        ).one()
        pages = conn.execute(
            text("SELECT count(*) FROM pages WHERE doc_id = :d"), {"d": doc_id}
        ).scalar_one()
        live_ids = set(
            conn.execute(
                text(
                    "SELECT c.chunk_id FROM chunks c JOIN documents d USING (doc_id) "
                    "WHERE d.status = 'indexed'"
                )
            ).scalars()
        )
    assert chunks[0] == chunks[1] > 0 and pages == 2
    assert FaissIndex.load(Path(env.settings.faiss_index_path)).ids() == live_ids


def test_cli_and_worker_together_keep_index_consistent(env, tmp_path, embedder) -> None:  # type: ignore[no-untyped-def]
    for i in range(3):
        upload(
            env,
            pdf_bytes(
                tmp_path, f"u{i}.pdf", [f"UPLOAD {i}\nPermit number {i} fee was ${i + 1}00.00."]
            ),
            name=f"u{i}.pdf",
        )
    src = tmp_path / "cli"
    for i in range(3):
        make_pdf(src / f"loan_95000{i}.pdf", [f"CLI FILE {i}\nEscrow line {i} is $9{i}.00."])
    acl = write_acl(
        tmp_path / "cli.yaml",
        {f"loan_95000{i}.pdf": {"groups": ["servicing"], "users": []} for i in range(3)},
    )
    pipe = IngestionPipeline(
        env.app_engine,
        embedder,
        index_path=Path(env.settings.faiss_index_path),
        document_store=None,
        acl_backend=YamlAclBackend(acl),
    )
    errors: list[BaseException] = []

    def run(fn) -> None:  # type: ignore[no-untyped-def]
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    w = worker(env)
    t1 = threading.Thread(target=run, args=(lambda: pipe.run(src),))
    t2 = threading.Thread(target=run, args=(lambda: w.run_once(),))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    while w.run_once():
        pass
    with env.owner_engine.connect() as conn:
        live = set(
            conn.execute(
                text(
                    "SELECT c.chunk_id FROM chunks c JOIN documents d USING (doc_id) "
                    "WHERE d.status = 'indexed' AND d.deleted_at IS NULL"
                )
            ).scalars()
        )
        statuses = list(conn.execute(text("SELECT status FROM upload_jobs")).scalars())
    assert statuses == ["indexed"] * 3
    assert FaissIndex.load(Path(env.settings.faiss_index_path)).ids() == live


def test_path_traversal_filename_is_stored_safely(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = upload(env, zebra_pdf(tmp_path), name="../../etc/passwd.pdf")
    assert r.status_code == 202
    body = r.json()
    assert body["filename"] == "passwd.pdf"
    store = Path(env.settings.document_store_path)
    assert (store / f"{body['doc_id']}.pdf").exists()
    assert not (store / "passwd.pdf").exists() and not (tmp_path / "etc").exists()
    r = upload(
        env, pdf_bytes(tmp_path, "x.pdf", ["X\nOther content."]), name="..\\..\\windows\\evil.pdf"
    )
    assert r.json()["filename"] == "evil.pdf"
    from app.uploads import sanitize_filename

    assert sanitize_filename("..\\..\\windows\\evil\x00\x07name.pdf") == "evilname.pdf"
    assert sanitize_filename("/etc/../" + "a" * 300 + ".pdf") == "a" * 200
    assert sanitize_filename("../..") == "upload.pdf"


def test_admin_events_are_audited(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc_id = upload(env, zebra_pdf(tmp_path)).json()["doc_id"]
    h = env.headers("u_admin")
    assert (
        env.client.put(
            f"/v1/admin/documents/{doc_id}/acl",
            json={"groups": ["servicing"], "users": []},
            headers=h,
        ).status_code
        == 200
    )
    assert env.client.delete(f"/v1/admin/documents/{doc_id}", headers=h).status_code == 200
    with env.owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT event, user_id, doc_id, query_hmac FROM audit_log "
                "WHERE event <> 'query' ORDER BY id"
            )
        ).all()
    assert [(r.event, r.user_id, str(r.doc_id), r.query_hmac) for r in rows] == [
        ("upload", "u_admin", doc_id, None),
        ("acl_change", "u_admin", doc_id, None),
        ("delete", "u_admin", doc_id, None),
    ]


def test_edit_access_requires_valid_access(env) -> None:  # type: ignore[no-untyped-def]
    doc = env.doc_ids["loan_900006.pdf"]
    h = env.headers("u_admin")
    for body in ({"groups": [], "users": []}, {"groups": ["nope"]}, {"users": ["u_ghost"]}):
        r = env.client.put(f"/v1/admin/documents/{doc}/acl", json=body, headers=h)
        assert r.status_code == 422 and r.json() == {"error": "invalid_access"}, body


def test_pickers_list_and_retry(env, tmp_path) -> None:  # type: ignore[no-untyped-def]
    h = env.headers("u_admin")
    assert env.client.get("/v1/admin/groups", headers=h).json() == [
        "compliance",
        "executive",
        "servicing",
        "underwriting",
    ]
    users = env.client.get("/v1/admin/users?q=dan", headers=h).json()
    assert users == [{"user_id": "u_dana", "display": "u_dana"}]
    assert len(env.client.get("/v1/admin/users", headers=h).json()) <= 20

    blank = PdfWriter()
    blank.add_blank_page(width=612, height=792)  # one page, no text: OCR finds nothing
    buf = io.BytesIO()
    blank.write(buf)
    bad = upload(env, buf.getvalue(), name="blank.pdf")
    assert bad.status_code == 202, bad.text
    job_id = bad.json()["job_id"]
    worker(env).run_once()
    job = env.client.get(f"/v1/admin/uploads/{job_id}", headers=h).json()
    assert job["status"] == "failed" and job["failure_reason"] == "no_text_found"
    listing = env.client.get("/v1/admin/documents?status=failed", headers=h).json()
    item = next(i for i in listing["items"] if i["doc_id"] == bad.json()["doc_id"])
    assert item["upload"]["failure_reason"] == "no_text_found"
    assert item["uploaded_by"] == "u_admin" and item["groups"] == ["underwriting"]
    assert "text" not in item and listing["summary"]["failed"] >= 1
    r = env.client.post(f"/v1/admin/uploads/{job_id}/retry", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert env.client.post(f"/v1/admin/uploads/{job_id}/retry", headers=h).status_code == 409
    jobs = env.client.get("/v1/admin/uploads?status=queued", headers=h).json()
    assert [j["job_id"] for j in jobs["items"]] == [job_id]


def test_admin_document_list_paging_and_search(env) -> None:  # type: ignore[no-untyped-def]
    h = env.headers("u_admin")
    page = env.client.get("/v1/admin/documents?limit=3", headers=h).json()
    assert len(page["items"]) == 3 and page["next_cursor"]
    assert page["summary"]["total"] == 7
    rest = env.client.get(
        f"/v1/admin/documents?limit=10&cursor={page['next_cursor']}", headers=h
    ).json()
    names = [i["filename"] for i in page["items"] + rest["items"]]
    assert names == sorted(names) and len(names) == 7
    found = env.client.get("/v1/admin/documents?q=900004", headers=h).json()["items"]
    assert [i["filename"] for i in found] == ["loan_900004.pdf"]
    assert found[0]["groups"] == [] and found[0]["users"] == []  # empty ACL shown as metadata
    assert env.client.get("/v1/admin/documents?q=%25", headers=h).json()["items"] == []


def test_upload_rate_limit_per_admin(env) -> None:  # type: ignore[no-untyped-def]
    codes = [upload(env, b"not a pdf", name=f"x{i}.pdf").status_code for i in range(121)]
    assert codes[:120] == [422] * 120
    assert codes[120] == 429
