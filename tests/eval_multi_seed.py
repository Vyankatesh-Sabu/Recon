#!/usr/bin/env python3
"""eval_multi_seed.py — correctness evidence across MANY independently
generated worlds, not just seed=42.

Every gate/test in this project (gate_p1..gate_p6, all of tests/unit) runs
against seed 42 alone. That's deliberate for gates (byte-identical,
reviewable, the number everyone points at) — but it means every claim
we've made ("0.0% false-match rate") has so far only ever been checked on
one dataset. This script generates `--count` independent worlds (seeds
`--start`..`--start+count-1`), runs the full LLM-off pipeline on each in
its own temp DB (never touching data/recon.db), and reports:

  - how many seeds even GENERATE successfully — several of the 14 defect
    injectors (recon/generator/defects.py) raise RuntimeError outright if
    that seed's random world doesn't happen to contain a structurally
    valid candidate (e.g. D-01 needs a settled batch of 5-7 rows with
    exactly one refund). Seed 42 was implicitly hand-picked to have all 14
    line up; most seeds will NOT. That's real information about how
    seed-fragile the defect suite is, not a bug in this script.
  - among the ones that generate and load, whether the pipeline completes
    or aborts (verifier.ClearingControlFailure — a hard failure, since V5
    is supposed to catch OUR bugs, not defect placement).
  - across every seed that completed: the false-match rate distribution
    (the number that matters), plus precision/recall/full-chain-rate
    ranges. A false_match_rate > 0 anywhere is flagged as the loudest
    possible finding, not averaged away.

Writes the full per-seed breakdown to --out (default data/eval_report.json)
so a specific failing seed can be re-run and inspected by hand.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import typer

from recon.db import migrate
from recon.engine.pipeline import run_pipeline
from recon.engine.verifier import ClearingControlFailure
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all

app = typer.Typer(add_completion=False)


def _run_one_seed(seed: int) -> dict:
    """Generate, load, and run the LLM-off pipeline for one seed in an
    isolated temp DB. Returns a result dict; never raises — every failure
    mode (generation, load, pipeline abort) is caught and reported as data,
    because the whole point is to see every outcome, not stop at the first."""
    t0 = time.monotonic()
    try:
        world, truth = generate_world(seed, defects=True)
    except Exception as exc:
        return {"seed": seed, "stage": "generate", "ok": False, "error": str(exc)}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        write_csvs(world, data_dir)
        gt_path = data_dir / "ground_truth.json"
        write_ground_truth(truth, gt_path)
        db_path = tmp_path / "recon.db"
        migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")

        conn = sqlite3.connect(db_path)
        try:
            load_report = load_all(conn, data_dir)
        finally:
            conn.close()
        if load_report.quarantined:
            return {
                "seed": seed,
                "stage": "load",
                "ok": False,
                "error": f"{len(load_report.quarantined)} row(s) quarantined",
                "quarantined": [q.reason for q in load_report.quarantined],
            }

        try:
            ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=seed, llm_mode="off")
        except ClearingControlFailure as exc:
            return {"seed": seed, "stage": "pipeline", "ok": False, "error": f"V5 CLEARING CONTROL FAILED: {exc}"}
        except Exception as exc:
            return {"seed": seed, "stage": "pipeline", "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    m = ctx["metrics"]
    return {
        "seed": seed,
        "stage": "done",
        "ok": True,
        "runtime_s": round(time.monotonic() - t0, 3),
        "records_processed": m["records_processed"],
        "false_match_rate": m["false_match_rate"],
        "link_precision": m["link_precision"],
        "link_recall": m["link_recall"],
        "full_chain_rate": m["full_chain_rate"],
        "exceptions_open": m["exceptions"]["open"],
        "residual_p": m["residual_p"],
    }


@app.command()
def main(
    start: int = typer.Option(1, help="First seed to evaluate"),
    count: int = typer.Option(100, help="Number of consecutive seeds to evaluate (start..start+count-1)"),
    out: Path = typer.Option(Path("data/eval_report.json"), help="Where to write the full per-seed JSON report"),
) -> None:
    """Run the LLM-off pipeline across MANY independently generated seeds
    to measure correctness across worlds, not just the single seed (42)
    every gate uses."""
    seeds = range(start, start + count)
    results = [_run_one_seed(seed) for seed in seeds]

    gen_failed = [r for r in results if r["stage"] == "generate"]
    load_failed = [r for r in results if r["stage"] == "load"]
    pipeline_aborted = [r for r in results if r["stage"] == "pipeline"]
    completed = [r for r in results if r["ok"]]
    false_matches = [r for r in completed if r["false_match_rate"] > 0]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))

    print(f"EVAL: seeds {start}..{start + count - 1} ({count} total)")
    print(f"  generated + loaded + ran to completion : {len(completed)}/{count}")
    print(f"  generation failed (defect had no candidate on this seed's world) : {len(gen_failed)}")
    print(f"  loader quarantined a row  : {len(load_failed)}")
    print(f"  pipeline aborted (verifier.ClearingControlFailure or other) : {len(pipeline_aborted)}")
    print()

    if completed:
        fmrs = [r["false_match_rate"] for r in completed]
        precisions = [r["link_precision"] for r in completed]
        recalls = [r["link_recall"] for r in completed]
        chains = [r["full_chain_rate"] for r in completed]
        print(f"  false-match rate   : min {min(fmrs):.4f}  max {max(fmrs):.4f}  mean {sum(fmrs)/len(fmrs):.4f}")
        print(f"  link precision     : min {min(precisions):.4f}  max {max(precisions):.4f}  mean {sum(precisions)/len(precisions):.4f}")
        print(f"  link recall        : min {min(recalls):.4f}  max {max(recalls):.4f}  mean {sum(recalls)/len(recalls):.4f}")
        print(f"  full chain rate    : min {min(chains):.4f}  max {max(chains):.4f}  mean {sum(chains)/len(chains):.4f}")
        print()

    if false_matches:
        print(f"  *** {len(false_matches)} seed(s) produced a NONZERO false-match rate: ***")
        for r in false_matches:
            print(f"    seed {r['seed']}: false_match_rate={r['false_match_rate']}")
    else:
        print("  false-match rate was 0.0 on every seed that completed.")

    if pipeline_aborted:
        print()
        print(f"  *** {len(pipeline_aborted)} seed(s) ABORTED the pipeline: ***")
        for r in pipeline_aborted:
            print(f"    seed {r['seed']}: {r['error']}")

    if gen_failed:
        reasons: dict[str, int] = {}
        for r in gen_failed:
            key = r["error"].split(":", 1)[0]
            reasons[key] = reasons.get(key, 0) + 1
        print()
        print(f"  generation-failure reasons (defect injector, count of seeds it blocked):")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {reason}: {n}")

    print()
    print(f"Full per-seed report written to {out}")

    if false_matches or pipeline_aborted:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
