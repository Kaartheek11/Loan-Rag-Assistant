"""§12.2 retrieval and API functional tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.main import StartupError, create_app
from app.ingestion.acl_source import YamlAclBackend
from app.ingestion.pipeline import IngestionPipeline
from app.retrieval.fusion import rrf
from app.retrieval.keyword_search import fulltext_query, keyword_search
from app.retrieval.retriever import Retriever
from app.security.identity import Identity
from tests.env import build_env, make_settings
from tests.helpers import make_pdf, write_acl


def test_rrf_is_deterministic_with_tie_breaks() -> None:
    a = [5, 3, 9]
    b = [3, 7, 5]
    fused = rrf([a, b])
    assert [c for c, _ in fused] == [3, 5, 7, 9]
    # 7 (rank 2 in b) and 9 (rank 3 in a) differ; make an exact tie: 1/(60+1) each.
    tie = rrf([[42], [17]])
    assert [c for c, _ in tie] == [17, 42]  # equal score -> lower chunk_id first
    assert rrf([a, b]) == rrf([a, b])
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_fulltext_query_uses_or_and_drops_stopwords() -> None:
    q = fulltext_query("What is the interest rate on loan LN-2024-004512?")
    assert q == "interest or rate or loan or ln-2024-004512"


PAYMENT_DOCS = {
    f"loan_95{i:04d}.pdf": (
        [
            f"PAYMENT STATUS - FILE {i}\nThe payment status report shows the "
            f"monthly payment status is current and the payment was received."
        ],
        ["servicing"],
        [],
    )
    for i in range(12)
}
PAYMENT_DOCS["loan_959999.pdf"] = (
    [
        "MIGRATION NOTES\nZebra herds migrate across the savanna in late summer following rain. "
        "Reference LN-2024-959999 appears in the archive index."
    ],
    ["servicing"],
    [],
)
PAYMENT_DOCS["loan_958888.pdf"] = (
    ["COMPLIANCE ONLY\nA compliance memo about payment status exceptions."],
    ["compliance"],
    [],
)


@pytest.fixture
def payment_env(tmp_path, pg_urls, app_engine, owner_engine, embedder, keypair):  # type: ignore[no-untyped-def]
    with build_env(
        tmp=tmp_path,
        pg_urls=pg_urls,
        app_engine=app_engine,
        owner_engine=owner_engine,
        embedder=embedder,
        keypair=keypair,
        docs=PAYMENT_DOCS,
    ) as e:
        yield e


def test_identifier_search_finds_loan_number_vector_ranks_low(payment_env, embedder) -> None:  # type: ignore[no-untyped-def]
    env = payment_env
    target = env.chunk_ids["loan_959999.pdf"]
    question = "What is the payment status for LN-2024-959999?"
    vec = env.app.state.index.search(embedder.embed_query(question), 50)
    vector_rank = next(i for i, c in enumerate(vec, 1) if c in target)
    assert vector_rank > 6  # vector search alone would miss it at top_k=6
    with env.app_engine.connect() as conn:
        kw = keyword_search(conn, question, 50)
    assert kw[0] in target  # identifier match ranks first
    env.query("u_bob", question, top_k=6)
    assert set(env.audit_rows()[-1].returned_chunk_ids) & target  # hybrid recovers it


def test_adaptive_loop_stops_at_n_max_with_fewer_than_top_k(payment_env, embedder) -> None:  # type: ignore[no-untyped-def]
    env = payment_env
    retriever = Retriever(env.app.state.index, n_start=2, n_max=8)
    carol = Identity("u_carol", frozenset({"compliance"}), frozenset())
    question = "payment status report"  # matches all 14 docs; carol can see one
    with env.app_engine.connect() as conn:
        result = retriever.retrieve(
            conn,
            carol,
            question,
            embedder.embed_query(question),
            top_k=6,
            request_id="00000000-0000-0000-0000-000000000001",
        )
    assert result.rounds == 3 and result.final_n == 8  # 2 -> 4 -> 8, then stop at N_MAX
    returned = {c.filename for c in result.permitted.chunks}
    assert returned <= {"loan_958888.pdf"}
    assert len(result.permitted.chunks) < 6


def test_adaptive_loop_grows_until_enough_visible(payment_env, embedder) -> None:  # type: ignore[no-untyped-def]
    env = payment_env
    retriever = Retriever(env.app.state.index, n_start=1, n_max=800)
    bob = Identity("u_bob", frozenset({"servicing"}), frozenset())
    q = "payment status"
    with env.app_engine.connect() as conn:
        result = retriever.retrieve(
            conn,
            bob,
            q,
            embedder.embed_query(q),
            top_k=6,
            request_id="00000000-0000-0000-0000-000000000002",
        )
    assert len(result.permitted.chunks) == 6
    assert result.rounds >= 3  # 1, 2, 4, 8 ...


def test_api_reloads_index_after_version_change(env, embedder, tmp_path) -> None:  # type: ignore[no-untyped-def]
    holder = env.app.state.index
    assert holder.version == 1
    src = tmp_path / "more"
    make_pdf(
        src / "loan_970001.pdf",
        [
            "NEW FILE - LOAN LN-2024-970001\nThe borrower keeps a "
            "flock of alpacas that produce wool income."
        ],
    )
    acl = write_acl(
        tmp_path / "more_acl.yaml", {"loan_970001.pdf": {"groups": ["underwriting"], "users": []}}
    )
    IngestionPipeline(
        env.app_engine,
        embedder,
        index_path=Path(env.settings.faiss_index_path),
        document_store=None,
        acl_backend=YamlAclBackend(acl),
    ).run(src)
    r = env.query("u_alice", "How does the borrower earn alpaca wool income?")
    assert holder.version == 2
    assert {c["filename"] for c in r.json()["citations"]} == {"loan_970001.pdf"}


def test_startup_fails_on_embedding_model_mismatch(
    env,
    tmp_path,
    keypair,
    pg_urls,  # type: ignore[no-untyped-def]
    app_engine,
    embedder,
) -> None:
    with env.owner_engine.begin() as conn:
        conn.execute(text("UPDATE index_state SET embedding_model = 'BAAI/bge-base-en-v1.5'"))
    settings = make_settings(tmp_path, pg_urls["app"], keypair[1])
    app = create_app(settings, engine=app_engine, embedder=embedder, configure_logs=False)
    with pytest.raises(StartupError, match="rebuild-index"), TestClient(app):
        pass


def test_startup_refuses_unsafe_production_config(
    tmp_path,
    keypair,
    pg_urls,
    app_engine,  # type: ignore[no-untyped-def]
    embedder,
    clean_db,
) -> None:
    bad = make_settings(tmp_path, pg_urls["app"], keypair[1], environment="production")
    app = create_app(bad, engine=app_engine, embedder=embedder, configure_logs=False)
    with pytest.raises(StartupError), TestClient(app):
        pass


def test_admin_lists_failed_documents_without_text(env, tmp_path, embedder) -> None:  # type: ignore[no-untyped-def]
    src = tmp_path / "broken"
    src.mkdir()
    (src / "loan_bad.pdf").write_bytes(b"%PDF-1.4 garbage")
    acl = write_acl(tmp_path / "b.yaml", {})
    IngestionPipeline(
        env.app_engine,
        embedder,
        index_path=Path(env.settings.faiss_index_path),
        document_store=None,
        acl_backend=YamlAclBackend(acl),
    ).run(src)
    r = env.client.get("/v1/admin/documents?status=failed", headers=env.headers("u_admin"))
    assert r.status_code == 200
    rows = r.json()["items"]
    assert [row["filename"] for row in rows] == ["loan_bad.pdf"]
    assert rows[0]["failure_reason"].startswith("extraction_failed")
    assert (
        env.client.get(
            "/v1/admin/documents?status=bogus", headers=env.headers("u_admin")
        ).status_code
        == 422
    )
