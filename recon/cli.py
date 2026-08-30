"""cli.py — typer app: generate|load|run|report|serve (SPEC §2).

P1: `generate` and `load` do real, deterministic, seeded work (SPEC §5, §6.1).
`run`/`report`/`serve` are still P0 stubs — hop matching is P2.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

import recon.db as db
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all

app = typer.Typer(help="RECON-4: four-way payment reconciliation agent.", no_args_is_help=True)

DATA_DIR = Path("data")


@app.command()
def generate(
    seed: int = 42,
    defects: bool = typer.Option(True, "--defects/--no-defects", help="Inject SPEC §5.3 defects (default on)"),
) -> None:
    """Build the synthetic world and write data/*.csv + ground_truth.json (SPEC §5)."""
    world, truth = generate_world(seed, defects=defects)
    write_csvs(world, DATA_DIR)
    write_ground_truth(truth, DATA_DIR / "ground_truth.json")
    typer.echo(
        f"generate: seed={seed} defects={'on' if defects else 'off'} — wrote "
        f"{len(world.orders)} orders, {len(world.gw_payments)} gateway rows, "
        f"{len(world.bank_lines)} bank lines, {len(world.gl_entries)} GL lines "
        f"to {DATA_DIR}/ (+ ground_truth.json: {len(truth.links)} links, "
        f"{len(truth.exceptions)} exceptions, {len(truth.in_transit)} in-transit)"
    )


@app.command()
def load() -> None:
    """Apply pending migrations, then normalise and load data/*.csv (SPEC §6.1)."""
    applied = db.migrate()
    if applied:
        typer.echo(f"load: applied migrations: {', '.join(applied)}")
    conn = db.connect()
    try:
        report = load_all(conn, DATA_DIR)
    finally:
        conn.close()
    typer.echo(f"load: loaded {DATA_DIR}/*.csv into {db.DB_PATH}")
    typer.echo(report.summary())


@app.command()
def run(llm: str = typer.Option("off", help="on|off — SPEC.md rule 5")) -> None:
    """Run the matching pipeline: hop1 -> hop2 -> hop3 -> verifier -> (tier4) -> scorer."""
    stages = "hop1 -> hop2 -> hop3 -> verifier"
    if llm == "on":
        stages += " -> tier4 adjudicator -> verifier"
    stages += " -> scorer"
    typer.echo(f"run: would execute {stages}, llm={llm} (SPEC.md §6) — not yet implemented")


@app.command()
def report() -> None:
    """Render the run report (terminal, JSON, HTML) — stub prints zeroed metrics."""
    stub_metrics = {
        "seed": None,
        "llm_mode": None,
        "records_processed": 0,
        "runtime_s": 0.0,
        "llm_calls": {"total": 0, "accepted": 0, "rejected": 0, "abstained": 0},
        "hop_match": {"h1": "0/0", "h2": "0/0", "h3": "0/0"},
        "full_chain_rate": 0.0,
        "link_precision": 0.0,
        "link_recall": 0.0,
        "false_match_rate": 0.0,
        "exceptions": {"open": 0, "critical": 0, "warn": 0, "info": 0},
        "amount_at_risk_p": 0,
        "clearing_residual_p": 0,
        "clearing_exposure_p": 0,
    }
    typer.echo("RECON-4 RUN (stub) · no run has been executed yet")
    typer.echo(json.dumps(stub_metrics, indent=2))
    typer.echo("report: real terminal/JSON/HTML rendering not yet implemented (SPEC.md §7)")


@app.command()
def serve() -> None:
    """Start the FastAPI app exposing Q&A tools + dashboard endpoints (SPEC.md §9-10)."""
    typer.echo(
        "serve: would start the FastAPI app (Q&A tools + dashboard, SPEC.md "
        "§9-10) — not yet implemented"
    )


if __name__ == "__main__":
    app()
