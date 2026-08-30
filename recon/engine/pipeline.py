"""pipeline.py — orchestration, run record (SPEC §6).

Assumes `recon.cli load` has already run (schema migrated, data/*.csv
loaded into the DB) — this module picks up from there: V3 -> hop1 -> hop2
-> verifier -> score. hop3/tier4 land in P3/P4; this is P2's full pipeline.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
from recon import db as recon_db
from recon.engine import hop1, hop2, verifier
from recon.scoring import scorer


def new_run_id(seed: int) -> str:
    return f"RUN-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{seed}-{uuid.uuid4().hex[:6]}"


def _top_exceptions(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return [
        dict(
            zip(
                ("exc_id", "code", "severity", "amount_at_risk_p", "explanation", "suggested_action"),
                row,
            )
        )
        for row in conn.execute(
            "SELECT exc_id, code, severity, amount_at_risk_p, explanation, suggested_action FROM exceptions "
            "WHERE run_id = ? AND status = 'open' ORDER BY amount_at_risk_p DESC LIMIT 10",
            (run_id,),
        ).fetchall()
    ]


def _write_v3_exceptions(conn: sqlite3.Connection, run_id: str) -> int:
    """V3 (SPEC §6.5): every GL voucher must balance. Conceptually "run at
    load", but needs a run_id to write exceptions against, so it runs here
    as the pipeline's first step, before any hop."""
    count = 0
    for voucher_no, debit, credit in verifier.check_v3_gl_balance(conn):
        count += 1
        conn.execute(
            "INSERT INTO exceptions "
            "(exc_id, run_id, code, severity, hop, records, amount_at_risk_p, age_days, explanation, suggested_action, status) "
            "VALUES (?, ?, 'DATA_QUALITY', 'critical', NULL, ?, ?, 0, ?, ?, 'open')",
            (
                f"{run_id}-V3-{count:04d}",
                run_id,
                json.dumps([{"src": "gl", "id": voucher_no}]),
                abs(debit - credit),
                f"GL voucher {voucher_no} does not balance: debit={debit}p credit={credit}p.",
                "Investigate the voucher's source before trusting any total derived from it.",
            ),
        )
    conn.commit()
    return count


def run_pipeline(
    db_path: Path | str = recon_db.DB_PATH,
    ground_truth_path: Path | str = Path("data/ground_truth.json"),
    seed: int = config.SEED,
    llm_mode: str = "off",
) -> dict:
    """Run V3 -> hop1 -> hop2 -> verifier -> score against the loaded DB.

    Returns a run context dict (run_id, timing, metrics, top_exceptions,
    per-stage stats) that report.py renders. Writes the `runs` row itself.
    """
    conn = recon_db.connect(db_path)
    run_id = new_run_id(seed)
    started_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO runs (run_id, seed, started_at, llm_mode) VALUES (?, ?, ?, ?)",
        (run_id, seed, started_at, llm_mode),
    )
    conn.commit()

    t0 = time.monotonic()
    v3_violations = _write_v3_exceptions(conn, run_id)
    hop1_stats = hop1.run_hop1(conn, run_id)
    hop2_stats = hop2.run_hop2(conn, run_id)
    verifier_stats = verifier.run_verifier(conn, run_id)
    runtime_s = time.monotonic() - t0

    metrics = scorer.score(conn, run_id, ground_truth_path)
    metrics["runtime_s"] = runtime_s
    metrics["seed"] = seed
    metrics["llm_mode"] = llm_mode
    metrics["v3_violations"] = v3_violations

    finished_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE runs SET finished_at = ?, metrics = ? WHERE run_id = ?",
        (finished_at, json.dumps(metrics), run_id),
    )
    conn.commit()

    top_exceptions = _top_exceptions(conn, run_id)
    conn.close()

    return {
        "run_id": run_id,
        "seed": seed,
        "llm_mode": llm_mode,
        "started_at": started_at,
        "finished_at": finished_at,
        "metrics": metrics,
        "top_exceptions": top_exceptions,
        "hop1_stats": hop1_stats,
        "hop2_stats": hop2_stats,
        "verifier_stats": verifier_stats,
    }


def load_latest_run_context(db_path: Path | str = recon_db.DB_PATH) -> dict | None:
    """Reconstruct the last `run_pipeline()` call's report context from the
    `runs` table, without re-running anything — what `recon.cli report`
    uses. Returns None if no run has ever completed."""
    conn = recon_db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_id, seed, started_at, finished_at, llm_mode, metrics FROM runs "
            "WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        run_id, seed, started_at, finished_at, llm_mode, metrics_json = row
        return {
            "run_id": run_id,
            "seed": seed,
            "llm_mode": llm_mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "metrics": json.loads(metrics_json),
            "top_exceptions": _top_exceptions(conn, run_id),
        }
    finally:
        conn.close()
