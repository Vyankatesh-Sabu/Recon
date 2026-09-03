#!/usr/bin/env python3
"""gate_p6.py — Phase P6 gate (P6 UI supplement §4: "the screen renders
correctly from real API data with the LLM off. No mock data in the frontend
at any point"). P6 itself is API-layer only (no frontend yet) — this gate
exercises every new /api/* endpoint against a real seeded pipeline run,
through FastAPI's TestClient (no live server needed), verifying each
response against direct SQL / the same functions the endpoint wraps —
never against a hand-written expected value.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config
from fastapi.testclient import TestClient
from recon.api import app, get_db_path, get_llm_client
from recon.db import migrate
from recon.engine import verifier
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm.client import MockLLM
from recon.llm.tools import trace_order
from recon.loader import load_all

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def drain(client: TestClient, run_id: str) -> list[dict]:
    stream = client.get(f"/api/run/{run_id}/stream")
    check(stream.status_code == 200, f"stream {run_id}: expected 200, got {stream.status_code}")
    check(
        stream.headers.get("content-type", "").startswith("text/event-stream"),
        f"stream {run_id}: expected text/event-stream, got {stream.headers.get('content-type')}",
    )
    return [json.loads(line[len("data: ") :]) for line in stream.text.splitlines() if line.startswith("data: ")]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        world, truth = generate_world(config.SEED, defects=True)
        write_csvs(world, data_dir)
        gt_path = data_dir / "ground_truth.json"
        write_ground_truth(truth, gt_path)
        db_path = tmp_path / "recon.db"
        migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
        conn = sqlite3.connect(db_path)
        report = load_all(conn, data_dir)
        check(report.quarantined == [], f"loader quarantined rows: {report.quarantined}")
        conn.close()

        app.dependency_overrides[get_db_path] = lambda: db_path
        client = TestClient(app)

        # --- run twice: exercises POST /api/run, GET .../stream (SSE event
        # order + shape), and is the direct regression check for the V2
        # index-scoping bug found while building this gate (002_v2_scope_
        # by_run.sql) — a second run against the same loaded data must not
        # spuriously reject its own proposals as DUPLICATE_CLAIM. ---
        run_ids = []
        all_events: dict[str, list[dict]] = {}
        for _ in range(2):
            resp = client.post("/api/run", json={"llm_mode": "off"})
            check(resp.status_code == 200, f"POST /api/run: expected 200, got {resp.status_code}")
            run_id = resp.json()["run_id"]
            events = drain(client, run_id)
            check(bool(events), f"{run_id}: expected at least one streamed event")
            check(
                {e["kind"] for e in events} <= {"match", "exception", "rejected"},
                f"{run_id}: unexpected event kind(s) {({e['kind'] for e in events})}",
            )
            check(
                [e["seq"] for e in events] == list(range(1, len(events) + 1)),
                f"{run_id}: seq must be contiguous starting at 1",
            )
            check(all(e["run_id"] == run_id for e in events), f"{run_id}: every event must carry this run_id")
            run_ids.append(run_id)
            all_events[run_id] = events

        for run_id in run_ids:
            conn = sqlite3.connect(db_path)
            open_exc = conn.execute(
                "SELECT COUNT(*) FROM exceptions WHERE run_id = ? AND status = 'open'", (run_id,)
            ).fetchone()[0]
            dup_claims = conn.execute(
                "SELECT COUNT(*) FROM exceptions WHERE run_id = ? AND code = 'DUPLICATE_CLAIM'", (run_id,)
            ).fetchone()[0]
            conn.close()
            check(dup_claims == 0, f"{run_id}: {dup_claims} spurious DUPLICATE_CLAIM(s) — V2 index scoping regressed")
            check(open_exc == 17, f"{run_id}: expected 17 open exceptions (gate_p2's own number), got {open_exc}")

        run_id = run_ids[-1]

        # --- GET /api/run/{id}/metrics: matches the runs table directly ---
        metrics_resp = client.get(f"/api/run/{run_id}/metrics")
        check(metrics_resp.status_code == 200, f"GET metrics: expected 200, got {metrics_resp.status_code}")
        m = metrics_resp.json()
        check(m["status"] == "finished", f"GET metrics: expected status=finished, got {m['status']}")
        check(m["metrics"]["false_match_rate"] == 0.0, f"GET metrics: expected 0.0 false-match rate, got {m['metrics']['false_match_rate']}")
        check(client.get("/api/run/NO-SUCH-RUN/metrics").status_code == 404, "GET metrics: unknown run_id must 404")

        # --- GET /api/run/{id}/exceptions: filterable, matches direct SQL ---
        exc_resp = client.get(f"/api/run/{run_id}/exceptions", params={"severity": "critical"})
        check(exc_resp.status_code == 200, f"GET exceptions: expected 200, got {exc_resp.status_code}")
        critical_from_api = {e["exc_id"] for e in exc_resp.json()["exceptions"]}
        conn = sqlite3.connect(db_path)
        critical_from_sql = {
            r[0]
            for r in conn.execute(
                "SELECT exc_id FROM exceptions WHERE run_id = ? AND status = 'open' AND severity = 'critical'",
                (run_id,),
            )
        }
        conn.close()
        check(
            critical_from_api == critical_from_sql,
            f"GET exceptions?severity=critical: API set {critical_from_api} != SQL set {critical_from_sql}",
        )

        # --- GET /api/match/{link_id}: an accepted link, evidence parsed ---
        conn = sqlite3.connect(db_path)
        link_row = conn.execute(
            "SELECT link_id FROM match_link WHERE run_id = ? AND status = 'accepted' AND evidence IS NOT NULL LIMIT 1",
            (run_id,),
        ).fetchone()
        conn.close()
        check(link_row is not None, "expected at least one accepted match_link with evidence")
        if link_row is not None:
            match_resp = client.get(f"/api/match/{link_row[0]}")
            check(match_resp.status_code == 200, f"GET match: expected 200, got {match_resp.status_code}")
            check(match_resp.json()["evidence"] is not None, "GET match: evidence should be parsed, not null")
        check(client.get("/api/match/NO-SUCH-LINK").status_code == 404, "GET match: unknown link_id must 404")

        # --- GET /api/order/{id}/chain: matches trace_order exactly ---
        conn = sqlite3.connect(db_path)
        order_id = conn.execute("SELECT order_id FROM orders WHERE method != 'cod' LIMIT 1").fetchone()[0]
        expected_chain = trace_order(conn, order_id, run_id=run_id)
        conn.close()
        chain_resp = client.get(f"/api/order/{order_id}/chain", params={"run_id": run_id})
        check(chain_resp.status_code == 200, f"GET chain: expected 200, got {chain_resp.status_code}")
        check(chain_resp.json() == expected_chain, "GET chain: API response != trace_order(conn, order_id) directly")
        check(client.get("/api/order/NO-SUCH-ORDER/chain").status_code == 404, "GET chain: unknown order_id must 404")

        # --- GET /api/control/clearing: matches verifier's V5 computation exactly ---
        conn = sqlite3.connect(db_path)
        expected_residual = verifier.compute_residual_p(conn)
        expected_exposure, expected_breakdown = verifier.compute_exposure_p(conn, run_id)
        conn.close()
        clearing_resp = client.get("/api/control/clearing", params={"run_id": run_id})
        check(clearing_resp.status_code == 200, f"GET clearing: expected 200, got {clearing_resp.status_code}")
        cb = clearing_resp.json()
        check(cb["residual_p"] == expected_residual, f"GET clearing: residual_p {cb['residual_p']} != {expected_residual}")
        check(cb["exposure_p"] == expected_exposure, f"GET clearing: exposure_p {cb['exposure_p']} != {expected_exposure}")
        check(cb["breakdown"] == expected_breakdown, "GET clearing: breakdown mismatch")
        check(cb["balanced"] is True, "GET clearing: expected balanced=true on this seeded run")

        # --- POST /api/ask: same grounded loop as /ask, targeted at a
        # specific run_id (not just "latest") — reuses gate_p5's Q2 exactly,
        # scoped to run_ids[0] (not the latest run) to prove the run_id
        # parameter actually threads through, not just defaults to latest. ---
        conn = sqlite3.connect(db_path)
        traced_order = conn.execute(
            "SELECT order_id FROM orders WHERE method != 'cod' LIMIT 1"
        ).fetchone()[0]
        conn.close()
        mock = MockLLM(
            qa_script={
                "trace": [
                    {"tool_call": {"name": "trace_order", "input": {"order_id": traced_order}}},
                    {"answer": f"Traced {traced_order}."},
                ]
            }
        )
        app.dependency_overrides[get_llm_client] = lambda: mock
        ask_resp = client.post("/api/ask", json={"question": f"trace {traced_order}", "run_id": run_ids[0]})
        check(ask_resp.status_code == 200, f"POST /api/ask: expected 200, got {ask_resp.status_code}")
        check(ask_resp.json()["answer"] == f"Traced {traced_order}.", "POST /api/ask: unexpected answer")
        del app.dependency_overrides[get_llm_client]

        app.dependency_overrides.clear()

    if FAILURES:
        print(f"GATE G6: FAIL ({len(FAILURES)} issue(s))")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"GATE G6: PASS ({len(run_ids)} runs, {sum(len(e) for e in all_events.values())} streamed events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
