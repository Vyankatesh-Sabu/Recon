"""api.py — FastAPI app: Q&A (SPEC §9), dashboard data (SPEC §10), and the
run/streaming/reconstruction API added by the P6 UI supplement (§3).

POST /ask {"question": str} -> {"answer": str, "tool_calls": [...], "record_ids": [...]}.
GET  /report -> the latest run's metrics + full open-exception list, each
    with its evidence: the refusals (AMBIGUOUS_SETTLEMENT,
    UNEXPLAINED_BANK_CREDIT) carry their own, persisted on the exception
    row by hop2 (migration 003) since they deliberately propose no link;
    everything else is reconstructed from whichever match_link touched its
    records, and reports that link's id as `evidence_link_id`.
web/index.html is mounted at /dashboard (plain HTML + fetch, SPEC §10).

P6 supplement §3 API surface (all additive — /ask, /report, /dashboard are
untouched, existing tests keep passing):
    POST /api/run                     -> {"run_id": str}, starts a pipeline
                                          run in a background thread and
                                          returns immediately.
    GET  /api/run/{id}/stream         -> SSE: one "data: {...}\n\n" chunk per
                                          match/exception event, in emission
                                          order (recon/engine/events.py).
    GET  /api/run/{id}/metrics        -> that run's `runs` row + metrics.
    GET  /api/run/{id}/exceptions     -> that run's exceptions, filterable.
    GET  /api/run/latest              -> same body as .../metrics, for the
                                          most recently finished run.
    GET  /api/match/{link_id}         -> one match_link row, evidence parsed;
                                          hop-2 links also carry the full
                                          settlement reconstruction.
    GET  /api/order/{order_id}/chain  -> recon.llm.tools.trace_order, as JSON.
    GET  /api/control/clearing        -> V5's residual_p vs exposure_p, their
                                          difference, and the PG_RECEIVABLE
                                          T-account with a running balance.
    POST /api/ask                     -> same as /ask, plus an optional
                                          run_id to target a specific run
                                          instead of always the latest.

The LLM client and DB path are FastAPI dependencies specifically so tests
(tests/unit/test_api.py) and gate_p5.py can override them with a MockLLM
and a seeded temp database via `app.dependency_overrides`, without ever
touching a real provider or `data/recon.db`.
"""

from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from recon import db as recon_db
from recon.engine import hop2, pipeline, verifier
from recon.llm import qa
from recon.llm.client import LLMClient, create_llm_client
from recon.llm.tools import trace_order

# Same .env loading as cli.py — `recon.cli serve` imports this module, but
# api.py is also importable directly (e.g. `uvicorn recon.api:app`), so it
# loads .env itself rather than relying on cli.py having done it first.
load_dotenv()

app = FastAPI(title="RECON-4 Q&A")

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=_WEB_DIR, html=True), name="dashboard")


class AskRequest(BaseModel):
    question: str
    run_id: str | None = None  # P6: target a specific run instead of always the latest


class AskResponse(BaseModel):
    answer: str
    tool_calls: list[dict]
    # One server-computed line per tool call — what the Q&A console shows
    # between the question and the answer, so the retrieval is visible
    # rather than asserted (UI_SPEC §2.8). Defaulted, because /ask
    # predates it and a caller reading only `answer` must not break.
    tool_results: list[dict] = []
    record_ids: list[str]


class _UnconfiguredLLM:
    """Fallback when create_llm_client() can't even construct a provider
    (no API key, bad config) — CLAUDE.md rule 5: never load-bearing. Without
    this, a misconfigured provider raises during FastAPI's dependency
    resolution, before the /ask handler's own body ever runs, producing a
    raw 500 with a plain-text (non-JSON) response body."""

    def converse(self, messages: list[dict], tools: list[dict], system: str) -> dict:
        return {
            "stop_reason": "end_turn",
            "tool_calls": [],
            "text": "No LLM provider is configured (missing API key) — the Q&A agent can't run right now.",
        }

    def adjudicate(self, payload: dict) -> str:
        return "{}"

    def explain(self, evidence: dict) -> str:
        return "{}"


def get_llm_client() -> LLMClient:
    try:
        return create_llm_client()
    except Exception:
        return _UnconfiguredLLM()


def get_db_path() -> Path:
    return recon_db.DB_PATH


@app.post("/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    client: LLMClient = Depends(get_llm_client),
    db_path: Path = Depends(get_db_path),
) -> AskResponse:
    conn = recon_db.connect(db_path)
    try:
        result = qa.answer_question(conn, request.question, client, run_id=request.run_id)
        return AskResponse(**result)
    finally:
        conn.close()


def _resolve_llm_client(provider: str | None) -> LLMClient:
    """Same never-load-bearing fallback as get_llm_client(), but honoring an
    explicit provider override (POST /api/run's body) instead of always
    reading $RECON_LLM_PROVIDER."""
    try:
        return create_llm_client(provider)
    except Exception:
        return _UnconfiguredLLM()


def _reconstruction_evidence(
    conn: sqlite3.Connection, run_id: str, records: list[dict]
) -> tuple[dict | None, str | None, int | None]:
    """Best-effort: a proposed/accepted/rejected match_link touching any of
    this exception's records carries the full reconstruction table in its
    evidence column. Returns (evidence, link_id, link_hop).

    The link_id is what the exception queue opens the reconstruction
    viewer with (UI_SPEC §2.4), and the hop is what tells it whether
    there is a reconstruction to open at all: only a hop-2 link carries
    settlement arithmetic. The match found here is a link touching one of
    the exception's records — related to it, but not necessarily an
    explanation OF it (an UNSETTLED_IN_TRANSIT row, for instance, matches
    its own payment's hop-1 link), so callers must label it as the linked
    match's evidence rather than as the exception's own.

    Exceptions with no match_link at all (a genuine refusal —
    AMBIGUOUS_SETTLEMENT, UNEXPLAINED_BANK_CREDIT) return (None, None,
    None); as of migration 003 those carry their own
    `exceptions.evidence` instead, which callers prefer over this."""
    for r in records:
        row = conn.execute(
            "SELECT link_id, hop, evidence FROM match_link WHERE run_id = ? AND (id_a = ? OR id_b = ?) "
            "AND evidence IS NOT NULL LIMIT 1",
            (run_id, r["id"], r["id"]),
        ).fetchone()
        if row and row[2]:
            return json.loads(row[2]), row[0], row[1]
    return None, None, None


def _exception_evidence(
    conn: sqlite3.Connection, run_id: str, records: list[dict], stored: str | None
) -> tuple[dict | None, str | None, int | None]:
    """The evidence an exception row should report, the match_link id (if
    any) that can render it as a reconstruction, and that link's hop.
    hop2's refusals store their own evidence on the row (migration 003)
    and have no link; everything else is reconstructed from whichever link
    touched its records, exactly as before 003 existed."""
    link_evidence, link_id, link_hop = _reconstruction_evidence(conn, run_id, records)
    if stored:
        return json.loads(stored), link_id, link_hop
    return link_evidence, link_id, link_hop


def _hop2_reconstruction(conn: sqlite3.Connection, run_id: str, line_id: str) -> dict:
    """The full settlement reconstruction behind one hop-2 link, as the
    reconstruction viewer (UI_SPEC §2.3) renders it: the bank line, every
    gateway row claiming it in this run, and the two totals.

    `net_p` is recomputed here from the raw gw_payments columns via the
    same hop2._contribution_p the matcher used — not read back from a
    stored evidence blob, and never summed in the browser (UI_SPEC §0:
    the frontend never computes a number). Rows are ordered by link_id,
    which is hop2's own insertion order, so the viewer streams them in
    the order the matcher considered them.
    """
    bank_row = conn.execute(
        "SELECT line_id, value_date, credit_p, narration, utr_extracted FROM bank_lines WHERE line_id = ?",
        (line_id,),
    ).fetchone()
    bank_line = (
        {
            "line_id": bank_row[0],
            "value_date": bank_row[1],
            "credit_p": bank_row[2],
            "narration": bank_row[3],
            "utr_extracted": bank_row[4],
        }
        if bank_row
        else None
    )

    rows = []
    running_p = 0
    for payment_id, kind, method, amount_p, fee_p, gst_p, settlement_id, utr in conn.execute(
        "SELECT g.payment_id, g.kind, g.method, g.amount_p, g.fee_p, g.gst_p, g.settlement_id, g.utr "
        "FROM match_link m JOIN gw_payments g ON g.payment_id = m.id_a "
        "WHERE m.run_id = ? AND m.hop = 2 AND m.id_b = ? AND m.status IN ('accepted', 'proposed') "
        "ORDER BY m.link_id",
        (run_id, line_id),
    ):
        net_p = hop2._contribution_p(kind, amount_p, fee_p, gst_p)
        running_p += net_p
        rows.append(
            {
                "payment_id": payment_id,
                "kind": kind,
                "method": method,
                "amount_p": amount_p,
                "fee_p": fee_p,
                "gst_p": gst_p,
                "net_p": net_p,
                # The running total after this row, so the viewer can count
                # up as rows stream in without adding anything itself.
                "subtotal_p": running_p,
                # What reference this row carried, if any. A tier-2
                # reconstruction exists precisely because these are null —
                # the viewer says "no UTR recovered · no settlement id"
                # off these values rather than asserting it blind.
                "settlement_id": settlement_id,
                "utr": utr,
            }
        )

    reconstructed_p = running_p
    credit_p = bank_line["credit_p"] if bank_line else 0
    return {
        "bank_line": bank_line,
        "rows": rows,
        "reconstructed_p": reconstructed_p,
        "delta_p": reconstructed_p - credit_p,
    }


@app.get("/report")
def get_report(db_path: Path = Depends(get_db_path)) -> dict:
    conn = recon_db.connect(db_path)
    try:
        run_id = recon_db.latest_run_id(conn)
        if run_id is None:
            return {"error": "no completed run found — run `recon.cli run` first"}
        row = conn.execute(
            "SELECT seed, started_at, finished_at, llm_mode, metrics FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        seed, started_at, finished_at, llm_mode, metrics_json = row

        exceptions = []
        for (
            exc_id,
            code,
            severity,
            amount_at_risk_p,
            explanation,
            suggested_action,
            records_json,
            stored_evidence,
        ) in conn.execute(
            "SELECT exc_id, code, severity, amount_at_risk_p, explanation, suggested_action, records, evidence "
            "FROM exceptions WHERE run_id = ? AND status = 'open' ORDER BY amount_at_risk_p DESC",
            (run_id,),
        ):
            records = json.loads(records_json)
            evidence, evidence_link_id, evidence_link_hop = _exception_evidence(
                conn, run_id, records, stored_evidence
            )
            exceptions.append(
                {
                    "exc_id": exc_id,
                    "code": code,
                    "severity": severity,
                    "amount_at_risk_p": amount_at_risk_p,
                    "explanation": explanation,
                    "suggested_action": suggested_action,
                    "records": records,
                    "evidence": evidence,
                    "evidence_link_id": evidence_link_id,
                    "evidence_link_hop": evidence_link_hop,
                }
            )

        return {
            "run_id": run_id,
            "seed": seed,
            "llm_mode": llm_mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "metrics": json.loads(metrics_json),
            "exceptions": exceptions,
        }
    finally:
        conn.close()


# --- P6 supplement: run/streaming/reconstruction API ---------------------

# run_id -> the queue its background pipeline thread is writing events to.
# A run's queue is consumed (and popped) by whichever client GETs its
# /stream first — Queue.get() is destructive, so this is single-reader by
# construction; a second concurrent stream on the same run_id gets a 404
# ("already consumed") rather than silently splitting the event feed. That
# is a real limitation, acceptable for a single-viewer demo dashboard
# within P6's timebox — not something a multi-viewer product could ship.
# An entry is also left behind (and never cleaned up) if nobody ever opens
# the stream for a completed run; harmless for a demo-length process.
_run_queues: dict[str, "queue.Queue[dict | None]"] = {}


class RunRequest(BaseModel):
    seed: int = config.SEED
    llm_mode: str = "off"  # CLAUDE.md rule 5: off always works, no key required
    llm_provider: str | None = None  # anthropic|gemini; default $RECON_LLM_PROVIDER
    pace_ms: int = 0
    # Narration is ~17 extra LLM calls (one per open exception) and is what
    # makes an --llm on run take ~2 minutes instead of ~15 seconds. Off, the
    # templated explanations hop1/2/3 already wrote stand unchanged, so the
    # run console can demo live adjudication without the wait.
    narrate: bool = True


class RunResponse(BaseModel):
    run_id: str


@app.post("/api/run", response_model=RunResponse)
def start_run(request: RunRequest = RunRequest(), db_path: Path = Depends(get_db_path)) -> RunResponse:
    """Start a pipeline run in a background thread; return its run_id
    immediately so the caller can connect to GET /api/run/{run_id}/stream
    before the first event fires. Runs against whatever is currently loaded
    at db_path — this endpoint does not generate or load data itself."""
    run_id = pipeline.new_run_id(request.seed)
    q: "queue.Queue[dict | None]" = queue.Queue()
    _run_queues[run_id] = q
    llm_client = _resolve_llm_client(request.llm_provider) if request.llm_mode == "on" else None

    def worker() -> None:
        try:
            pipeline.run_pipeline(
                db_path=db_path,
                seed=request.seed,
                llm_mode=request.llm_mode,
                llm_client=llm_client,
                run_id=run_id,
                on_event=q.put,
                pace_ms=request.pace_ms,
                narrate=request.narrate,
            )
        except Exception as exc:
            # Never let a pipeline failure (including V5's ClearingControlFailure)
            # vanish silently — the stream's last event names it explicitly,
            # instead of the connection just going quiet.
            q.put({"kind": "error", "message": str(exc)})
        finally:
            q.put(None)  # sentinel: tells /stream the run is over

    threading.Thread(target=worker, daemon=True).start()
    return RunResponse(run_id=run_id)


@app.get("/api/run/{run_id}/stream")
async def stream_run(run_id: str) -> StreamingResponse:
    """SSE: one `data: {...}\\n\\n` chunk per match/exception event, in
    emission order, terminated by the worker thread's sentinel. Blocking
    `queue.Queue.get()` is run off the event loop via asyncio.to_thread so
    it doesn't stall other requests while waiting."""
    q = _run_queues.get(run_id)
    if q is None:
        raise HTTPException(
            status_code=404,
            detail=f"no active run stream for {run_id} — either it was never started via POST /api/run, "
            "or its stream was already consumed by another connection.",
        )

    async def event_source():
        try:
            while True:
                item = await asyncio.to_thread(q.get)
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _run_queues.pop(run_id, None)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/api/run/latest")
def get_latest_run(db_path: Path = Depends(get_db_path)) -> dict:
    """The most recently finished run, in the same shape as
    /api/run/{id}/metrics. Every screen except the run console needs "the
    run" without having started one this session (UI_SPEC §2.4/§2.6) —
    before this, they had no way to name a run id at all."""
    conn = recon_db.connect(db_path)
    try:
        run_id = recon_db.latest_run_id(conn)
        if run_id is None:
            raise HTTPException(status_code=404, detail="no completed run found — run POST /api/run first")
    finally:
        conn.close()
    return get_run_metrics(run_id, db_path)


@app.get("/api/run/{run_id}/metrics")
def get_run_metrics(run_id: str, db_path: Path = Depends(get_db_path)) -> dict:
    conn = recon_db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT seed, started_at, finished_at, llm_mode, metrics FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
        seed, started_at, finished_at, llm_mode, metrics_json = row
        return {
            "run_id": run_id,
            "seed": seed,
            "llm_mode": llm_mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": "finished" if finished_at else "running",
            "metrics": json.loads(metrics_json) if metrics_json else None,
        }
    finally:
        conn.close()


@app.get("/api/run/{run_id}/exceptions")
def get_run_exceptions(
    run_id: str,
    hop: int | None = None,
    code: str | None = None,
    severity: str | None = None,
    db_path: Path = Depends(get_db_path),
) -> dict:
    conn = recon_db.connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")

        query = (
            "SELECT exc_id, code, severity, hop, records, amount_at_risk_p, age_days, "
            "explanation, suggested_action, status, evidence FROM exceptions WHERE run_id = ?"
        )
        params: list = [run_id]
        if hop is not None:
            query += " AND hop = ?"
            params.append(hop)
        if code is not None:
            query += " AND code = ?"
            params.append(code)
        if severity is not None:
            query += " AND severity = ?"
            params.append(severity)
        query += " ORDER BY amount_at_risk_p DESC"

        exceptions = []
        for (
            exc_id,
            code_,
            severity_,
            hop_,
            records_json,
            amount_at_risk_p,
            age_days,
            explanation,
            suggested_action,
            status,
            stored_evidence,
        ) in conn.execute(query, params):
            records = json.loads(records_json)
            evidence, evidence_link_id, evidence_link_hop = _exception_evidence(
                conn, run_id, records, stored_evidence
            )
            exceptions.append(
                {
                    "exc_id": exc_id,
                    "code": code_,
                    "severity": severity_,
                    "hop": hop_,
                    "records": records,
                    "amount_at_risk_p": amount_at_risk_p,
                    "age_days": age_days,
                    "explanation": explanation,
                    "suggested_action": suggested_action,
                    "status": status,
                    "evidence": evidence,
                    "evidence_link_id": evidence_link_id,
                    "evidence_link_hop": evidence_link_hop,
                }
            )
        return {"run_id": run_id, "exceptions": exceptions}
    finally:
        conn.close()


@app.get("/api/match/{link_id}")
def get_match(link_id: str, db_path: Path = Depends(get_db_path)) -> dict:
    conn = recon_db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT link_id, run_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence "
            "FROM match_link WHERE link_id = ?",
            (link_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no such match_link: {link_id}")
        link_id_, run_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence_json = row
        result = {
            "link_id": link_id_,
            "run_id": run_id,
            "hop": hop,
            "src_a": src_a,
            "id_a": id_a,
            "src_b": src_b,
            "id_b": id_b,
            "tier": tier,
            "confidence": confidence,
            "status": status,
            "reason": reason,
            "evidence": json.loads(evidence_json) if evidence_json else None,
        }
        if hop == 2:
            result.update(_hop2_reconstruction(conn, run_id, id_b))
        return result
    finally:
        conn.close()


@app.get("/api/order/{order_id}/chain")
def get_order_chain(order_id: str, run_id: str | None = None, db_path: Path = Depends(get_db_path)) -> dict:
    """Thin wrapper around recon.llm.tools.trace_order — the SAME grounded
    lookup the Q&A agent's trace_order tool uses, exposed directly for a
    UI chain-explorer screen that doesn't need an LLM in the loop at all."""
    conn = recon_db.connect(db_path)
    try:
        result = trace_order(conn, order_id, run_id=run_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    finally:
        conn.close()


@app.get("/api/control/clearing")
def get_clearing_control(run_id: str | None = None, db_path: Path = Depends(get_db_path)) -> dict:
    """V5's residual_p (from gl_entries alone) vs exposure_p (from the
    signed exception inclusion map) — the same two numbers `recon.cli
    report` prints under "Clearing control", as JSON for the UI's control
    screen (P6 supplement §2.6)."""
    conn = recon_db.connect(db_path)
    try:
        if run_id is None:
            run_id = recon_db.latest_run_id(conn)
        if run_id is None:
            raise HTTPException(status_code=404, detail="no completed run found — run POST /api/run first")
        residual_p = verifier.compute_residual_p(conn)
        exposure_p, breakdown = verifier.compute_exposure_p(conn, run_id)

        # The T-account itself (UI_SPEC §2.6): every PG_RECEIVABLE line in
        # ledger order, with a running balance computed server-side. The
        # frontend never sums (UI_SPEC §0) and never subtracts — hence
        # difference_p too.
        entries = []
        balance_p = 0
        for voucher_no, entry_date, account, debit_p, credit_p, memo in conn.execute(
            "SELECT voucher_no, entry_date, account, debit_p, credit_p, memo FROM gl_entries "
            "WHERE account = 'PG_RECEIVABLE' ORDER BY entry_date, voucher_no, line_no"
        ):
            balance_p += debit_p - credit_p
            entries.append(
                {
                    "voucher_no": voucher_no,
                    "entry_date": entry_date,
                    "account": account,
                    "debit_p": debit_p,
                    "credit_p": credit_p,
                    "memo": memo,
                    "balance_p": balance_p,
                }
            )
        # Same table, same filter, two ways of adding it up — if these ever
        # disagree the bug is here, not in the data. Assert rather than ship
        # a T-account whose last line contradicts the control number
        # printed beneath it.
        assert balance_p == residual_p, (
            f"T-account closing balance {balance_p}p != compute_residual_p {residual_p}p "
            "— same gl_entries rows summed two ways must agree"
        )

        return {
            "run_id": run_id,
            "residual_p": residual_p,
            "exposure_p": exposure_p,
            "difference_p": residual_p - exposure_p,
            "balanced": residual_p == exposure_p,
            "breakdown": breakdown,
            "entries": entries,
        }
    finally:
        conn.close()


@app.post("/api/ask", response_model=AskResponse)
def api_ask(
    request: AskRequest,
    client: LLMClient = Depends(get_llm_client),
    db_path: Path = Depends(get_db_path),
) -> AskResponse:
    """Same grounded Q&A loop as POST /ask, namespaced under /api for the
    new frontend — identical behavior, kept as a separate route (rather
    than replacing /ask) so the existing dashboard and its tests are
    untouched."""
    conn = recon_db.connect(db_path)
    try:
        result = qa.answer_question(conn, request.question, client, run_id=request.run_id)
        return AskResponse(**result)
    finally:
        conn.close()
