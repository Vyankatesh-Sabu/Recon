"""test_tools.py — the four Q&A tools against a real seeded+piped run (SPEC §9.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config
from recon.db import migrate
from recon.engine import verifier
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm import tools
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_seeded_pipeline(tmp_path: Path) -> tuple[sqlite3.Connection, str]:
    data_dir = tmp_path / "data"
    world, truth = generate_world(config.SEED, defects=True)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    report = load_all(conn, data_dir)
    assert report.quarantined == []
    conn.close()

    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
    conn = sqlite3.connect(db_path)
    return conn, ctx["run_id"]


def test_trace_order_full_chain(tmp_path: Path):
    conn, run_id = _run_seeded_pipeline(tmp_path)
    order_id = conn.execute(
        "SELECT order_id FROM orders WHERE method != 'cod' ORDER BY order_id LIMIT 1"
    ).fetchone()[0]
    result = tools.trace_order(conn, order_id, run_id=run_id)
    assert result["order"]["order_id"] == order_id
    assert result["capture"] is not None
    assert result["hops"]["h1"] == "accepted"
    # a normal, non-defect order should fully chain through hop3 too
    if result["hops"]["h2"] == "accepted":
        assert result["gl"]["vouchers"]


def test_trace_order_unknown_order(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    result = tools.trace_order(conn, "ORD-9999")
    assert "error" in result


def test_explain_settlement_by_utr_and_line_id(tmp_path: Path):
    conn, run_id = _run_seeded_pipeline(tmp_path)
    line_id, utr = conn.execute(
        "SELECT line_id, utr_extracted FROM bank_lines WHERE utr_extracted LIKE 'UTIB0%' LIMIT 1"
    ).fetchone()
    by_line = tools.explain_settlement(conn, line_id, run_id=run_id)
    by_utr = tools.explain_settlement(conn, utr, run_id=run_id)
    assert by_line["bank_line"]["line_id"] == line_id
    assert by_utr["bank_line"]["line_id"] == line_id
    assert by_line["rows"]
    assert by_line["delta_p"] == 0 or abs(by_line["delta_p"]) <= config.AMOUNT_TOL_P


def test_explain_settlement_ambiguous_reports_unresolved_reason(tmp_path: Path):
    conn, run_id = _run_seeded_pipeline(tmp_path)
    d02_line = conn.execute("SELECT line_id FROM bank_lines WHERE line_id LIKE 'bl_d02_%' LIMIT 1").fetchone()[0]
    result = tools.explain_settlement(conn, d02_line, run_id=run_id)
    assert result["rows"] == []
    assert result["unresolved_reason"] is not None
    assert result["unresolved_reason"]["code"] == "AMBIGUOUS_SETTLEMENT"


def test_list_exceptions_filters_and_orders(tmp_path: Path):
    conn, run_id = _run_seeded_pipeline(tmp_path)
    all_open = tools.list_exceptions(conn, run_id=run_id)
    amounts = [e["amount_at_risk_p"] for e in all_open]
    assert amounts == sorted(amounts, reverse=True)

    only_ambiguous = tools.list_exceptions(conn, code="AMBIGUOUS_SETTLEMENT", run_id=run_id)
    assert len(only_ambiguous) == 2
    assert all(e["code"] == "AMBIGUOUS_SETTLEMENT" for e in only_ambiguous)

    big_only = tools.list_exceptions(conn, min_amount_p=10_000_00, run_id=run_id)
    assert all(e["amount_at_risk_p"] >= 10_000_00 for e in big_only)
    assert len(big_only) < len(all_open)


def test_cash_position_unreconciled_matches_v5_exposure_exactly(tmp_path: Path):
    """The load-bearing assertion: cash_position's number, V5's exposure_p,
    and V5's residual_p (they're equal by the pipeline having succeeded at
    all) all agree — three surfaces, one number."""
    conn, run_id = _run_seeded_pipeline(tmp_path)
    position = tools.cash_position(conn, as_of=config.DATE_TO.isoformat(), run_id=run_id)

    exposure_p, _breakdown = verifier.compute_exposure_p(conn, run_id)
    residual_p = verifier.compute_residual_p(conn)

    assert position["unreconciled_p"] == exposure_p
    assert position["unreconciled_p"] == residual_p


def test_cash_position_in_transit_matches_unsettled_batches(tmp_path: Path):
    conn, run_id = _run_seeded_pipeline(tmp_path)
    position = tools.cash_position(conn, as_of=config.DATE_TO.isoformat(), run_id=run_id)
    assert len(position["in_transit"]) == 2
    for entry in position["in_transit"]:
        assert entry["expected_date"] > config.DATE_TO.isoformat()


def test_collect_record_ids():
    obj = {
        "order": {"order_id": "ORD-1001", "customer": "CUST-001"},
        "rows": [{"payment_id": "PAY-0001"}, {"payment_id": "PAY-0002"}],
        "unrelated": "not-an-id-field",
    }
    ids = tools.collect_record_ids(obj)
    assert ids == {"ORD-1001", "CUST-001", "PAY-0001", "PAY-0002"}
