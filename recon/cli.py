"""cli.py — typer app: generate|load|run|report|serve (SPEC §2).

P0: every command is a stub that prints what it would do. Nothing here
computes anything real yet — that lands phase by phase per SPEC.md §3.
"""

from __future__ import annotations

import json

import typer

from recon.db import DB_PATH

app = typer.Typer(help="RECON-4: four-way payment reconciliation agent.", no_args_is_help=True)


@app.command()
def generate(seed: int = 42) -> None:
    """Build the synthetic world (clean + defects) and write data/*.csv + ground_truth.json."""
    typer.echo(
        f"generate: would build the synthetic world at seed={seed} and write "
        "data/orders.csv, data/gateway.csv, data/bank.csv, data/gl.csv, "
        "data/ground_truth.json (SPEC.md §5) — not yet implemented"
    )


@app.command()
def load() -> None:
    """Normalise and load data/*.csv into the SQLite database (SPEC.md §6.1)."""
    typer.echo(
        f"load: would normalise and load data/orders.csv, data/gateway.csv, "
        f"data/bank.csv, data/gl.csv into {DB_PATH} (SPEC.md §6.1) — not yet "
        "implemented (run `python -m recon.db` to apply schema migrations)"
    )


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
