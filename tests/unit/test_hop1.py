"""test_hop1.py — hop-1 against the seeded defect world (SPEC §5.3 x §6.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config
from recon.db import migrate
from recon.engine.hop1 import run_hop1
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "TEST-RUN-HOP1"


def _seeded_db(tmp_path: Path) -> sqlite3.Connection:
    data_dir = tmp_path / "data"
    world, _truth = generate_world(config.SEED, defects=True)
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


def test_hop1_never_accepts_anything(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    run_hop1(conn, RUN_ID)
    statuses = {row[0] for row in conn.execute("SELECT DISTINCT status FROM match_link")}
    assert statuses == {"proposed"}


def test_hop1_raises_expected_codes_for_seeded_defects(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    run_hop1(conn, RUN_ID)
    codes = [row[0] for row in conn.execute("SELECT code FROM exceptions ORDER BY exc_id")]

    # D-06 (2 orphan payments), D-08 (1 duplicate), D-09 (1 orphan order),
    # D-12 (1 partial-capture mismatch) — SPEC §5.3.
    assert codes.count("ORPHAN_PAYMENT") == config.DEFECT_COUNTS["D-06"]
    assert codes.count("DUPLICATE_PAYMENT") == config.DEFECT_COUNTS["D-08"]
    assert codes.count("ORPHAN_ORDER") == config.DEFECT_COUNTS["D-09"]
    assert codes.count("PARTIAL_CAPTURE_MISMATCH") == config.DEFECT_COUNTS["D-12"]


def test_hop1_partial_capture_mismatch_amount(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    run_hop1(conn, RUN_ID)
    row = conn.execute(
        "SELECT amount_at_risk_p FROM exceptions WHERE code = 'PARTIAL_CAPTURE_MISMATCH'"
    ).fetchone()
    assert row[0] == config.D12_ORDER_AMOUNT_P - config.D12_CAPTURE_AMOUNT_P


def test_hop1_cod_orders_skipped_silently(tmp_path: Path):
    conn = _seeded_db(tmp_path)
    run_hop1(conn, RUN_ID)
    cod_order_ids = [row[0] for row in conn.execute("SELECT order_id FROM orders WHERE method = 'cod'")]
    assert len(cod_order_ids) == config.N_COD_ORDERS
    for order_id in cod_order_ids:
        n = conn.execute(
            "SELECT COUNT(*) FROM exceptions WHERE records LIKE ?", (f'%"{order_id}"%',)
        ).fetchone()[0]
        assert n == 0, f"cod order {order_id} should not raise any exception"


def test_hop1_clean_world_fully_chains_no_exceptions(tmp_path: Path):
    data_dir = tmp_path / "data"
    world, _truth = generate_world(config.SEED, defects=False)
    write_csvs(world, data_dir)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    load_all(conn, data_dir)
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES (?, ?, datetime('now'), 'off')",
        (RUN_ID, config.SEED),
    )
    conn.commit()

    stats = run_hop1(conn, RUN_ID)
    assert stats.orphan_orders == 0
    assert stats.orphan_payments == 0
    assert stats.duplicate_payments == 0
    assert stats.partial_mismatches == 0
    non_cod = conn.execute("SELECT COUNT(*) FROM orders WHERE method != 'cod'").fetchone()[0]
    assert stats.links_proposed == non_cod
