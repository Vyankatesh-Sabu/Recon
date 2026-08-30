"""test_api.py — POST /ask (SPEC §9), via FastAPI's TestClient (no live server needed)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import config
from recon.api import app, get_db_path, get_llm_client
from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm.client import MockLLM
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]


def _seeded_db(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    world, truth = generate_world(config.SEED, defects=True)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    assert load_all(conn, data_dir).quarantined == []
    conn.close()
    run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
    return db_path


def test_ask_endpoint_returns_answer_tool_calls_and_record_ids(tmp_path: Path):
    db_path = _seeded_db(tmp_path)
    conn = sqlite3.connect(db_path)
    order_id = conn.execute("SELECT order_id FROM orders WHERE method != 'cod' LIMIT 1").fetchone()[0]
    conn.close()

    mock = MockLLM(
        qa_script={
            "trace": [
                {"tool_call": {"name": "trace_order", "input": {"order_id": order_id}}},
                {"answer": f"Traced {order_id}."},
            ]
        }
    )
    app.dependency_overrides[get_llm_client] = lambda: mock
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        response = client.post("/ask", json={"question": f"trace {order_id}"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == f"Traced {order_id}."
        assert body["tool_calls"] == [{"name": "trace_order", "input": {"order_id": order_id}}]
        assert order_id in body["record_ids"]
    finally:
        app.dependency_overrides.clear()


def test_ask_endpoint_validates_request_body():
    client = TestClient(app)
    response = client.post("/ask", json={"not_question": "oops"})
    assert response.status_code == 422


def test_report_endpoint_returns_metrics_and_exceptions_with_evidence(tmp_path: Path):
    db_path = _seeded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        response = client.get("/report")
        assert response.status_code == 200
        body = response.json()
        assert "error" not in body
        assert body["metrics"]["false_match_rate"] == 0.0
        assert len(body["exceptions"]) == body["metrics"]["exceptions"]["open"]
        amounts = [e["amount_at_risk_p"] for e in body["exceptions"]]
        assert amounts == sorted(amounts, reverse=True)
        # a tier-2-resolved exception has no reconstruction evidence to attach at all... but
        # a FEE_VARIANCE/GL_DECOMPOSITION_FAIL-style exception should carry one via its match_link
        with_evidence = [e for e in body["exceptions"] if e["evidence"] is not None]
        assert with_evidence, "expected at least one exception to carry match_link reconstruction evidence"
        without_evidence_codes = {e["code"] for e in body["exceptions"] if e["evidence"] is None}
        assert "AMBIGUOUS_SETTLEMENT" in without_evidence_codes  # a genuine refusal — nothing to attach
    finally:
        app.dependency_overrides.clear()


def test_report_endpoint_no_run_yet(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    from recon.db import migrate as _migrate

    _migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        response = client.get("/report")
        assert response.status_code == 200
        assert "error" in response.json()
    finally:
        app.dependency_overrides.clear()


def test_dashboard_is_served_at_slash_dashboard():
    client = TestClient(app)
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "RECON-4" in response.text
    assert "/report" in response.text
    assert "/ask" in response.text
