"""Smoke test: the full stack answers a question with a valid citation."""


def test_query_answers_with_citation(env) -> None:  # type: ignore[no-untyped-def]
    r = env.query(
        "u_alice", "What is the annual escrow disbursement for volcanic ash roof cleaning?"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "answered", body
    assert "$7,777.77" in body["answer"]
    assert body["citations"][0]["filename"] == "loan_900001.pdf"
    assert body["citations"][0]["page"] == 1
    assert "[1]" in body["answer"]


def test_health(env) -> None:  # type: ignore[no-untyped-def]
    r = env.client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "index_version": 1, "llm": "ok"}
