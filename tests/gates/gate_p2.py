#!/usr/bin/env python3
"""gate_p2.py — Phase P2 gate (SPEC.md G2). Run directly: python tests/gates/gate_p2.py

On seed 42 with defects:
(a) zero false matches vs ground truth
(b) every clean (--no-defects) record matched at tier 1 or 2
(c) all three report forms render
(d) runtime < 10s
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config
from recon.db import migrate
from recon.engine import hop1, hop2, verifier
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all
from recon.report.report import render_html, render_json, render_terminal

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def _build_and_load(tmp_path: Path, defects: bool) -> tuple[sqlite3.Connection, Path]:
    data_dir = tmp_path / f"data_{'defects' if defects else 'clean'}"
    world, truth = generate_world(config.SEED, defects=defects)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = tmp_path / f"recon_{'defects' if defects else 'clean'}.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    report = load_all(conn, data_dir)
    check(report.quarantined == [], f"loader quarantined rows in {'defects' if defects else 'clean'} world: {report.quarantined}")
    return conn, gt_path


def check_zero_false_matches_and_runtime(tmp_path: Path) -> None:
    conn, gt_path = _build_and_load(tmp_path, defects=True)
    conn.close()

    db_path = tmp_path / "recon_defects.db"
    start = time.monotonic()
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
    elapsed = time.monotonic() - start

    m = ctx["metrics"]
    check(m["false_match_rate"] == 0.0, f"(a) expected false_match_rate == 0.0, got {m['false_match_rate']}")
    check(m["link_precision"] == 1.0, f"(a) expected link_precision == 1.0, got {m['link_precision']}")
    check(elapsed < 10.0, f"(d) runtime {elapsed:.2f}s exceeds 10s budget")
    check(m["runtime_s"] < 10.0, f"(d) reported runtime_s {m['runtime_s']:.2f}s exceeds 10s budget")

    return ctx


def check_clean_world_all_matched_tier1_or_2(tmp_path: Path) -> None:
    conn, gt_path = _build_and_load(tmp_path, defects=False)
    run_id = "GATE-P2-CLEAN"
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES (?, ?, datetime('now'), 'off')",
        (run_id, config.SEED),
    )
    conn.commit()
    hop1.run_hop1(conn, run_id)
    hop2.run_hop2(conn, run_id)
    verifier.run_verifier(conn, run_id)

    non_cod_orders = [r[0] for r in conn.execute("SELECT order_id FROM orders WHERE method != 'cod'")]
    for order_id in non_cod_orders:
        row = conn.execute(
            "SELECT tier FROM match_link WHERE run_id = ? AND hop = 1 AND src_a = 'orders' AND id_a = ? AND status = 'accepted'",
            (run_id, order_id),
        ).fetchone()
        check(row is not None, f"(b) order {order_id} has no accepted hop-1 link in the clean world")
        if row is not None:
            check(row[0] in (1, 2), f"(b) order {order_id}'s hop-1 link has tier {row[0]}, expected 1 or 2")

    captures = [r[0] for r in conn.execute("SELECT payment_id FROM gw_payments WHERE kind = 'capture'")]
    for payment_id in captures:
        expected_settle_row = conn.execute(
            "SELECT captured_on FROM gw_payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        row = conn.execute(
            "SELECT tier FROM match_link WHERE run_id = ? AND hop = 2 AND src_a = 'gw' AND id_a = ? AND status = 'accepted'",
            (run_id, payment_id),
        ).fetchone()
        # in-transit captures legitimately have no hop-2 link yet
        from datetime import date

        from recon import busdays

        captured_on = date.fromisoformat(expected_settle_row[0])
        expected_settle = busdays.add_bdays(captured_on, config.SETTLEMENT_LAG_BDAYS)
        if expected_settle > config.DATE_TO:
            continue
        check(row is not None, f"(b) capture {payment_id} has no accepted hop-2 link in the clean world")
        if row is not None:
            check(row[0] in (1, 2), f"(b) capture {payment_id}'s hop-2 link has tier {row[0]}, expected 1 or 2")

    conn.close()


def check_all_three_report_forms_render(ctx: dict) -> None:
    terminal = render_terminal(ctx)
    check("FALSE-MATCH RATE" in terminal, "(c) terminal report must print FALSE-MATCH RATE prominently")
    check(len(terminal) > 0, "(c) terminal report is empty")

    json_str = render_json(ctx)
    parsed = json.loads(json_str)  # must not raise
    check(parsed["metrics"]["false_match_rate"] == ctx["metrics"]["false_match_rate"], "(c) JSON report round-trips false_match_rate")

    html_str = render_html(ctx)
    check("<html" in html_str.lower(), "(c) HTML report must contain an <html> tag")
    check("FALSE-MATCH RATE" in html_str, "(c) HTML report must print FALSE-MATCH RATE prominently")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ctx = check_zero_false_matches_and_runtime(tmp_path)
        check_clean_world_all_matched_tier1_or_2(tmp_path)
        check_all_three_report_forms_render(ctx)

    if FAILURES:
        print(f"GATE G2: FAIL ({len(FAILURES)} issue(s))")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("GATE G2: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
