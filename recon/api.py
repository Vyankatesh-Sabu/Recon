"""api.py — FastAPI app exposing the Q&A endpoint (SPEC §9) and dashboard data (SPEC §10).

POST /ask {"question": str} -> {"answer": str, "tool_calls": [...], "record_ids": [...]}.
GET  /report -> the latest run's metrics + full open-exception list, each
    with its reconstruction evidence when a match_link happens to carry one
    (tier1/tier2/hop3 links do; AMBIGUOUS_SETTLEMENT/UNEXPLAINED_BANK_CREDIT
    never got one in the first place — they're refusals, nothing to attach
    it to — so `evidence` is null there; the exception's own `explanation`
    already says why).
web/index.html is mounted at /dashboard (plain HTML + fetch, SPEC §10).

The LLM client and DB path are FastAPI dependencies specifically so tests
(tests/unit/test_api.py) and gate_p5.py can override them with a MockLLM
and a seeded temp database via `app.dependency_overrides`, without ever
touching a real provider or `data/recon.db`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from recon import db as recon_db
from recon.llm import qa
from recon.llm.client import LLMClient, create_llm_client

app = FastAPI(title="RECON-4 Q&A")

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=_WEB_DIR, html=True), name="dashboard")


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    tool_calls: list[dict]
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
        result = qa.answer_question(conn, request.question, client)
        return AskResponse(**result)
    finally:
        conn.close()


def _reconstruction_evidence(conn: sqlite3.Connection, run_id: str, records: list[dict]) -> dict | None:
    """Best-effort: a proposed/accepted/rejected match_link touching any of
    this exception's records carries the full reconstruction table in its
    evidence column. Exceptions with no match_link at all (a genuine
    refusal — AMBIGUOUS_SETTLEMENT, UNEXPLAINED_BANK_CREDIT) return None."""
    for r in records:
        row = conn.execute(
            "SELECT evidence FROM match_link WHERE run_id = ? AND (id_a = ? OR id_b = ?) "
            "AND evidence IS NOT NULL LIMIT 1",
            (run_id, r["id"], r["id"]),
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    return None


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
        for exc_id, code, severity, amount_at_risk_p, explanation, suggested_action, records_json in conn.execute(
            "SELECT exc_id, code, severity, amount_at_risk_p, explanation, suggested_action, records "
            "FROM exceptions WHERE run_id = ? AND status = 'open' ORDER BY amount_at_risk_p DESC",
            (run_id,),
        ):
            records = json.loads(records_json)
            exceptions.append(
                {
                    "exc_id": exc_id,
                    "code": code,
                    "severity": severity,
                    "amount_at_risk_p": amount_at_risk_p,
                    "explanation": explanation,
                    "suggested_action": suggested_action,
                    "records": records,
                    "evidence": _reconstruction_evidence(conn, run_id, records),
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
