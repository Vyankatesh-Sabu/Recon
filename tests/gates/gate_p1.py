#!/usr/bin/env python3
"""gate_p1.py — Phase P1 gate (SPEC.md G1). Run directly: python tests/gates/gate_p1.py

(a) record counts exactly match the exact SPEC §5.2/§5.3 figures (the
    "~62/~64/~13/~90" aggregates in §5.3 are explicitly approximate — see
    CLAUDE.md/plan notes; only sanity-bound the aggregate total).
(b) clean mode (--no-defects) passes the brute-force chain checker with
    zero violations, and loads with zero quarantined rows.
(c) two `generate --seed 42` runs produce byte-identical SHA256 per file.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from recon.db import migrate
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all

from _chain_check import check_clean_world_chains

# Which D-codes raise a ground_truth exception at all, per SPEC §5.3's
# "Expected engine outcome" column — D-01 (resolves correctly via tier 2)
# and D-14 (loader normalises silently) raise none.
EXPECTED_EXCEPTIONS_PER_DEFECT = {
    "D-01": 0,
    "D-02": 1,  # one AMBIGUOUS_SETTLEMENT entry covering the pair
    "D-03": 2,
    "D-04": 1,
    "D-05": 1,
    "D-06": 2,
    "D-07": 1,
    "D-08": 1,
    "D-09": 1,
    "D-10": 1,
    "D-11": 1,
    "D-12": 1,
    "D-13": 1,
    "D-14": 0,
}
N_IN_TRANSIT_BATCHES = 2  # last SETTLEMENT_LAG_BDAYS business days always land past DATE_TO

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def check_record_counts() -> None:
    world, truth = generate_world(config.SEED, defects=True)
    check(len(world.orders) == config.N_ORDERS, f"(a) expected {config.N_ORDERS} orders, got {len(world.orders)}")
    cod = sum(1 for o in world.orders if o.method == "cod")
    check(cod == config.N_COD_ORDERS, f"(a) expected {config.N_COD_ORDERS} cod orders, got {cod}")
    refunds = sum(1 for p in world.gw_payments if p.kind == "refund")
    check(refunds == config.N_REFUNDS, f"(a) expected {config.N_REFUNDS} refunds, got {refunds}")

    orphan_payments = sum(1 for p in world.gw_payments if p.order_id is None)
    check(
        orphan_payments == config.DEFECT_COUNTS["D-06"],
        f"(a) expected {config.DEFECT_COUNTS['D-06']} orphan payments (D-06), got {orphan_payments}",
    )

    expected_exceptions = sum(EXPECTED_EXCEPTIONS_PER_DEFECT.values())
    check(
        len(truth.exceptions) == expected_exceptions,
        f"(a) expected {expected_exceptions} ground-truth exceptions, got {len(truth.exceptions)}",
    )
    check(
        len(truth.in_transit) == N_IN_TRANSIT_BATCHES,
        f"(a) expected {N_IN_TRANSIT_BATCHES} in-transit batches, got {len(truth.in_transit)}",
    )

    total_records = len(world.orders) + len(world.gw_payments) + len(world.bank_lines) + len(world.gl_entries)
    check(total_records > 200, f"(a) expected >200 total records (SPEC §5.3), got {total_records}")

    clean_world, clean_truth = generate_world(config.SEED, defects=False)
    check(
        len(clean_world.orders) == config.N_ORDERS,
        f"(a) clean: expected {config.N_ORDERS} orders, got {len(clean_world.orders)}",
    )
    clean_refunds = sum(1 for p in clean_world.gw_payments if p.kind == "refund")
    check(clean_refunds == config.N_REFUNDS, f"(a) clean: expected {config.N_REFUNDS} refunds, got {clean_refunds}")
    check(len(clean_truth.exceptions) == 0, f"(a) clean: expected 0 exceptions, got {len(clean_truth.exceptions)}")
    check(
        len(clean_truth.in_transit) == N_IN_TRANSIT_BATCHES,
        f"(a) clean: expected {N_IN_TRANSIT_BATCHES} in-transit batches, got {len(clean_truth.in_transit)}",
    )


def check_clean_world_reconciles() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        db_path = tmp_path / "recon.db"
        world, truth = generate_world(config.SEED, defects=False)
        write_csvs(world, data_dir)
        write_ground_truth(truth, data_dir / "ground_truth.json")
        migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
        conn = sqlite3.connect(db_path)
        try:
            report = load_all(conn, data_dir)
            check(len(report.quarantined) == 0, f"(b) clean load quarantined rows: {report.quarantined}")
            violations = check_clean_world_chains(conn)
            check(
                len(violations) == 0,
                "(b) brute-force chain check found violations:\n    " + "\n    ".join(violations),
            )
        finally:
            conn.close()


def check_determinism() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dir1, dir2 = tmp_path / "run1", tmp_path / "run2"
        for out_dir in (dir1, dir2):
            world, truth = generate_world(config.SEED, defects=True)
            write_csvs(world, out_dir)
            write_ground_truth(truth, out_dir / "ground_truth.json")
        for name in ("orders.csv", "gateway.csv", "bank.csv", "gl.csv", "ground_truth.json"):
            h1 = hashlib.sha256((dir1 / name).read_bytes()).hexdigest()
            h2 = hashlib.sha256((dir2 / name).read_bytes()).hexdigest()
            check(h1 == h2, f"(c) {name}: SHA256 differs between two seed={config.SEED} runs")


def main() -> int:
    check_record_counts()
    check_clean_world_reconciles()
    check_determinism()
    if FAILURES:
        print(f"GATE G1: FAIL ({len(FAILURES)} issue(s))")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("GATE G1: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
