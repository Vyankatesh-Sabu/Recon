"""test_hop2.py — hop-2 against the seeded defect world (SPEC §5.3 x §6.3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import config
from recon.db import migrate
from recon.engine.hop2 import run_hop2
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "TEST-RUN-HOP2"


def _seeded_db(tmp_path: Path, defects: bool = True) -> sqlite3.Connection:
    data_dir = tmp_path / "data"
    world, _truth = generate_world(config.SEED, defects=defects)
    write_csvs(world, data_dir)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    report = load_all(conn, data_dir)
    assert report.quarantined == []
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES (?, ?, datetime('now'), 'off')",
        (RUN_ID, config.SEED),
    )
    conn.commit()
    return conn


def test_hop2_never_accepts_anything(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    run_hop2(conn, RUN_ID)
    statuses = {row[0] for row in conn.execute("SELECT DISTINCT status FROM match_link")}
    assert statuses <= {"proposed"}


def test_d01_resolves_at_tier_2(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = run_hop2(conn, RUN_ID)
    assert stats.tier2_unique == 1

    rows = conn.execute("SELECT id_a, id_b, tier, confidence, evidence FROM match_link WHERE tier = 2 AND reason = 'tier2_subset_sum_unique'").fetchall()
    assert len(rows) > 0
    line_ids = {r[1] for r in rows}
    assert len(line_ids) == 1  # all rows in the unique solution point at the same bank line
    for _id_a, _id_b, tier, confidence, _evidence in rows:
        assert tier == 2
        assert confidence == 0.98

    # No exception should be raised for this batch — it resolved cleanly.
    codes = {row[0] for row in conn.execute("SELECT code FROM exceptions")}
    line_id = next(iter(line_ids))
    exc_records = conn.execute("SELECT records FROM exceptions").fetchall()
    assert not any(line_id in rec[0] for rec in exc_records)


def test_d02_refuses(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = run_hop2(conn, RUN_ID)
    assert stats.tier2_ambiguous == 2  # two independent ambiguous bank lines (see plan's scope note)

    d02_lines = [row[0] for row in conn.execute("SELECT line_id FROM bank_lines WHERE line_id LIKE 'bl_d02_%'")]
    assert len(d02_lines) == 2
    for line_id in d02_lines:
        n = conn.execute("SELECT COUNT(*) FROM match_link WHERE id_b = ?", (line_id,)).fetchone()[0]
        assert n == 0, f"{line_id} must have zero proposed links — refusal, not a guess"

    exc_rows = conn.execute("SELECT code, records FROM exceptions WHERE code = 'AMBIGUOUS_SETTLEMENT'").fetchall()
    assert len(exc_rows) == 2
    referenced = {json.loads(rec)[0]["id"] for _code, rec in exc_rows}
    assert referenced == set(d02_lines)


def test_cross_bank_line_collision_refuses_both_seed_6(tmp_path: Path):
    """Regression test: found via tests/eval_multi_seed.py (100-seed sweep)
    — seed 6's D-02 places a single-row candidate (its isolated UPI row)
    that, on its own, exactly satisfies BOTH twin bank lines' identical
    credit_p. Each line's OWN subset-sum call independently reports
    "Unique" (neither sees the other's pool), so before this fix hop2
    proposed the SAME payment to two different bank lines and the verifier
    arbitrarily accepted whichever reached the DB first — a genuine false
    match (nonzero false_match_rate), not merely a duplicate-claim retry.
    The fix must refuse for BOTH lines instead of picking a winner."""
    seed = 6
    data_dir = tmp_path / "data"
    world, _truth = generate_world(seed, defects=True)
    write_csvs(world, data_dir)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    report = load_all(conn, data_dir)
    assert report.quarantined == []
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES (?, ?, datetime('now'), 'off')",
        (RUN_ID, seed),
    )
    conn.commit()

    stats = run_hop2(conn, RUN_ID)
    assert stats.tier2_cross_collision >= 1

    d02_lines = [row[0] for row in conn.execute("SELECT line_id FROM bank_lines WHERE line_id LIKE 'bl_d02_%'")]
    assert len(d02_lines) == 2
    for line_id in d02_lines:
        n = conn.execute("SELECT COUNT(*) FROM match_link WHERE id_b = ?", (line_id,)).fetchone()[0]
        assert n == 0, f"{line_id} must have zero proposed links — refusal, not a false match"


def test_d10_fee_variance_detected_link_still_proposed(tmp_path: Path):
    from recon import moneymath

    conn = _seeded_db(tmp_path)
    stats = run_hop2(conn, RUN_ID)
    assert stats.tier1_fee_variance_batches == 1

    exc = conn.execute("SELECT amount_at_risk_p, records FROM exceptions WHERE code = 'FEE_VARIANCE'").fetchone()
    assert exc is not None
    amount_at_risk_p, records = exc
    victim_payment_id = json.loads(records)[1]["id"]  # records[0] is the bank line, records[1] the row
    amount_p, method = conn.execute(
        "SELECT amount_p, method FROM gw_payments WHERE payment_id = ?", (victim_payment_id,)
    ).fetchone()
    assert method == "card"
    correct_fee = moneymath.fee_p(amount_p, config.FEE_BPS["card"])
    wrong_fee = moneymath.fee_p(amount_p, config.D10_WRONG_FEE_BPS)
    assert amount_at_risk_p == wrong_fee - correct_fee

    # the flagged row's link is still proposed (detect only, never auto-resolve)
    n = conn.execute("SELECT COUNT(*) FROM match_link WHERE reason = 'tier1_fee_variance'").fetchone()[0]
    assert n > 0


def test_d07_unexplained_bank_credit(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = run_hop2(conn, RUN_ID)
    assert stats.tier2_unexplained_credit == 1
    row = conn.execute(
        "SELECT amount_at_risk_p FROM exceptions WHERE code = 'UNEXPLAINED_BANK_CREDIT'"
    ).fetchone()
    assert row[0] == config.D07_AMOUNT_P
    n = conn.execute("SELECT COUNT(*) FROM match_link WHERE id_b = 'bl_direct_neft'").fetchone()[0]
    assert n == 0


def test_in_transit_batches_are_informational(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = run_hop2(conn, RUN_ID)
    assert stats.in_transit_batches == 2
    rows = conn.execute("SELECT severity FROM exceptions WHERE code = 'UNSETTLED_IN_TRANSIT'").fetchall()
    assert len(rows) == 2
    assert all(r[0] == "info" for r in rows)


def test_clean_world_all_gateway_batches_tier1_no_failures(tmp_path: Path):
    conn = _seeded_db(tmp_path, defects=False)
    stats = run_hop2(conn, RUN_ID)
    assert stats.tier1_fee_variance_batches == 0
    assert stats.tier2_unique == 0
    assert stats.tier2_ambiguous == 0
    assert stats.missing_in_bank == 0
    assert stats.in_transit_batches == 2
    # bl_direct_neft (the clean world's non-gateway "direct customer NEFT
    # payment", SPEC §5.2.6) has no gateway counterpart at all, so hop2 —
    # which only ever looks at gw_payments/bank_lines — correctly can't
    # explain it and reports it unexplained. It IS legitimately explained,
    # but only at the GL level (hop3, not built yet): its own balanced
    # Dr BANK / Cr SALES voucher. This is expected hop2-in-isolation
    # behavior, not a false positive — a later pipeline would let hop3 run
    # before finalizing this exception.
    assert stats.tier2_unexplained_credit == 1
    row = conn.execute(
        "SELECT records FROM exceptions WHERE code = 'UNEXPLAINED_BANK_CREDIT'"
    ).fetchone()
    assert json.loads(row[0]) == [{"src": "bank", "id": "bl_direct_neft"}]
    non_info_non_neft_exceptions = conn.execute(
        "SELECT COUNT(*) FROM exceptions WHERE severity != 'info' AND code != 'UNEXPLAINED_BANK_CREDIT'"
    ).fetchone()[0]
    assert non_info_non_neft_exceptions == 0
