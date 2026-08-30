"""scorer.py — metrics vs ground truth (SPEC §7). Runs only when ground truth exists.

All formulas per SPEC §7. The D-02 rule falls out of the general
link_precision formula with no special-casing needed: ground_truth.json's
`links` deliberately has no entry for either D-02 bank line (P1's
apply_d02 removes them), so an accepted link on those records is, by
construction, not in truth_links and counts as a precision-reducing false
match; the AMBIGUOUS_SETTLEMENT exception is a normal ground_truth.exceptions
entry and counts toward exc_detection like any other.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _load_ground_truth(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _truth_link_set(truth: dict) -> set[tuple]:
    return {(link["hop"], tuple(link["a"]), tuple(link["b"])) for link in truth["links"]}


def score(conn: sqlite3.Connection, run_id: str, ground_truth_path: Path | str) -> dict:
    truth = _load_ground_truth(Path(ground_truth_path))
    truth_links = _truth_link_set(truth)
    truth_exceptions = truth["exceptions"]

    accepted = conn.execute(
        "SELECT hop, src_a, id_a, src_b, id_b, tier FROM match_link WHERE run_id = ? AND status = 'accepted'",
        (run_id,),
    ).fetchall()
    accepted_set = {(hop, (sa, ia), (sb, ib)) for hop, sa, ia, sb, ib, _tier in accepted}

    intersection = accepted_set & truth_links
    link_precision = len(intersection) / len(accepted_set) if accepted_set else 1.0
    link_recall = len(intersection) / len(truth_links) if truth_links else 1.0
    false_match_rate = 1.0 - link_precision  # headline number

    tier_histogram: dict[str, int] = {}
    for _hop, _sa, _ia, _sb, _ib, tier in accepted:
        tier_histogram[str(tier)] = tier_histogram.get(str(tier), 0) + 1

    # full_chain_rate — P2 scope: h1+h2 only (hop3 lands in P3 and will
    # extend this to the true 3-hop chain).
    hop1_accepted_orders = {ia for hop, _sa, ia, _sb, _ib, _t in accepted if hop == 1}
    hop2_accepted_payments = {ia for hop, _sa, ia, _sb, _ib, _t in accepted if hop == 2}
    truth_order_to_payment = {a[1]: b[1] for hop, a, b in truth_links if hop == 1}
    fully_chained = sum(
        1
        for order_id, payment_id in truth_order_to_payment.items()
        if order_id in hop1_accepted_orders and payment_id in hop2_accepted_payments
    )
    full_chain_rate = (
        fully_chained / len(truth_order_to_payment) if truth_order_to_payment else 1.0
    )

    # exception detection / code accuracy
    actual_exceptions = conn.execute(
        "SELECT code, records FROM exceptions WHERE run_id = ?", (run_id,)
    ).fetchall()
    actual_codes_by_record: dict[tuple[str, str], set[str]] = {}
    for code, records_json in actual_exceptions:
        for rec in json.loads(records_json):
            actual_codes_by_record.setdefault((rec["src"], rec["id"]), set()).add(code)

    correct_code = 0
    raised_and_expected = 0
    for exc in truth_exceptions:
        rec_keys = [(r["src"], r["id"]) for r in exc["records"]]
        raised_codes: set[str] = set()
        for k in rec_keys:
            raised_codes |= actual_codes_by_record.get(k, set())
        if raised_codes:
            raised_and_expected += 1
            if exc["code"] in raised_codes:
                correct_code += 1
    exc_detection = correct_code / len(truth_exceptions) if truth_exceptions else 1.0
    exc_code_accuracy = correct_code / raised_and_expected if raised_and_expected else 1.0

    # ₹ metrics (paise; format at display time only — CLAUDE.md rule 1)
    value_reconciled_p = 0
    for (line_id,) in conn.execute(
        "SELECT DISTINCT id_b FROM match_link WHERE run_id = ? AND status = 'accepted' AND hop = 2",
        (run_id,),
    ):
        row = conn.execute("SELECT credit_p FROM bank_lines WHERE line_id = ?", (line_id,)).fetchone()
        if row:
            value_reconciled_p += row[0]
    amount_at_risk_p = conn.execute(
        "SELECT COALESCE(SUM(amount_at_risk_p), 0) FROM exceptions "
        "WHERE run_id = ? AND status = 'open' AND severity != 'info'",
        (run_id,),
    ).fetchone()[0]

    exceptions_by_severity: dict[str, int] = {"critical": 0, "warn": 0, "info": 0}
    for severity, n in conn.execute(
        "SELECT severity, COUNT(*) FROM exceptions WHERE run_id = ? AND status = 'open' GROUP BY severity",
        (run_id,),
    ):
        exceptions_by_severity[severity] = n
    exceptions_open = sum(exceptions_by_severity.values())

    records_processed = sum(
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 — fixed table names
        for table in ("orders", "gw_payments", "bank_lines", "gl_entries")
    )

    # hop_match: accepted / proposed (attempted) at that hop — hop3 isn't
    # built yet, so "0/0" there for now.
    hop_match = {}
    for hop in (1, 2, 3):
        total_proposed = conn.execute(
            "SELECT COUNT(*) FROM match_link WHERE run_id = ? AND hop = ?", (run_id, hop)
        ).fetchone()[0]
        total_accepted = conn.execute(
            "SELECT COUNT(*) FROM match_link WHERE run_id = ? AND hop = ? AND status = 'accepted'",
            (run_id, hop),
        ).fetchone()[0]
        hop_match[f"h{hop}"] = f"{total_accepted}/{total_proposed}"

    return {
        "link_precision": link_precision,
        "link_recall": link_recall,
        "false_match_rate": false_match_rate,
        "full_chain_rate": full_chain_rate,
        "exc_detection": exc_detection,
        "exc_code_accuracy": exc_code_accuracy,
        "tier_histogram": tier_histogram,
        "hop_match": hop_match,
        "llm_calls": {"total": 0, "accepted": 0, "rejected": 0, "abstained": 0},
        "records_processed": records_processed,
        "exceptions": {"open": exceptions_open, **exceptions_by_severity},
        "amount_at_risk_p": amount_at_risk_p,
        "value_reconciled_p": value_reconciled_p,
    }
