"""test_api.py — POST /ask (SPEC §9) and the P6 run/streaming/reconstruction
API (supplement §3), via FastAPI's TestClient (no live server needed)."""

from __future__ import annotations

import json
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


# --- P6 supplement: run/streaming/reconstruction API ----------------------


def _preloaded_db(tmp_path: Path) -> Path:
    """A migrated, loaded-but-not-yet-run DB — what POST /api/run expects to
    find (generate + load already done; the run itself is this endpoint's job)."""
    data_dir = tmp_path / "data"
    world, truth = generate_world(config.SEED, defects=True)
    write_csvs(world, data_dir)
    write_ground_truth(truth, data_dir / "ground_truth.json")
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    assert load_all(conn, data_dir).quarantined == []
    conn.close()
    return db_path


def test_api_run_and_stream_produce_a_gate_p2_equivalent_result(tmp_path: Path):
    """POST /api/run against real seeded data, drain its SSE stream to
    completion, then confirm the run's own on-disk state (metrics, match
    counts) is exactly what the LLM-off pipeline always produces (gate_p2's
    own numbers) — the API layer must not change what the pipeline does."""
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        response = client.post("/api/run", json={"llm_mode": "off"})
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        assert run_id

        stream = client.get(f"/api/run/{run_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[len("data: ") :]) for line in stream.text.splitlines() if line.startswith("data: ")]
        assert events, "expected at least one streamed event"
        kinds = {e["kind"] for e in events}
        assert kinds <= {"match", "exception"}
        assert all(e["run_id"] == run_id for e in events)
        seqs = [e["seq"] for e in events]
        assert seqs == list(range(1, len(events) + 1)), "seq must be contiguous and in emission order"

        # a second GET on the same (now-consumed) stream is a 404, not a hang
        # or a silently-empty second copy of the same events.
        second = client.get(f"/api/run/{run_id}/stream")
        assert second.status_code == 404

        metrics_resp = client.get(f"/api/run/{run_id}/metrics")
        assert metrics_resp.status_code == 200
        m = metrics_resp.json()
        assert m["status"] == "finished"
        assert m["metrics"]["false_match_rate"] == 0.0

        match_events = [e for e in events if e["kind"] == "match"]
        exc_events = [e for e in events if e["kind"] == "exception"]
        assert len(match_events) + len(exc_events) == len(events)
    finally:
        app.dependency_overrides.clear()


def test_api_run_missing_run_id_metrics_and_stream_404(tmp_path: Path):
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        assert client.get("/api/run/NO-SUCH-RUN/metrics").status_code == 404
        assert client.get("/api/run/NO-SUCH-RUN/stream").status_code == 404
        assert client.get("/api/run/NO-SUCH-RUN/exceptions").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_api_run_twice_against_same_db_does_not_spuriously_reject(tmp_path: Path):
    """Regression test for the V2 index scoping bug found while building
    this endpoint: a second POST /api/run against the same already-loaded
    DB must reconcile identically to the first, not mass-reject its own
    proposals as DUPLICATE_CLAIM against the first run's accepted links."""
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_ids = []
        for _ in range(2):
            run_id = client.post("/api/run", json={"llm_mode": "off"}).json()["run_id"]
            client.get(f"/api/run/{run_id}/stream")  # drain to completion
            run_ids.append(run_id)

        for run_id in run_ids:
            m = client.get(f"/api/run/{run_id}/metrics").json()["metrics"]
            assert m["false_match_rate"] == 0.0
            exceptions = client.get(f"/api/run/{run_id}/exceptions").json()["exceptions"]
            codes = {e["code"] for e in exceptions}
            assert "DUPLICATE_CLAIM" not in codes, f"{run_id} spuriously rejected its own proposals"
    finally:
        app.dependency_overrides.clear()


def test_api_run_exceptions_filterable_by_hop_code_severity(tmp_path: Path):
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_id = client.post("/api/run", json={"llm_mode": "off"}).json()["run_id"]
        client.get(f"/api/run/{run_id}/stream")

        all_exc = client.get(f"/api/run/{run_id}/exceptions").json()["exceptions"]
        assert all_exc

        by_hop = client.get(f"/api/run/{run_id}/exceptions", params={"hop": 1}).json()["exceptions"]
        assert by_hop and all(e["hop"] == 1 for e in by_hop)

        one_code = all_exc[0]["code"]
        by_code = client.get(f"/api/run/{run_id}/exceptions", params={"code": one_code}).json()["exceptions"]
        assert by_code and all(e["code"] == one_code for e in by_code)

        by_severity = client.get(f"/api/run/{run_id}/exceptions", params={"severity": "critical"}).json()["exceptions"]
        assert all(e["severity"] == "critical" for e in by_severity)
    finally:
        app.dependency_overrides.clear()


def test_api_match_link_lookup(tmp_path: Path):
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_id = client.post("/api/run", json={"llm_mode": "off"}).json()["run_id"]
        client.get(f"/api/run/{run_id}/stream")

        conn = sqlite3.connect(db_path)
        link_id = conn.execute(
            "SELECT link_id FROM match_link WHERE run_id = ? AND status = 'accepted' LIMIT 1", (run_id,)
        ).fetchone()[0]
        conn.close()

        response = client.get(f"/api/match/{link_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["link_id"] == link_id
        assert body["status"] == "accepted"
        assert body["evidence"] is not None

        assert client.get("/api/match/NO-SUCH-LINK").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_api_order_chain_matches_trace_order_tool(tmp_path: Path):
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_id = client.post("/api/run", json={"llm_mode": "off"}).json()["run_id"]
        client.get(f"/api/run/{run_id}/stream")

        conn = sqlite3.connect(db_path)
        order_id = conn.execute("SELECT order_id FROM orders WHERE method != 'cod' LIMIT 1").fetchone()[0]
        from recon.llm.tools import trace_order

        expected = trace_order(conn, order_id, run_id=run_id)
        conn.close()

        response = client.get(f"/api/order/{order_id}/chain", params={"run_id": run_id})
        assert response.status_code == 200
        assert response.json() == expected

        assert client.get("/api/order/NO-SUCH-ORDER/chain").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_api_control_clearing_matches_verifier_v5(tmp_path: Path):
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_id = client.post("/api/run", json={"llm_mode": "off"}).json()["run_id"]
        client.get(f"/api/run/{run_id}/stream")

        conn = sqlite3.connect(db_path)
        from recon.engine import verifier as verifier_module

        expected_residual = verifier_module.compute_residual_p(conn)
        expected_exposure, expected_breakdown = verifier_module.compute_exposure_p(conn, run_id)
        conn.close()

        response = client.get("/api/control/clearing", params={"run_id": run_id})
        assert response.status_code == 200
        body = response.json()
        assert body["residual_p"] == expected_residual
        assert body["exposure_p"] == expected_exposure
        assert body["breakdown"] == expected_breakdown
        assert body["balanced"] is True
    finally:
        app.dependency_overrides.clear()


def test_api_ask_endpoint_targets_a_specific_run_id(tmp_path: Path):
    db_path = _preloaded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_id = client.post("/api/run", json={"llm_mode": "off"}).json()["run_id"]
        client.get(f"/api/run/{run_id}/stream")

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
        response = client.post("/api/ask", json={"question": f"trace {order_id}", "run_id": run_id})
        assert response.status_code == 200
        assert response.json()["answer"] == f"Traced {order_id}."
    finally:
        app.dependency_overrides.clear()
