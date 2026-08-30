#!/usr/bin/env python3
"""gate_p3.py — Phase P3 gate (SPEC.md G3). Run directly: python tests/gates/gate_p3.py

G3: residual_p == exposure_p to the paisa, on seed 42 (with defects).
Also smoke-checks that hop3 actually ran and produced the codes SPEC §6.4
describes, since a G3 pass where hop3 never ran anything would be vacuous.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config
from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.engine.verifier import compute_exposure_p, compute_residual_p
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


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

        ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")

        conn = sqlite3.connect(db_path)
        residual_p = compute_residual_p(conn)
        exposure_p, breakdown = compute_exposure_p(conn, ctx["run_id"])
        check(
            residual_p == exposure_p,
            f"G3: residual_p ({residual_p}) != exposure_p ({exposure_p}) — breakdown: {breakdown}",
        )
        check(
            ctx["metrics"]["residual_p"] == residual_p,
            "G3: pipeline-reported residual_p doesn't match a fresh recomputation",
        )

        h3 = ctx["hop3_stats"]
        check(h3.matched > 0, "G3: hop3 never successfully matched a settlement to a GL journal")
        check(h3.decomposition_fail == 1, f"G3: expected 1 GL_DECOMPOSITION_FAIL (D-04), got {h3.decomposition_fail}")
        check(h3.gl_missing == 1, f"G3: expected 1 GL_MISSING (D-05), got {h3.gl_missing}")
        check(h3.gl_duplicate == 1, f"G3: expected 1 GL_DUPLICATE (D-13), got {h3.gl_duplicate}")
        check(
            h3.unlinked_refund == config.DEFECT_COUNTS["D-03"],
            f"G3: expected {config.DEFECT_COUNTS['D-03']} UNLINKED_REFUND (D-03), got {h3.unlinked_refund}",
        )

        gst_exc = conn.execute(
            "SELECT explanation FROM exceptions WHERE code = 'GL_DECOMPOSITION_FAIL'"
        ).fetchone()
        check(gst_exc is not None, "G3: no GL_DECOMPOSITION_FAIL exception found")
        if gst_exc is not None:
            check(
                "input tax credit" in gst_exc[0],
                f"G3: GL_DECOMPOSITION_FAIL explanation must state the lost input tax credit, got: {gst_exc[0]!r}",
            )
        conn.close()

    if FAILURES:
        print(f"GATE G3: FAIL ({len(FAILURES)} issue(s))")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"GATE G3: PASS (residual_p == exposure_p == {residual_p}p)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
