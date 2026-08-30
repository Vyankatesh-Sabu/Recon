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
