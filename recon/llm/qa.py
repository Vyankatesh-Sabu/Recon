"""qa.py — grounded, tool-calling Q&A loop (SPEC §9.2).

System prompt (verbatim intent): answer only from tool results, quote
record IDs for every figure, say plainly when the data doesn't show
something, never estimate or compute arithmetic — call a tool. Standard
tool-use loop, max 4 tool calls per question. The loop itself is provider-
agnostic — it only ever talks to `LLMClient.converse()`'s normalized shape
(recon/llm/client.py), never a provider SDK directly.
"""

from __future__ import annotations

import sqlite3

from recon.db import latest_run_id
from recon.llm.client import LLMClient
from recon.llm.tools import cash_position, collect_record_ids, explain_settlement, list_exceptions, trace_order
from recon.moneymath import format_rupees

MAX_TOOL_CALLS = 4

SYSTEM_PROMPT = (
    "Answer only from tool results. Quote record IDs for every figure you "
    "state. If a tool returns nothing relevant, say plainly that the data "
    "does not show it — never guess. Never estimate or compute arithmetic "
    "yourself; every number must come from a tool call. Write plain prose: "
    "no markdown headings, bullets, bold or backticks — the answer is "
    "rendered as text, so formatting characters appear literally."
)

TOOL_SCHEMAS = [
    {
        "name": "trace_order",
        "description": (
            "Trace one order end to end: its capture, settlement (batch/UTR/bank line), "
            "GL vouchers, hop1/hop2/hop3 statuses, and any exceptions referencing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "explain_settlement",
        "description": (
            "Explain one settlement by its UTR, bank line id, or gateway settlement id: "
            "constituent gateway rows, fee/GST subtotals, the reconstruction table, and the GL voucher."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "list_exceptions",
        "description": "List open exceptions ordered by ₹ at risk descending, optionally filtered by hop, code, or a minimum amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hop": {"type": "integer"},
                "code": {"type": "string"},
                "min_amount_p": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "cash_position",
        "description": (
            "Cash position as of a given ISO date: cleared_p, in-transit batches with "
            "expected settlement dates, disputed_p (chargebacks), and unreconciled_p."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"as_of": {"type": "string", "description": "ISO date, e.g. 2026-08-14"}},
            "required": ["as_of"],
        },
    },
]

_TOOL_FUNCS = {
    "trace_order": trace_order,
    "explain_settlement": explain_settlement,
    "list_exceptions": list_exceptions,
    "cash_position": cash_position,
}


def _summarize(name: str, result: object) -> str:
    """A one-line, human-readable summary of what a tool returned, for the
    Q&A console's visible call log (UI_SPEC §2.8).

    Computed here, on the server, from the tool's own output — the console
    shows what the retrieval actually returned, and the frontend is not
    asked to add up or interpret a result blob (UI_SPEC §0). Never raises:
    a summary is a display convenience, and failing to render one must not
    cost the user their answer.
    """
    try:
        if isinstance(result, dict) and result.get("error"):
            return str(result["error"])
        if name == "list_exceptions" and isinstance(result, list):
            # "At risk" excludes info severity, exactly as the scorer and the
            # report define it (recon/scoring/scorer.py: severity != 'info').
            # Summing all severities here would put a different number on
            # this screen than on the metrics band for the same run — the
            # in-transit batches are unsettled, not at risk.
            at_risk = sum(e.get("amount_at_risk_p", 0) for e in result if e.get("severity") != "info")
            critical = sum(1 for e in result if e.get("severity") == "critical")
            info = sum(1 for e in result if e.get("severity") == "info")
            return (
                f"{len(result)} open exceptions, {format_rupees(at_risk)} at risk, "
                f"{critical} critical, {info} informational"
            )
        if name == "explain_settlement" and isinstance(result, dict):
            rows = result.get("rows") or []
            captures = sum(1 for r in rows if r.get("kind") == "capture")
            others = len(rows) - captures
            bank_line = (result.get("bank_line") or {}).get("line_id", "?")
            return (
                f"{bank_line}: {captures} captures, {others} other rows, "
                f"net {format_rupees(result.get('subtotal_p', 0))}, "
                f"delta {format_rupees(result.get('delta_p', 0))}"
            )
        if name == "trace_order" and isinstance(result, dict):
            hops = result.get("hops") or {}
            done = [h for h, status in hops.items() if status == "accepted"]
            return (
                f"{(result.get('order') or {}).get('order_id', '?')}: "
                f"{len(done)}/{len(hops) or 3} hops accepted, "
                f"{len(result.get('exceptions') or [])} exceptions"
            )
        if name == "cash_position" and isinstance(result, dict):
            return (
                f"cleared {format_rupees(result.get('cleared_p', 0))}, "
                f"{len(result.get('in_transit') or [])} batches in transit, "
                f"unreconciled {format_rupees(result.get('unreconciled_p', 0))}"
            )
        if isinstance(result, list):
            return f"{len(result)} rows"
        return "no result"
    except Exception:  # a summary must never cost the user their answer
        return "result returned"


def _execute_tool(conn: sqlite3.Connection, run_id: str | None, name: str, tool_input: dict) -> dict:
    func = _TOOL_FUNCS.get(name)
    if func is None:
        return {"error": f"unknown tool: {name!r}"}
    try:
        return func(conn, run_id=run_id, **tool_input)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}


def answer_question(
    conn: sqlite3.Connection, question: str, client: LLMClient, run_id: str | None = None
) -> dict:
    """Answer one question via the grounded tool-calling loop.

    Returns {"answer": str, "tool_calls": [{"name", "input"}, ...],
    "tool_results": [{"name", "summary"}, ...], "record_ids": [str, ...]}.

    `tool_results` is what makes the Q&A console's retrieval visible
    (UI_SPEC §2.8): one server-computed line per call, so the screen can
    show that the model looked something up rather than recalled it.
    """
    if run_id is None:
        run_id = latest_run_id(conn)

    messages: list[dict] = [{"role": "user", "content": question}]
    tool_calls_made: list[dict] = []
    tool_results: list[dict] = []
    record_ids: set[str] = set()

    while True:
        try:
            response = client.converse(messages, tools=TOOL_SCHEMAS, system=SYSTEM_PROMPT)
        except Exception as exc:
            # CLAUDE.md rule 5: never load-bearing. A real provider can raise
            # for reasons unrelated to the question (no API key, network
            # error) — that must degrade to an honest answer, not crash the
            # endpoint (same policy as recon/llm/adjudicator.py's retry loop).
            return {
                "answer": f"Could not reach the LLM to answer this ({exc}). The data itself is still queryable via the tools directly.",
                "tool_calls": tool_calls_made,
                "tool_results": tool_results,
                "record_ids": sorted(record_ids),
            }

        if response["stop_reason"] != "tool_use" or len(tool_calls_made) >= MAX_TOOL_CALLS:
            answer = response.get("text") or "The data does not show enough to answer this."
            return {
                "answer": answer,
                "tool_calls": tool_calls_made,
                "tool_results": tool_results,
                "record_ids": sorted(record_ids),
            }

        # One assistant message for the whole model turn, then one
        # tool_result per call — not an assistant message per call. A turn
        # with two function calls is one turn, and Gemini rejects a replay
        # that splits it: the provider's own raw parts (which carry the
        # thought_signature it requires back) describe the turn as a whole,
        # so attaching them to each call would resend them N times.
        turn_calls = response["tool_calls"][: MAX_TOOL_CALLS - len(tool_calls_made)]
        if not turn_calls:
            return {
                "answer": response.get("text") or "The data does not show enough to answer this.",
                "tool_calls": tool_calls_made,
                "tool_results": tool_results,
                "record_ids": sorted(record_ids),
            }

        assistant_message: dict = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("input", {})}
                for call in turn_calls
            ],
        }
        # Provider-specific, ignored by every backend that doesn't set it
        # (AnthropicLLM reads only role/content). Gemini requires its own
        # parts back verbatim on the next turn — see gemini_llm.converse.
        if response.get("raw_parts"):
            assistant_message["raw_parts"] = response["raw_parts"]
        messages.append(assistant_message)

        for call in turn_calls:
            tool_input = call.get("input", {})
            result = _execute_tool(conn, run_id, call["name"], tool_input)
            tool_calls_made.append({"name": call["name"], "input": tool_input})
            tool_results.append({"name": call["name"], "summary": _summarize(call["name"], result)})
            record_ids |= collect_record_ids(result)
            messages.append(
                {
                    "role": "tool_result",
                    "content": {"tool_call_id": call["id"], "name": call["name"], "result": result},
                }
            )
