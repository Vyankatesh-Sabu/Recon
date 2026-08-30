#!/usr/bin/env python3
"""gate_p4.py — Phase P4 gate (SPEC.md G4). Run directly: python tests/gates/gate_p4.py

G4: with MockLLM scripted to return a wrong match for the ambiguous-twins
case (D-02), the verifier rejects it and the exception stands. D-01 still
resolves. --llm off still runs the whole pipeline and reports honestly.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config
from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm.client import MockLLM
from recon.loader import load_all

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def _build_and_load(tmp_path: Path) -> tuple[Path, Path]:
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
    return db_path, gt_path


def check_wrong_match_rejected_and_d01_resolves(tmp_path: Path) -> None:
    db_path, gt_path = _build_and_load(tmp_path)
    conn = sqlite3.connect(db_path)
    d02_lines = [r[0] for r in conn.execute("SELECT line_id FROM bank_lines WHERE line_id LIKE 'bl_d02_%'")]
    conn.close()
    check(len(d02_lines) == 2, f"expected 2 D-02 bank lines, found {len(d02_lines)}")

    script = {
        line_id: {
            "decision": "match",
            "candidate": "candidate_a",
            "reason_code": None,
            "explanation": "Deliberately wrong test fixture.",
            "confidence": 0.9,
        }
        for line_id in d02_lines
    }
    mock = MockLLM(adjudicate_script=script)
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="on", llm_client=mock)

    conn = sqlite3.connect(db_path)
    try:
        for line_id in d02_lines:
            n_accepted = conn.execute(
                "SELECT COUNT(*) FROM match_link WHERE id_b = ? AND status = 'accepted'", (line_id,)
            ).fetchone()[0]
            check(n_accepted == 0, f"G4: {line_id} — a wrong tier-4 match must never be accepted, got {n_accepted}")

        exc_rows = conn.execute(
            "SELECT records, status FROM exceptions WHERE code = 'AMBIGUOUS_SETTLEMENT'"
        ).fetchall()
        check(len(exc_rows) == 2, f"G4: expected 2 AMBIGUOUS_SETTLEMENT exceptions to survive, found {len(exc_rows)}")
        for records_json, status in exc_rows:
            check(status == "open", f"G4: AMBIGUOUS_SETTLEMENT for {records_json} must stay open, got {status}")

        # D-01 still resolves (tier2_unique, unaffected by tier4/D-02 activity)
        d01_accepted = conn.execute(
            "SELECT COUNT(*) FROM match_link WHERE tier = 2 AND reason = 'tier2_subset_sum_unique' AND status = 'accepted'"
        ).fetchone()[0]
        check(d01_accepted > 0, "G4: D-01's batch must still resolve at tier 2")

        check(
            ctx["metrics"]["false_match_rate"] == 0.0,
            f"G4: false_match_rate must stay 0.0 even with a wrong tier-4 attempt, got {ctx['metrics']['false_match_rate']}",
        )
    finally:
        conn.close()


def check_llm_off_runs_and_reports_honestly(tmp_path: Path) -> None:
    db_path, gt_path = _build_and_load(tmp_path)
    ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
    check(ctx["metrics"]["llm_mode"] == "off", "G4: --llm off must record llm_mode='off'")
    check(
        ctx["metrics"]["llm_calls"] == {"total": 0, "accepted": 0, "rejected": 0, "abstained": 0},
        f"G4: --llm off must report zero LLM activity, got {ctx['metrics']['llm_calls']}",
    )
    check(
        ctx["metrics"]["false_match_rate"] == 0.0,
        f"G4: --llm off must still reconcile with zero false matches, got {ctx['metrics']['false_match_rate']}",
    )
    conn = sqlite3.connect(db_path)
    codes = {row[0] for row in conn.execute("SELECT code FROM exceptions WHERE status = 'open'")}
    conn.close()
    check("AMBIGUOUS_SETTLEMENT" in codes, "G4: --llm off must still report AMBIGUOUS_SETTLEMENT honestly")
    check("UNEXPLAINED_BANK_CREDIT" in codes, "G4: --llm off must still report UNEXPLAINED_BANK_CREDIT honestly")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        check_wrong_match_rejected_and_d01_resolves(Path(tmp) / "on")
    with tempfile.TemporaryDirectory() as tmp:
        check_llm_off_runs_and_reports_honestly(Path(tmp) / "off")

    if FAILURES:
        print(f"GATE G4: FAIL ({len(FAILURES)} issue(s))")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("GATE G4: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
