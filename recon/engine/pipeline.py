"""pipeline.py — orchestration, run record (SPEC §6).

Assumes `recon.cli load` has already run (schema migrated, data/*.csv
loaded into the DB) — this module picks up from there: V3 -> hop1 -> hop2
-> hop3 -> verifier -> [tier4 -> verifier again, if llm_mode=='on'] -> V5
-> score. Matches SPEC §6's stated run order exactly: hop3 runs BEFORE the
verifier's first pass (so hop3's "accepted settlement line" actually means
"hop2 proposed", not yet verifier-accepted — see hop3.py), and tier4 only
gets a turn on whatever residue survives that first pass.

If V5 (the clearing-account control) fails, `run_pipeline` lets
verifier.ClearingControlFailure propagate uncaught — no partial `runs` row
is marked finished, no report is generated. This control exists to catch
OUR bugs; never catch-and-continue past it.

CLAUDE.md rule 5: the LLM is never load-bearing. `llm_mode='off'` never
imports recon.llm at all — the exact same V3->hop1->hop2->hop3->verifier->V5
path runs either way; llm_mode='on' only ever ADDS a chance to resolve
residue that already reports honestly as an exception without it.
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
from recon.engine import hop1, hop2, hop3, verifier
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
    llm_client: object | None = None,
) -> dict:
    """Run V3 -> hop1 -> hop2 -> hop3 -> verifier -> [tier4 -> verifier] -> V5 -> score.

    `llm_client`: an `LLMClient` to use when `llm_mode=='on'` (tests inject
    `MockLLM`; omitted, a real provider backend is constructed from
    `RECON_LLM_PROVIDER`/API-key env vars via `recon.llm.client.create_llm_client`).
    Ignored when `llm_mode=='off'` — recon.llm is never even imported in that case.

    Returns a run context dict (run_id, timing, metrics, top_exceptions,
    per-stage stats) that report.py renders. Writes the `runs` row itself.
    Raises verifier.ClearingControlFailure (V5 mismatch) uncaught — no
    `runs.finished_at` is set and no report context is returned in that case.
    """
    conn = recon_db.connect(db_path)
    try:
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
        hop3_stats = hop3.run_hop3(conn, run_id)
        verifier.run_verifier(conn, run_id)  # 1st pass: hop1/hop2/hop3 proposals

        tier4_stats = None
        narrated = 0
        if llm_mode == "on":
            from recon.llm import adjudicator
            from recon.llm.client import create_llm_client

            client = llm_client if llm_client is not None else create_llm_client()
            tier4_stats = adjudicator.run_tier4(conn, run_id, hop2_stats.evidence_log, client)
            verifier.run_verifier(conn, run_id)  # 2nd pass: tier-4 proposals only
            adjudicator.finalize_tier4_stats(conn, run_id, tier4_stats)
            adjudicator.resolve_exceptions_for_accepted_tier4(conn, run_id)
            narrated = adjudicator.narrate_exceptions(conn, run_id, client)

        verifier.check_v5_clearing_control(conn, run_id)  # aborts (raises) on mismatch
        runtime_s = time.monotonic() - t0

        metrics = scorer.score(conn, run_id, ground_truth_path)
        metrics["runtime_s"] = runtime_s
        metrics["seed"] = seed
        metrics["llm_mode"] = llm_mode
        metrics["v3_violations"] = v3_violations
        metrics["residual_p"] = verifier.compute_residual_p(conn)
        metrics["narrated_exceptions"] = narrated
        if tier4_stats is not None:
            metrics["llm_calls"] = {
                "total": tier4_stats.proposed + tier4_stats.abstained,
                "accepted": tier4_stats.accepted,
                "rejected": tier4_stats.rejected,
                "abstained": tier4_stats.abstained,
            }
            metrics["llm_call_log"] = tier4_stats.call_log

        finished_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE runs SET finished_at = ?, metrics = ? WHERE run_id = ?",
            (finished_at, json.dumps(metrics), run_id),
        )
        conn.commit()

        top_exceptions = _top_exceptions(conn, run_id)

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
            "hop3_stats": hop3_stats,
            "tier4_stats": tier4_stats,
        }
    finally:
        conn.close()


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
