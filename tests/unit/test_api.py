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
        # a FEE_VARIANCE/GL_DECOMPOSITION_FAIL-style exception carries its
        # evidence via the match_link that touched its records
        with_evidence = [e for e in body["exceptions"] if e["evidence"] is not None]
        assert with_evidence, "expected at least one exception to carry match_link reconstruction evidence"
        # A refusal has no match_link to attach evidence to — that used to
        # mean it reported none at all. As of migration 003 it stores its
        # own, so what distinguishes it is the absent link, not absent
        # evidence (see test_refusals_carry_their_own_stored_evidence).
        refusals = [e for e in body["exceptions"] if e["code"] == "AMBIGUOUS_SETTLEMENT"]
        assert refusals
        assert all(e["evidence_link_id"] is None for e in refusals)
        assert all(e["evidence"] is not None for e in refusals)
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
        assert kinds <= {"match", "exception", "rejected"}
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


# --- P9 backend additions (ROADMAP step 1) ------------------------------


def test_refusals_carry_their_own_stored_evidence(tmp_path: Path):
    """Migration 003: AMBIGUOUS_SETTLEMENT and UNEXPLAINED_BANK_CREDIT
    propose no match_link by design, so before the evidence column there
    was nothing for the refusal card (UI_SPEC §2.5) to render — the
    candidate subsets hop2 computed died with the process. Seed 42's two
    twins and its one unexplained credit must now come back with the real
    subsets, the bank line, and no evidence_link_id (there is no link)."""
    db_path = _seeded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        run_id = client.get("/api/run/latest").json()["run_id"]
        exceptions = client.get(f"/api/run/{run_id}/exceptions").json()["exceptions"]

        twins = [e for e in exceptions if e["code"] == "AMBIGUOUS_SETTLEMENT"]
        assert len(twins) == 2, "seed 42 refuses exactly the D-02 twin pair"
        for exc in twins:
            ev = exc["evidence"]
            assert ev is not None and ev["tier"] == 2
            assert ev["bank_line"] == exc["records"][0]["id"]
            assert ev["value_date"] == "2026-08-12"
            assert ev["narration"]
            # the two indistinguishable readings — what the card counts
            assert ev["subset_a"] and ev["subset_b"]
            assert ev["subtotal_a_p"] == ev["subtotal_b_p"] == ev["target_p"]
            assert exc["evidence_link_id"] is None, "a refusal proposes no link"
        assert {e["records"][0]["id"] for e in twins} == {"bl_d02_0810a", "bl_d02_0810b"}

        (unexplained,) = [e for e in exceptions if e["code"] == "UNEXPLAINED_BANK_CREDIT"]
        ev = unexplained["evidence"]
        assert ev is not None
        assert ev["bank_line"] == "bl_direct_neft"
        assert ev["value_date"] == "2026-08-04"
        assert "NEFT" in ev["narration"]
        assert ev["reason"], "NoSolution's own reason, not a blank refusal"
        assert unexplained["evidence_link_id"] is None
    finally:
        app.dependency_overrides.clear()


def test_report_endpoint_exposes_stored_evidence_too(tmp_path: Path):
    """/report and /api/run/{id}/exceptions must not disagree about what a
    refusal's evidence is — both read the same stored column."""
    db_path = _seeded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        body = client.get("/report").json()
        twins = [e for e in body["exceptions"] if e["code"] == "AMBIGUOUS_SETTLEMENT"]
        assert len(twins) == 2
        assert all(e["evidence"] and e["evidence"]["subset_a"] for e in twins)
    finally:
        app.dependency_overrides.clear()


def test_match_endpoint_returns_the_full_hop2_reconstruction(tmp_path: Path):
    """UI_SPEC §2.3: the viewer shows one row per gateway payment, the
    reconstructed total and the delta — all computed server-side. Seed
    42's tier-2 batch is setl_0812: four captures plus one refund, netting
    exactly to the bank credit."""
    db_path = _seeded_db(tmp_path)
    conn = sqlite3.connect(db_path)
    run_id = conn.execute("SELECT run_id FROM runs ORDER BY finished_at DESC LIMIT 1").fetchone()[0]
    link_id = conn.execute(
        "SELECT link_id FROM match_link WHERE run_id = ? AND hop = 2 AND tier = 2 AND id_b = 'setl_0812' "
        "ORDER BY link_id LIMIT 1",
        (run_id,),
    ).fetchone()[0]
    conn.close()

    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        body = client.get(f"/api/match/{link_id}").json()
        assert body["bank_line"]["line_id"] == "setl_0812"
        assert body["bank_line"]["credit_p"] == 3_769_736

        rows = body["rows"]
        assert len(rows) == 5, "four captures + one refund"
        assert {r["payment_id"] for r in rows} == {"PAY-0043", "PAY-0044", "PAY-0045", "PAY-0046", "PAY-0059"}
        (refund,) = [r for r in rows if r["kind"] == "refund"]
        assert refund["payment_id"] == "PAY-0059" and refund["net_p"] < 0

        assert sum(r["net_p"] for r in rows) == body["reconstructed_p"]
        assert body["reconstructed_p"] == body["bank_line"]["credit_p"]
        assert body["delta_p"] == 0

        # The running subtotal is the server's, so the viewer can count up
        # as rows stream in without adding anything itself (UI_SPEC §0).
        running = 0
        for r in rows:
            running += r["net_p"]
            assert r["subtotal_p"] == running
        assert rows[-1]["subtotal_p"] == body["reconstructed_p"]

        # This batch needed reconstructing precisely because no row carried
        # a reference — what the viewer's "no UTR recovered · no settlement
        # id" line is asserted from, rather than hardcoded.
        assert all(r["utr"] is None and r["settlement_id"] is None for r in rows)
    finally:
        app.dependency_overrides.clear()


def test_match_endpoint_leaves_non_hop2_links_alone(tmp_path: Path):
    """The reconstruction fields are additive and hop-2 only — a hop-1 link
    must come back exactly as it did before this endpoint grew them."""
    db_path = _seeded_db(tmp_path)
    conn = sqlite3.connect(db_path)
    link_id = conn.execute("SELECT link_id FROM match_link WHERE hop = 1 ORDER BY link_id LIMIT 1").fetchone()[0]
    conn.close()

    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        body = client.get(f"/api/match/{link_id}").json()
        assert body["hop"] == 1
        assert "rows" not in body and "delta_p" not in body
    finally:
        app.dependency_overrides.clear()


def test_clearing_control_returns_a_balancing_t_account(tmp_path: Path):
    """UI_SPEC §2.6: the T-account's closing balance IS the control number.
    Seed 42's residual and exposure are both 13,640,000p (₹1,36,400.00),
    and their difference is subtracted server-side, not in the browser."""
    db_path = _seeded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        body = client.get("/api/control/clearing").json()
        assert body["residual_p"] == body["exposure_p"] == 13_640_000
        assert body["difference_p"] == 0
        assert body["balanced"] is True

        entries = body["entries"]
        assert entries, "PG_RECEIVABLE has journal lines"
        assert all(e["account"] == "PG_RECEIVABLE" for e in entries)
        assert entries[-1]["balance_p"] == body["residual_p"]
        # the running balance really is running, not repeated
        running = 0
        for e in entries:
            running += e["debit_p"] - e["credit_p"]
            assert e["balance_p"] == running
        # ledger order: by date, then voucher
        dates = [e["entry_date"] for e in entries]
        assert dates == sorted(dates)
    finally:
        app.dependency_overrides.clear()


def test_run_latest_returns_the_last_finished_run_and_404s_when_empty(tmp_path: Path):
    db_path = _seeded_db(tmp_path)
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        client = TestClient(app)
        latest = client.get("/api/run/latest")
        assert latest.status_code == 200
        body = latest.json()
        assert body["status"] == "finished"
        assert body["metrics"]["false_match_rate"] == 0.0
        # identical to addressing the same run by id
        assert body == client.get(f"/api/run/{body['run_id']}/metrics").json()
    finally:
        app.dependency_overrides.clear()

    empty_db = tmp_path / "empty.db"
    migrate(db_path=empty_db, migrations_dir=REPO_ROOT / "db" / "migrations")
    app.dependency_overrides[get_db_path] = lambda: empty_db
    try:
        assert TestClient(app).get("/api/run/latest").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_verifier_emits_a_rejected_event_the_stream_carries(tmp_path: Path):
    """A proposal the verifier throws out used to emit nothing at all —
    the one outcome that most needed to be visible. reject_v1 now emits,
    and the SSE stream carries it like any other event."""
    from recon.engine import verifier

    db_path = _seeded_db(tmp_path)
    conn = sqlite3.connect(db_path)
    run_id = "RUN-rejected-event-test"
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES (?, ?, '2026-09-03T00:00:00Z', 'off')",
        (run_id, config.SEED),
    )
    # A hop-1 proposal that cannot survive V1: the capture belongs to a
    # different order than the one claimed.
    order_id, payment_id = conn.execute(
        "SELECT o.order_id, g.payment_id FROM orders o, gw_payments g "
        "WHERE g.kind = 'capture' AND g.order_id != o.order_id LIMIT 1"
    ).fetchone()
    conn.execute(
        "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, run_id) "
        "VALUES ('ML-bogus', 1, 'order', ?, 'gw', ?, 1, 1.0, 'proposed', 'test', ?)",
        (order_id, payment_id, run_id),
    )
    conn.commit()

    events: list[dict] = []
    stats = verifier.run_verifier(conn, run_id, on_event=events.append)
    conn.close()

    assert stats.rejected == 1
    (rejected,) = [e for e in events if e["kind"] == "rejected"]
    assert rejected["link_id"] == "ML-bogus"
    assert rejected["hop"] == 1
    assert rejected["reason"].startswith("V1_failed")


def test_eval_endpoint_runs_the_multi_seed_harness_in_process():
    """The accuracy claim is measured by the UI, not typed into it — so the
    endpoint behind the robustness panel has to actually run the harness.
    Kept to a couple of seeds; `make eval` is the 500-world version."""
    client = TestClient(app)
    body = client.get("/api/eval?count=2&start=42").json()
    assert body["count"] == 2
    assert body["start"] == 42
    assert body["nonzero_false_match_seeds"] == []
    assert body["pipeline_aborted"] == 0
    assert body["completed"] >= 1, "seed 42 always generates"
    assert body["false_match_rate"]["max"] == 0.0
    assert body["link_precision"]["min"] == 1.0
    # counted, not dropped: every attempted seed lands in exactly one bucket
    assert (
        body["completed"]
        + body["generation_failed"]
        + body["loader_quarantined"]
        + body["pipeline_aborted"]
        == body["count"]
    )


def test_eval_endpoint_caps_count():
    """A stray query string must not turn one request into a very long job."""
    body = TestClient(app).get("/api/eval?count=99999&start=1").json()
    assert body["count"] == 500
