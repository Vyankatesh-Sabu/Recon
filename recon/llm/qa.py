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

MAX_TOOL_CALLS = 4

SYSTEM_PROMPT = (
    "Answer only from tool results. Quote record IDs for every figure you "
    "state. If a tool returns nothing relevant, say plainly that the data "
    "does not show it — never guess. Never estimate or compute arithmetic "
    "yourself; every number must come from a tool call."
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

    Returns {"answer": str, "tool_calls": [{"name", "input"}, ...], "record_ids": [str, ...]}.
    """
    if run_id is None:
        run_id = latest_run_id(conn)

    messages: list[dict] = [{"role": "user", "content": question}]
    tool_calls_made: list[dict] = []
    record_ids: set[str] = set()

    while True:
        response = client.converse(messages, tools=TOOL_SCHEMAS, system=SYSTEM_PROMPT)

        if response["stop_reason"] != "tool_use" or len(tool_calls_made) >= MAX_TOOL_CALLS:
            answer = response.get("text") or "The data does not show enough to answer this."
            return {
                "answer": answer,
                "tool_calls": tool_calls_made,
                "record_ids": sorted(record_ids),
            }

        for call in response["tool_calls"]:
            if len(tool_calls_made) >= MAX_TOOL_CALLS:
                break
            tool_input = call.get("input", {})
            result = _execute_tool(conn, run_id, call["name"], tool_input)
            tool_calls_made.append({"name": call["name"], "input": tool_input})
            record_ids |= collect_record_ids(result)
            messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": call["id"], "name": call["name"], "input": tool_input}],
                }
            )
            messages.append(
                {
                    "role": "tool_result",
                    "content": {"tool_call_id": call["id"], "name": call["name"], "result": result},
                }
            )
