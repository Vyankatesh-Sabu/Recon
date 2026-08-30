"""test_v5_clearing_control.py — T-7: a corrupted GL amount aborts the pipeline loudly.

V5 exists to catch OUR bugs, not the synthetic world's defects — so this
corrupts a fixture in a way NO existing exception code can explain, and
asserts the pipeline refuses to produce a report rather than silently
absorbing the discrepancy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import config
from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.engine.verifier import ClearingControlFailure, check_v5_clearing_control
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_clean_db(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    world, truth = generate_world(config.SEED, defects=False)
    write_csvs(world, data_dir)
    gt_path = data_dir / "ground_truth.json"
    write_ground_truth(truth, gt_path)
    db_path = tmp_path / "recon.db"
    migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    report = load_all(conn, data_dir)
    assert report.quarantined == []
    conn.close()
    return db_path, gt_path


def test_clean_world_v5_reconciles_before_corruption(tmp_path: Path):
    """Sanity baseline: the clean world's residual/exposure already agree
    (in-transit batches are the only nonzero contributor) before we break
    anything — so the corruption below is what causes the failure, not an
    already-broken fixture."""
    db_path, gt_path = _build_clean_db(tmp_path)
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED)  # must not raise
    assert isinstance(ctx["metrics"]["residual_p"], int)
    assert ctx["finished_at"] is not None


def test_corrupted_gl_amount_aborts_the_pipeline(tmp_path: Path):
    db_path, gt_path = _build_clean_db(tmp_path)
    conn = sqlite3.connect(db_path)

    # Corrupt one settlement voucher: bump BOTH its BANK debit and its
    # PG_RECEIVABLE credit by the same small amount (voucher stays
    # balanced, V3 stays silent), small enough (< AMOUNT_TOL_P) that hop3's
    # window search and V4's tolerance check still accept the pairing
    # normally — nothing downstream has any reason to flag this batch at
    # all, yet PG_RECEIVABLE now carries an extra, unexplained credit.
    # Pick a settlement voucher specifically (has both BANK and
    # PG_RECEIVABLE lines) — a daily CAP voucher (Dr PG_RECEIVABLE / Cr
    # SALES) has no BANK line, so nudging "BANK" there would be a no-op.
    voucher_no = conn.execute("SELECT voucher_no FROM gl_entries WHERE account = 'BANK' LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE gl_entries SET debit_p = debit_p + 50 WHERE voucher_no = ? AND account = 'BANK'",
        (voucher_no,),
    )
    conn.execute(
        "UPDATE gl_entries SET credit_p = credit_p + 50 WHERE voucher_no = ? AND account = 'PG_RECEIVABLE'",
        (voucher_no,),
    )
    conn.commit()
    # Voucher still balances (both sides +50) — confirm V3 alone wouldn't catch this.
    imbalanced = conn.execute(
        "SELECT voucher_no FROM gl_entries WHERE voucher_no = ? GROUP BY voucher_no "
        "HAVING SUM(debit_p) != SUM(credit_p)",
        (voucher_no,),
    ).fetchall()
    assert imbalanced == []
    conn.close()

    with pytest.raises(ClearingControlFailure) as exc_info:
        run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED)

    message = str(exc_info.value)
    assert "residual_p=" in message
    assert "exposure_p=" in message
    assert "50" in message  # the 50p diff introduced above is surfaced

    # the run must NOT be marked finished — no partial report exists
    conn = sqlite3.connect(db_path)
    unfinished = conn.execute("SELECT COUNT(*) FROM runs WHERE finished_at IS NULL").fetchone()[0]
    assert unfinished >= 1
    conn.close()


def test_check_v5_directly_reports_per_code_breakdown(tmp_path: Path):
    db_path, gt_path = _build_clean_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES ('V5-DIRECT', ?, datetime('now'), 'off')",
        (config.SEED,),
    )
    conn.commit()

    # Corrupt without running any hop — an exposure-less, unexplained
    # PG_RECEIVABLE credit bump.
    voucher_no = conn.execute("SELECT voucher_no FROM gl_entries WHERE account = 'PG_RECEIVABLE' LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE gl_entries SET credit_p = credit_p + 100000 WHERE voucher_no = ? AND account = 'PG_RECEIVABLE'",
        (voucher_no,),
    )
    conn.commit()

    with pytest.raises(ClearingControlFailure) as exc_info:
        check_v5_clearing_control(conn, "V5-DIRECT")
    assert "Per-code exposure breakdown" in str(exc_info.value)
    conn.close()
