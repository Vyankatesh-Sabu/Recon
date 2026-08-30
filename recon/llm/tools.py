"""tools.py — the four Q&A tools (SPEC §9.1): plain Python functions, SQL inside.

Every tool takes `conn` and an explicit `run_id` (defaults to the latest
finished run via `recon.db.latest_run_id` when omitted) — the LLM sees only
JSON-serializable dicts back, never a raw table or a live connection.
`cash_position.unreconciled_p` intentionally calls the SAME function V5 uses
(`verifier.compute_exposure_p`) rather than re-deriving the inclusion map a
second time — that would risk the two drifting apart silently. The "third
independent surface" this tool proves is a third *access path* (ask the Q&A
agent, not just read the report or trust the pipeline's own abort check),
not a third reimplementation of the one authoritative inclusion map.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import config
from recon import busdays, db as recon_db, moneymath
from recon.engine import verifier

_ID_KEYS = {
    "order_id",
    "payment_id",
    "line_id",
    "voucher_no",
    "exc_id",
    "batch",
    "id_a",
    "id_b",
    "id",  # exceptions' `records: [{"src":..., "id":...}]` shape
    "customer",
}
# Keys whose value is a LIST of plain ID strings (not a list of dicts) —
# e.g. trace_order's "vouchers": ["V-...-CAP", "V-...-SETL"].
_ID_LIST_KEYS = {"vouchers"}


def collect_record_ids(obj: object) -> set[str]:
    """Recursively pull every value stored under a known ID-shaped key out
    of a tool result — used by qa.py to report `record_ids` for a question,
    without guessing at string patterns."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _ID_KEYS and isinstance(value, str) and value:
                found.add(value)
            elif key in _ID_LIST_KEYS and isinstance(value, list):
                found |= {v for v in value if isinstance(v, str) and v}
            found |= collect_record_ids(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= collect_record_ids(item)
    return found


def _resolve_run_id(conn: sqlite3.Connection, run_id: str | None) -> str | None:
    return run_id or recon_db.latest_run_id(conn)


def _contribution_p(kind: str, amount_p: int, fee_p: int, gst_p: int) -> int:
    return moneymath.net_p(amount_p, fee_p, gst_p) if kind == "capture" else amount_p


def _hop_status(conn: sqlite3.Connection, run_id: str, hop: int, src_a: str, id_a: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM match_link WHERE run_id = ? AND hop = ? AND src_a = ? AND id_a = ?",
        (run_id, hop, src_a, id_a),
    ).fetchone()
    return row[0] if row else None


def _exceptions_for_records(conn: sqlite3.Connection, run_id: str, keys: list[tuple[str, str]]) -> list[dict]:
    out = []
    seen_exc_ids: set[str] = set()
    for code, records_json, exc_id, severity, amount_at_risk_p, explanation, suggested_action, status in conn.execute(
        "SELECT code, records, exc_id, severity, amount_at_risk_p, explanation, suggested_action, status "
        "FROM exceptions WHERE run_id = ?",
        (run_id,),
    ):
        records = json.loads(records_json)
        if any((r["src"], r["id"]) in keys for r in records) and exc_id not in seen_exc_ids:
            seen_exc_ids.add(exc_id)
            out.append(
                {
                    "exc_id": exc_id,
                    "code": code,
                    "severity": severity,
                    "amount_at_risk_p": amount_at_risk_p,
                    "explanation": explanation,
                    "suggested_action": suggested_action,
                    "status": status,
                }
            )
    return out


def trace_order(conn: sqlite3.Connection, order_id: str, run_id: str | None = None) -> dict:
    """order -> capture -> settlement -> GL, full chain, plus hop statuses and exceptions."""
    run_id = _resolve_run_id(conn, run_id)
    order_row = conn.execute(
        "SELECT order_id, customer, amount_p, method, status, created_on FROM orders WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    if order_row is None:
        return {"error": f"no such order: {order_id}"}
    order = dict(
        zip(("order_id", "customer", "amount_p", "method", "status", "created_on"), order_row)
    )

    hops = {"h1": None, "h2": None, "h3": None}
    settlement = {"batch": None, "utr": None, "bank_line": None}
    gl_vouchers: list[str] = []
    capture = None

    capture_row = conn.execute(
        "SELECT payment_id, kind, amount_p, fee_p, gst_p, method, captured_on, settlement_id, utr "
        "FROM gw_payments WHERE order_id = ? AND kind = 'capture'",
        (order_id,),
    ).fetchone()
    if capture_row is not None and run_id is not None:
        capture = dict(
            zip(
                ("payment_id", "kind", "amount_p", "fee_p", "gst_p", "method", "captured_on", "settlement_id", "utr"),
                capture_row,
            )
        )
        hops["h1"] = _hop_status(conn, run_id, 1, "orders", order_id)
        settlement["batch"] = capture["settlement_id"]
        settlement["utr"] = capture["utr"]

        cap_voucher = conn.execute(
            "SELECT DISTINCT voucher_no FROM gl_entries WHERE entry_date = ? AND account = 'PG_RECEIVABLE' AND debit_p > 0",
            (capture["captured_on"],),
        ).fetchone()
        if cap_voucher:
            gl_vouchers.append(cap_voucher[0])

        if capture["utr"]:
            bank_line = conn.execute(
                "SELECT line_id FROM bank_lines WHERE utr_extracted = ?", (capture["utr"],)
            ).fetchone()
            if bank_line:
                settlement["bank_line"] = bank_line[0]
                hops["h2"] = _hop_status(conn, run_id, 2, "gw", capture["payment_id"])
                hops["h3"] = _hop_status(conn, run_id, 3, "bank", bank_line[0])
                voucher = conn.execute(
                    "SELECT id_b FROM match_link WHERE run_id = ? AND hop = 3 AND id_a = ?",
                    (run_id, bank_line[0]),
                ).fetchone()
                if voucher:
                    gl_vouchers.append(voucher[0])

    keys = [("orders", order_id)]
    if capture:
        keys.append(("gw", capture["payment_id"]))
    exceptions = _exceptions_for_records(conn, run_id, keys) if run_id else []

    return {
        "order": order,
        "capture": capture,
        "settlement": settlement,
        "gl": {"vouchers": gl_vouchers},
        "hops": hops,
        "exceptions": exceptions,
    }


def explain_settlement(conn: sqlite3.Connection, ref: str, run_id: str | None = None) -> dict:
    """ref = a bank line's UTR, its own line_id, or a gateway settlement_id —
    constituent rows, fee/GST subtotals, the reconstruction table, and the
    GL voucher (if hop3 found one)."""
    run_id = _resolve_run_id(conn, run_id)
    ref_norm = ref.strip().upper()

    bank_line = conn.execute(
        "SELECT line_id, value_date, narration, credit_p FROM bank_lines WHERE line_id = ? OR utr_extracted = ?",
        (ref, ref_norm),
    ).fetchone()
    if bank_line is None:
        sibling = conn.execute(
            "SELECT utr FROM gw_payments WHERE settlement_id = ? AND utr IS NOT NULL LIMIT 1", (ref_norm,)
        ).fetchone()
        if sibling and sibling[0]:
            bank_line = conn.execute(
                "SELECT line_id, value_date, narration, credit_p FROM bank_lines WHERE utr_extracted = ?",
                (sibling[0],),
            ).fetchone()
    if bank_line is None:
        return {"error": f"no settlement found for ref={ref!r}"}
    line_id, value_date, narration, credit_p = bank_line

    rows = []
    fee_total = gst_total = subtotal = 0
    link_rows = (
        conn.execute(
            "SELECT id_a, status, tier FROM match_link WHERE run_id = ? AND hop = 2 AND id_b = ?",
            (run_id, line_id),
        ).fetchall()
        if run_id
        else []
    )
    for payment_id, link_status, tier in link_rows:
        p = conn.execute(
            "SELECT kind, amount_p, fee_p, gst_p FROM gw_payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        kind, amount_p, fee_p, gst_p = p
        net_p = _contribution_p(kind, amount_p, fee_p, gst_p)
        rows.append(
            {
                "payment_id": payment_id,
                "kind": kind,
                "amount_p": amount_p,
                "fee_p": fee_p,
                "gst_p": gst_p,
                "net_p": net_p,
                "link_status": link_status,
                "tier": tier,
            }
        )
        if kind == "capture":
            fee_total += fee_p
            gst_total += gst_p
        subtotal += net_p

    voucher_row = (
        conn.execute(
            "SELECT id_b FROM match_link WHERE run_id = ? AND hop = 3 AND id_a = ?", (run_id, line_id)
        ).fetchone()
        if run_id
        else None
    )

    result = {
        "bank_line": {"line_id": line_id, "value_date": value_date, "narration": narration, "credit_p": credit_p},
        "rows": rows,
        "fee_total_p": fee_total,
        "gst_total_p": gst_total,
        "subtotal_p": subtotal,
        "delta_p": subtotal - credit_p,
        "gl_voucher": voucher_row[0] if voucher_row else None,
    }
    if not rows and run_id:
        exc = _exceptions_for_records(conn, run_id, [("bank", line_id)])
        result["unresolved_reason"] = exc[0] if exc else None
    return result


def list_exceptions(
    conn: sqlite3.Connection,
    hop: int | None = None,
    code: str | None = None,
    min_amount_p: int | None = None,
    run_id: str | None = None,
) -> list[dict]:
    """Open exceptions, ordered by ₹ at risk descending, optionally filtered."""
    run_id = _resolve_run_id(conn, run_id)
    if run_id is None:
        return []
    query = (
        "SELECT exc_id, code, severity, hop, records, amount_at_risk_p, age_days, "
        "explanation, suggested_action, status FROM exceptions WHERE run_id = ? AND status = 'open'"
    )
    params: list = [run_id]
    if hop is not None:
        query += " AND hop = ?"
        params.append(hop)
    if code is not None:
        query += " AND code = ?"
        params.append(code)
    if min_amount_p is not None:
        query += " AND amount_at_risk_p >= ?"
        params.append(min_amount_p)
    query += " ORDER BY amount_at_risk_p DESC"

    return [
        {
            "exc_id": r[0],
            "code": r[1],
            "severity": r[2],
            "hop": r[3],
            "records": json.loads(r[4]),
            "amount_at_risk_p": r[5],
            "age_days": r[6],
            "explanation": r[7],
            "suggested_action": r[8],
            "status": r[9],
        }
        for r in conn.execute(query, params).fetchall()
    ]


def cash_position(conn: sqlite3.Connection, as_of: str, run_id: str | None = None) -> dict:
    """{cleared_p, in_transit:[{batch, expected_date, net_p}], disputed_p, unreconciled_p}.

    unreconciled_p == verifier.compute_exposure_p's number exactly — see
    module docstring for why this calls that function rather than
    re-deriving the inclusion map here.
    """
    run_id = _resolve_run_id(conn, run_id)
    as_of_date = date.fromisoformat(as_of)

    cleared_p = 0
    if run_id:
        for (line_id,) in conn.execute(
            "SELECT DISTINCT id_b FROM match_link WHERE run_id = ? AND hop = 2 AND status = 'accepted'", (run_id,)
        ):
            row = conn.execute(
                "SELECT credit_p, value_date FROM bank_lines WHERE line_id = ?", (line_id,)
            ).fetchone()
            if row and date.fromisoformat(row[1]) <= as_of_date:
                cleared_p += row[0]

    in_transit: list[dict] = []
    by_batch: dict[str, list[tuple]] = {}
    for payment_id, kind, amount_p, fee_p, gst_p, captured_on, settlement_id in conn.execute(
        "SELECT payment_id, kind, amount_p, fee_p, gst_p, captured_on, settlement_id "
        "FROM gw_payments WHERE utr IS NULL AND settlement_id IS NOT NULL"
    ):
        by_batch.setdefault(settlement_id, []).append((kind, amount_p, fee_p, gst_p, captured_on))
    for batch_id, batch_rows in sorted(by_batch.items()):
        earliest = min(date.fromisoformat(r[4]) for r in batch_rows)
        expected_settle = busdays.add_bdays(earliest, config.SETTLEMENT_LAG_BDAYS)
        if expected_settle <= as_of_date:
            continue
        net_p = sum(_contribution_p(kind, amount_p, fee_p, gst_p) for kind, amount_p, fee_p, gst_p, _d in batch_rows)
        in_transit.append({"batch": batch_id, "expected_date": expected_settle.isoformat(), "net_p": net_p})

    disputed_p = 0
    if run_id:
        disputed_p = conn.execute(
            "SELECT COALESCE(SUM(amount_at_risk_p), 0) FROM exceptions "
            "WHERE run_id = ? AND status = 'open' AND code = 'CHARGEBACK_UNRESOLVED'",
            (run_id,),
        ).fetchone()[0]

    unreconciled_p = verifier.compute_exposure_p(conn, run_id)[0] if run_id else 0

    return {
        "as_of": as_of,
        "cleared_p": cleared_p,
        "in_transit": sorted(in_transit, key=lambda e: e["expected_date"]),
        "disputed_p": disputed_p,
        "unreconciled_p": unreconciled_p,
    }
