"""cli.py — typer app: generate|load|run|report|serve (SPEC §2).

P5: every command does real work. `serve` runs the FastAPI app (recon/api.py,
POST /ask — SPEC §9) via uvicorn if installed. The dashboard (SPEC §10) is P6.
"""

from __future__ import annotations

from pathlib import Path

import typer
from dotenv import load_dotenv

import recon.db as db
from recon.engine import pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.loader import load_all
from recon.report import report as report_module

# Loads .env (gitignored — see .env.example) into the environment before
# any provider code reads ANTHROPIC_API_KEY/GEMINI_API_KEY/RECON_LLM_
# PROVIDER. A no-op if .env doesn't exist or a var is already exported —
# never overrides a real shell export (CLAUDE.md rule 8's approved
# exception, confirmed with the user 2026-08-31).
load_dotenv()

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
def run(
    llm: str = typer.Option("off", help="on|off — CLAUDE.md rule 5: never load-bearing, off always works"),
    llm_provider: str = typer.Option(
        None, help="anthropic|gemini — which LLMClient strategy backs tier4 (default: $RECON_LLM_PROVIDER, else anthropic)"
    ),
    pace: int = typer.Option(
        0, "--pace", help="Milliseconds to sleep after each match/exception event — 0 (default) is as fast as possible; "
        "same event stream the API's SSE run console consumes (P6 supplement §3), printed one line per event here."
    ),
) -> None:
    """Run the matching pipeline: V3 -> hop1 -> hop2 -> hop3 -> verifier -> [tier4 -> verifier] -> V5 -> scorer (SPEC §6)."""
    llm_client = None
    if llm == "on":
        from recon.llm.client import create_llm_client

        llm_client = create_llm_client(llm_provider)

    def print_event(event: dict) -> None:
        if event["kind"] == "match":
            typer.echo(f"  [{event['seq']:04d}] match  hop{event['hop']} {event['id_a']} <-> {event['id_b']}")
        else:
            typer.echo(f"  [{event['seq']:04d}] except hop{event['hop']} {event['code']} ({event['severity']})")

    on_event = print_event if pace else None
    ctx = pipeline.run_pipeline(llm_mode=llm, llm_client=llm_client, on_event=on_event, pace_ms=pace)
    m = ctx["metrics"]
    typer.echo(
        f"run: {ctx['run_id']} — {m['records_processed']} records, {m['runtime_s']:.2f}s, "
        f"false-match rate {m['false_match_rate'] * 100:.1f}%, "
        f"{m['exceptions']['open']} open exceptions"
        + (
            f", LLM: {m['llm_calls']['total']} calls "
            f"({m['llm_calls']['accepted']} accepted, {m['llm_calls']['rejected']} rejected, "
            f"{m['llm_calls']['abstained']} abstained)"
            if llm == "on"
            else ""
        )
        + ". Run `recon.cli report` to render it."
    )


@app.command()
def report() -> None:
    """Render the latest run's report — terminal (printed), JSON + HTML (written to data/)."""
    ctx = pipeline.load_latest_run_context()
    if ctx is None:
        typer.echo("report: no completed run found — run `recon.cli run` first.")
        raise typer.Exit(code=1)
    typer.echo(report_module.render_terminal(ctx))
    json_path, html_path = report_module.write_reports(ctx, DATA_DIR)
    typer.echo(f"report: wrote {json_path} and {html_path}")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve the FastAPI app: POST /ask (SPEC §9). The dashboard (SPEC §10) is P6."""
    try:
        import uvicorn
    except ImportError:
        typer.echo(
            "serve: the app is implemented (recon/api.py: POST /ask) but uvicorn isn't "
            "installed to run it — `pip install uvicorn` (not yet an approved dependency; "
            "add it to requirements.txt if you want `serve` to actually listen on a port). "
            "Meanwhile, tests/gates exercise the same app in-process via FastAPI's TestClient, "
            "no server needed."
        )
        raise typer.Exit(code=1)
    uvicorn.run("recon.api:app", host=host, port=port)


if __name__ == "__main__":
    app()
