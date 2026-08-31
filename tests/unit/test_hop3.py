"""test_hop3.py — hop-3 against the seeded defect world (SPEC §5.3 x §6.4)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import config
from recon.db import migrate
from recon.engine import hop1, hop2, hop3, verifier
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "TEST-RUN-HOP3"


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


def _run_hops(conn: sqlite3.Connection):
    hop1.run_hop1(conn, RUN_ID)
    hop2.run_hop2(conn, RUN_ID)
    return hop3.run_hop3(conn, RUN_ID)


def test_hop3_never_accepts_anything(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    _run_hops(conn)
    statuses = {row[0] for row in conn.execute("SELECT DISTINCT status FROM match_link")}
    assert statuses <= {"proposed"}


def test_d04_gl_decomposition_fail(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = _run_hops(conn)
    assert stats.decomposition_fail == 1
    exc = conn.execute(
        "SELECT explanation FROM exceptions WHERE code = 'GL_DECOMPOSITION_FAIL'"
    ).fetchone()
    assert exc is not None
    assert "input tax credit" in exc[0]
    # the pairing (bank<->voucher) is still proposed — detect, don't refuse
    n = conn.execute("SELECT COUNT(*) FROM match_link WHERE hop = 3 AND status = 'proposed'").fetchone()[0]
    assert n > 0


def test_d05_gl_missing(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = _run_hops(conn)
    assert stats.gl_missing == 1
    exc = conn.execute("SELECT severity, records FROM exceptions WHERE code = 'GL_MISSING'").fetchone()
    assert exc[0] == "critical"
    line_id = json.loads(exc[1])[0]["id"]
    n = conn.execute("SELECT COUNT(*) FROM match_link WHERE hop = 3 AND id_a = ?", (line_id,)).fetchone()[0]
    assert n == 0  # nothing to propose — no journal exists


def test_d13_gl_duplicate(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = _run_hops(conn)
    assert stats.gl_duplicate == 1
    exc = conn.execute(
        "SELECT severity, records FROM exceptions WHERE code = 'GL_DUPLICATE'"
    ).fetchone()
    assert exc[0] == "warn"
    dup_voucher = json.loads(exc[1])[0]["id"]
    assert dup_voucher.endswith("-DUP")
    # the ORIGINAL (non-dup) voucher is the one proposed, not the duplicate
    n = conn.execute(
        "SELECT COUNT(*) FROM match_link WHERE hop = 3 AND id_b = ?", (dup_voucher,)
    ).fetchone()[0]
    assert n == 0


def test_gl_ambiguous_match_refuses_not_guesses(tmp_path: Path):
    """Regression test for a bug found via tests/eval_multi_seed.py: two
    genuinely distinct GL vouchers (different FEE_EXPENSE/INPUT_GST/
    PG_RECEIVABLE underneath — not a real duplicate) that coincidentally
    share a BANK debit amount and date, exactly what D-02's twin
    settlements produce. Before this fix hop3's amount+date voucher lookup
    picked one arbitrarily and mislabeled the other a GL_DUPLICATE it
    never was — doing so once per bank line that queried it, corrupting V5
    (verifier.ClearingControlFailure on ~7% of random seeds).

    Built directly against the schema (bypassing hop1/hop2/the generator)
    so this exercises hop3's signature-comparison logic in isolation,
    independent of whatever hop2.py's own cross-bank-line collision guard
    (test_hop2.py's test_cross_bank_line_collision_refuses_both_seed_6)
    does or doesn't happen to filter out first for a given seed."""
    data_dir = tmp_path / "data"
    world, _truth = generate_world(config.SEED, defects=False)  # just need a schema-valid base world
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

    # Two bank lines, identical credit + value_date (same shape as D-02).
    for line_id in ("BL-TEST-X", "BL-TEST-Y"):
        conn.execute(
            "INSERT INTO bank_lines (line_id, value_date, narration, credit_p, debit_p, utr_extracted) "
            "VALUES (?, '2026-08-11', 'test settlement', 100000, 0, NULL)",
            (line_id,),
        )
    # Two vouchers with the SAME BANK debit and date, but DIFFERENT other
    # lines underneath — genuinely distinct, not a byte-for-byte duplicate.
    for voucher_no, fee_p, receivable_p in (("V-TEST-A", 1500, 101500), ("V-TEST-B", 2000, 102000)):
        conn.execute(
            "INSERT INTO gl_entries (voucher_no, line_no, entry_date, account, debit_p, credit_p, memo) VALUES "
            "(?, 1, '2026-08-11', 'BANK', 100000, 0, 'test'), "
            "(?, 2, '2026-08-11', 'FEE_EXPENSE', ?, 0, 'test'), "
            "(?, 3, '2026-08-11', 'PG_RECEIVABLE', 0, ?, 'test')",
            (voucher_no, voucher_no, fee_p, voucher_no, receivable_p),
        )
    # A hop2 'proposed' link into each bank line, so hop3's batch_payments
    # dict picks both up (using two real capture rows from the clean world
    # loaded above — their own amounts are irrelevant here since the
    # ambiguity path returns before any decomposition check runs).
    payment_ids = [r[0] for r in conn.execute("SELECT payment_id FROM gw_payments WHERE kind = 'capture' LIMIT 2")]
    assert len(payment_ids) == 2
    for seq, (payment_id, line_id) in enumerate(zip(payment_ids, ("BL-TEST-X", "BL-TEST-Y")), start=1):
        conn.execute(
            "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence, run_id) "
            "VALUES (?, 2, 'gw', ?, 'bank', ?, 1, 1.0, 'proposed', 'test_fixture', NULL, ?)",
            (f"TEST-ML2-{seq}", payment_id, line_id, RUN_ID),
        )
    conn.commit()

    stats = hop3.run_hop3(conn, RUN_ID)

    assert stats.gl_ambiguous_match == 1  # reported exactly once, not once per bank line
    exc_rows = conn.execute("SELECT records FROM exceptions WHERE code = 'GL_AMBIGUOUS_MATCH'").fetchall()
    assert len(exc_rows) == 1

    gl_records = json.loads(exc_rows[0][0])
    voucher_ids = {r["id"] for r in gl_records if r["src"] == "gl"}
    assert voucher_ids == {"V-TEST-A", "V-TEST-B"}  # both candidates named, neither silently dropped
    for voucher_no in voucher_ids:
        n = conn.execute("SELECT COUNT(*) FROM match_link WHERE hop = 3 AND id_b = ?", (voucher_no,)).fetchone()[0]
        assert n == 0, f"{voucher_no} must have zero proposed hop3 links — refusal, not a guess"


def test_d03_unlinked_refund(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = _run_hops(conn)
    assert stats.unlinked_refund == config.DEFECT_COUNTS["D-03"]
    rows = conn.execute("SELECT severity FROM exceptions WHERE code = 'UNLINKED_REFUND'").fetchall()
    assert all(r[0] == "warn" for r in rows)


def test_d11_chargeback_unresolved_and_gl_balances(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    stats = _run_hops(conn)
    assert stats.chargeback_unresolved == config.DEFECT_COUNTS["D-11"]
    exc = conn.execute(
        "SELECT severity FROM exceptions WHERE code = 'CHARGEBACK_UNRESOLVED'"
    ).fetchone()
    assert exc[0] == "critical"
    # the chargeback's own settlement voucher must still balance (it gets
    # its own CHARGEBACK_LOSS line, not lumped into PG_RECEIVABLE)
    assert verifier.check_v3_gl_balance(conn) == []


def test_clean_world_all_batches_match_no_unlinked_refunds(tmp_path: Path):
    conn = _seeded_db(tmp_path, defects=False)
    stats = _run_hops(conn)
    assert stats.gl_missing == 0
    assert stats.gl_duplicate == 0
    assert stats.decomposition_fail == 0
    assert stats.unlinked_refund == 0
    assert stats.chargeback_unresolved == 0
    assert stats.matched > 0
