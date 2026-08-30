"""test_verifier.py — V1/V2/V3 (SPEC §6.5). Only verifier.py may accept a link."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import config
from recon.db import migrate
from recon.engine import hop1, hop2, verifier
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "TEST-RUN-VERIFIER"


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


def test_v3_clean_world_all_vouchers_balance(tmp_path: Path):
    conn = _seeded_db(tmp_path, defects=False)
    assert verifier.check_v3_gl_balance(conn) == []


def test_v3_detects_a_corrupted_voucher(tmp_path: Path):
    conn = _seeded_db(tmp_path, defects=False)
    voucher_no = conn.execute("SELECT voucher_no FROM gl_entries LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE gl_entries SET debit_p = debit_p + 100 WHERE voucher_no = ? AND line_no = 1", (voucher_no,)
    )
    conn.commit()
    violations = verifier.check_v3_gl_balance(conn)
    assert any(v[0] == voucher_no for v in violations)


def test_end_to_end_only_accepts_via_verifier(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    hop1.run_hop1(conn, RUN_ID)
    hop2.run_hop2(conn, RUN_ID)
    # before verifier runs, nothing is accepted yet
    assert conn.execute("SELECT COUNT(*) FROM match_link WHERE status = 'accepted'").fetchone()[0] == 0
    stats = verifier.run_verifier(conn, RUN_ID)
    assert stats.accepted > 0
    statuses = {row[0] for row in conn.execute("SELECT DISTINCT status FROM match_link")}
    assert statuses <= {"accepted", "rejected"}  # nothing left 'proposed'


def test_v1_rejects_a_link_whose_reference_vanished(tmp_path: Path):
    conn = _seeded_db(tmp_path, defects=False)
    hop1.run_hop1(conn, RUN_ID)
    link = conn.execute(
        "SELECT link_id, id_b FROM match_link WHERE hop = 1 AND status = 'proposed' LIMIT 1"
    ).fetchone()
    link_id, payment_id = link
    conn.execute("DELETE FROM gw_payments WHERE payment_id = ?", (payment_id,))
    conn.commit()
    verifier.run_verifier(conn, RUN_ID)
    status, reason = conn.execute(
        "SELECT status, reason FROM match_link WHERE link_id = ?", (link_id,)
    ).fetchone()
    assert status == "rejected"
    assert reason.startswith("V1_failed")


def test_v2_duplicate_claim_rejected_with_exception(tmp_path: Path):
    conn = _seeded_db(tmp_path, defects=False)
    order_id, payment_id = conn.execute(
        "SELECT order_id, payment_id FROM gw_payments WHERE kind = 'capture' LIMIT 1"
    ).fetchone()
    # Two independent proposals both claiming the same order at hop 1 —
    # a genuine double-claim, which one_claim_a must catch.
    conn.execute(
        "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, run_id) "
        "VALUES ('DUP-1', 1, 'orders', ?, 'gw', ?, 1, 1.0, 'proposed', ?)",
        (order_id, payment_id, RUN_ID),
    )
    conn.execute(
        "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, run_id) "
        "VALUES ('DUP-2', 1, 'orders', ?, 'gw', ?, 1, 1.0, 'proposed', ?)",
        (order_id, payment_id, RUN_ID),
    )
    conn.commit()
    stats = verifier.run_verifier(conn, RUN_ID)
    assert stats.duplicate_claims == 1

    statuses = dict(
        conn.execute("SELECT link_id, status FROM match_link WHERE link_id IN ('DUP-1', 'DUP-2')").fetchall()
    )
    assert sorted(statuses.values()) == ["accepted", "rejected"]

    exc = conn.execute("SELECT code, severity FROM exceptions WHERE code = 'DUPLICATE_CLAIM'").fetchone()
    assert exc == ("DUPLICATE_CLAIM", "critical")

    # the DB itself refuses a second accepted claim on the same record —
    # this is what verifier.py's catch actually depends on.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE match_link SET status = 'accepted' WHERE link_id = 'DUP-2' AND status = 'rejected'"
        )
    conn.rollback()
