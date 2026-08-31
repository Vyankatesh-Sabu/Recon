"""hop1.py — orders ↔ gateway captures, tier 1 only (SPEC §6.2).

Writes `match_link` rows with status='proposed' ONLY — hop1 never accepts a
link (CLAUDE.md rule 7: only verifier.py may). Exceptions are written with a
templated explanation/suggested_action (no LLM). Reads orders/gw_payments
straight off the given connection; the caller (a future pipeline.py, or a
test) is responsible for the run_id and for loading the DB first.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

import config
from recon.engine.events import OnEvent

# SPEC §5.3: every code hop1 can raise is severity "warn".
_SEVERITY = "warn"

_EXPLANATIONS = {
    "ORPHAN_ORDER": "Order {order_id} is confirmed but has no linked gateway capture.",
    "ORPHAN_PAYMENT": "Gateway capture {payment_id} references no known order.",
    "DUPLICATE_PAYMENT": "Order {order_id} has multiple captures; {payment_id} is a duplicate of the first, {primary_id}.",
    "PARTIAL_CAPTURE_MISMATCH": "Order {order_id}'s capture {payment_id} amount differs from the order amount by {delta_p}p.",
}
_SUGGESTED_ACTIONS = {
    "ORPHAN_ORDER": "Investigate gateway for a missing or failed capture; confirm with customer if unpaid.",
    "ORPHAN_PAYMENT": "Confirm capture {payment_id} against known orders (payment link / manual sale) or write off.",
    "DUPLICATE_PAYMENT": "Refund the duplicate capture {payment_id}.",
    "PARTIAL_CAPTURE_MISMATCH": "Verify with customer; collect the shortfall or refund the difference.",
}


@dataclass
class Hop1Stats:
    orders_seen: int = 0
    cod_skipped: int = 0
    links_proposed: int = 0
    orphan_orders: int = 0
    orphan_payments: int = 0
    duplicate_payments: int = 0
    partial_mismatches: int = 0
    exceptions_by_code: dict[str, int] = field(default_factory=dict)


def _age_days(event_date: date, as_of: date = config.DATE_TO) -> int:
    return max((as_of - event_date).days, 0)


def _insert_link(conn, run_id, seq, order_id, payment_id, confidence, reason, evidence) -> None:
    conn.execute(
        "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence, run_id) "
        "VALUES (?, 1, 'orders', ?, 'gw', ?, 1, ?, 'proposed', ?, ?, ?)",
        (f"{run_id}-ML-{seq:04d}", order_id, payment_id, confidence, reason, json.dumps(evidence), run_id),
    )


def _insert_exception(
    conn, run_id, seq, code, records, amount_at_risk_p, event_date, explanation, suggested_action
) -> None:
    conn.execute(
        "INSERT INTO exceptions "
        "(exc_id, run_id, code, severity, hop, records, amount_at_risk_p, age_days, explanation, suggested_action, status) "
        "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'open')",
        (
            f"{run_id}-EXC-{seq:04d}",
            run_id,
            code,
            _SEVERITY,
            json.dumps(records),
            amount_at_risk_p,
            _age_days(event_date),
            explanation,
            suggested_action,
        ),
    )


def run_hop1(conn: sqlite3.Connection, run_id: str, on_event: OnEvent | None = None) -> Hop1Stats:
    """Run hop-1 order<->capture matching (SPEC §6.2) and commit the results."""
    stats = Hop1Stats()
    link_seq = 0
    exc_seq = 0

    def add_exception(code: str, records: list[dict], amount_at_risk_p: int, event_date: date, **fmt) -> None:
        nonlocal exc_seq
        exc_seq += 1
        explanation = _EXPLANATIONS[code].format(**fmt)
        suggested_action = _SUGGESTED_ACTIONS[code].format(**fmt)
        exc_id = f"{run_id}-EXC-{exc_seq:04d}"
        _insert_exception(conn, run_id, exc_seq, code, records, amount_at_risk_p, event_date, explanation, suggested_action)
        stats.exceptions_by_code[code] = stats.exceptions_by_code.get(code, 0) + 1
        if on_event is not None:
            on_event(
                {
                    "kind": "exception",
                    "hop": 1,
                    "exc_id": exc_id,
                    "code": code,
                    "severity": _SEVERITY,
                    "amount_at_risk_p": amount_at_risk_p,
                    "records": records,
                }
            )

    orders = conn.execute("SELECT order_id, amount_p, method, status, created_on FROM orders").fetchall()
    captures = conn.execute(
        "SELECT payment_id, order_id, amount_p, captured_on FROM gw_payments WHERE kind = 'capture'"
    ).fetchall()

    order_ids = {row[0] for row in orders}
    captures_by_order: dict[str, list[tuple]] = {}
    orphan_captures: list[tuple] = []
    for payment_id, order_id, amount_p, captured_on in captures:
        if order_id is None or order_id not in order_ids:
            orphan_captures.append((payment_id, order_id, amount_p, captured_on))
        else:
            captures_by_order.setdefault(order_id, []).append((payment_id, order_id, amount_p, captured_on))

    for payment_id, order_id, amount_p, captured_on in orphan_captures:
        stats.orphan_payments += 1
        add_exception(
            "ORPHAN_PAYMENT",
            [{"src": "gw", "id": payment_id}],
            amount_p,
            date.fromisoformat(captured_on),
            payment_id=payment_id,
        )

    for order_id, order_amount_p, method, status, created_on in orders:
        stats.orders_seen += 1
        if method == "cod" or status != "confirmed":
            stats.cod_skipped += 1
            continue

        caps = sorted(captures_by_order.get(order_id, []), key=lambda c: (c[3], c[0]))  # (captured_on, payment_id)
        if not caps:
            stats.orphan_orders += 1
            add_exception(
                "ORPHAN_ORDER",
                [{"src": "orders", "id": order_id}],
                order_amount_p,
                date.fromisoformat(created_on),
                order_id=order_id,
            )
            continue

        primary_id, _, primary_amount_p, primary_captured_on = caps[0]
        link_seq += 1
        delta_p = order_amount_p - primary_amount_p
        exact = delta_p == 0
        _insert_link(
            conn,
            run_id,
            link_seq,
            order_id,
            primary_id,
            confidence=1.0,
            reason="exact_amount_match" if exact else "amount_mismatch",
            evidence={
                "order_amount_p": order_amount_p,
                "capture_amount_p": primary_amount_p,
                "delta_p": delta_p,
            },
        )
        stats.links_proposed += 1

        if not exact:
            stats.partial_mismatches += 1
            add_exception(
                "PARTIAL_CAPTURE_MISMATCH",
                [{"src": "orders", "id": order_id}, {"src": "gw", "id": primary_id}],
                abs(delta_p),
                date.fromisoformat(primary_captured_on),
                order_id=order_id,
                payment_id=primary_id,
                delta_p=abs(delta_p),
            )

        for dup_id, _, dup_amount_p, dup_captured_on in caps[1:]:
            stats.duplicate_payments += 1
            add_exception(
                "DUPLICATE_PAYMENT",
                [{"src": "gw", "id": dup_id}],
                dup_amount_p,
                date.fromisoformat(dup_captured_on),
                order_id=order_id,
                payment_id=dup_id,
                primary_id=primary_id,
            )

    conn.commit()
    return stats
