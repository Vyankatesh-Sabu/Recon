"""hop2.py — gateway batches ↔ bank lines (SPEC §6.3).

Writes `match_link` rows with status='proposed' ONLY — never accepted
(CLAUDE.md rule 7). Every reconstruction attempt (tier-1 match, tier-1
fee-variance, tier-2 unique, tier-2 ambiguous) builds a full evidence dict
(per-row net, subtotal, target, delta). For proposed links it's persisted in
`match_link.evidence`; the refused/no-link cases (ambiguous, unexplained-
credit) have nowhere to persist it (no evidence column on `exceptions`, and
no link should exist to hang it on for an ambiguous case we refuse to pick),
so `run_hop2` also returns every attempt's evidence on `Hop2Stats.evidence_log`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

import config
from recon import busdays, moneymath
from recon.engine.subsetsum import Multiple, NoSolution, Unique, reconstruct

_SEVERITY = {
    "FEE_VARIANCE": "warn",
    "AMBIGUOUS_SETTLEMENT": "critical",
    "UNEXPLAINED_BANK_CREDIT": "critical",
    "MISSING_IN_BANK": "critical",
    "UNSETTLED_IN_TRANSIT": "info",
}


@dataclass
class Hop2Stats:
    tier1_matched_batches: int = 0
    tier1_fee_variance_batches: int = 0
    tier2_unique: int = 0
    tier2_ambiguous: int = 0
    tier2_unexplained_credit: int = 0
    missing_in_bank: int = 0
    in_transit_batches: int = 0
    links_proposed: int = 0
    exceptions_by_code: dict[str, int] = field(default_factory=dict)
    evidence_log: list[dict] = field(default_factory=list)


def _contribution_p(kind: str, amount_p: int, fee_p: int, gst_p: int) -> int:
    if kind == "capture":
        return moneymath.net_p(amount_p, fee_p, gst_p)
    return amount_p  # refund/chargeback/adjustment: already signed


def _row_dict(row: tuple) -> dict:
    payment_id, order_id, kind, amount_p, fee_p, gst_p, method, captured_on, settlement_id, utr = row
    return {
        "payment_id": payment_id,
        "order_id": order_id,
        "kind": kind,
        "amount_p": amount_p,
        "fee_p": fee_p,
        "gst_p": gst_p,
        "method": method,
        "captured_on": captured_on,
        "settlement_id": settlement_id,
        "utr": utr,
        "net_p": _contribution_p(kind, amount_p, fee_p, gst_p),
    }


def run_hop2(conn: sqlite3.Connection, run_id: str) -> Hop2Stats:
    stats = Hop2Stats()
    link_seq = 0
    exc_seq = 0

    def next_link_id() -> str:
        nonlocal link_seq
        link_seq += 1
        return f"{run_id}-ML2-{link_seq:04d}"

    def add_exception(code: str, records: list[dict], amount_at_risk_p: int, event_date: date, explanation: str, suggested_action: str) -> None:
        nonlocal exc_seq
        exc_seq += 1
        conn.execute(
            "INSERT INTO exceptions "
            "(exc_id, run_id, code, severity, hop, records, amount_at_risk_p, age_days, explanation, suggested_action, status) "
            "VALUES (?, ?, ?, ?, 2, ?, ?, ?, ?, ?, 'open')",
            (
                f"{run_id}-EXC2-{exc_seq:04d}",
                run_id,
                code,
                _SEVERITY[code],
                json.dumps(records),
                amount_at_risk_p,
                max((config.DATE_TO - event_date).days, 0),
                explanation,
                suggested_action,
            ),
        )
        stats.exceptions_by_code[code] = stats.exceptions_by_code.get(code, 0) + 1

    def propose_link(payment_id: str, line_id: str, tier: int, confidence: float, reason: str, evidence: dict) -> None:
        conn.execute(
            "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence, run_id) "
            "VALUES (?, 2, 'gw', ?, 'bank', ?, ?, ?, 'proposed', ?, ?, ?)",
            (next_link_id(), payment_id, line_id, tier, confidence, reason, json.dumps(evidence), run_id),
        )
        stats.links_proposed += 1

    rows = conn.execute(
        "SELECT payment_id, order_id, kind, amount_p, fee_p, gst_p, method, captured_on, settlement_id, utr "
        "FROM gw_payments"
    ).fetchall()
    bank_lines = conn.execute(
        "SELECT line_id, value_date, narration, credit_p, debit_p, utr_extracted FROM bank_lines"
    ).fetchall()
    credit_lines = {r[0]: r for r in bank_lines if r[3] > 0}  # line_id -> row, credit_p > 0 only

    in_transit_rows: list[dict] = []
    tier1_groups: dict[str, list[dict]] = {}
    tier2_pool: list[dict] = []
    tier2_pool_touched: set[str] = set()  # payment_ids ever placed in a candidate pool

    for row in rows:
        r = _row_dict(row)
        captured_on = date.fromisoformat(r["captured_on"])
        expected_settle = busdays.add_bdays(captured_on, config.SETTLEMENT_LAG_BDAYS)
        if expected_settle > config.DATE_TO:
            in_transit_rows.append(r)
        elif r["utr"] is not None:
            tier1_groups.setdefault(r["settlement_id"], []).append(r)
        else:
            tier2_pool.append(r)

    # --- in-transit batches: informational, never a failure ---
    in_transit_by_batch: dict[str, list[dict]] = {}
    for r in in_transit_rows:
        in_transit_by_batch.setdefault(r["settlement_id"], []).append(r)
    for batch_id, batch_rows in sorted(in_transit_by_batch.items()):
        stats.in_transit_batches += 1
        net = sum(r["net_p"] for r in batch_rows)
        earliest_date = min(date.fromisoformat(r["captured_on"]) for r in batch_rows)
        add_exception(
            "UNSETTLED_IN_TRANSIT",
            [{"src": "gw", "id": r["payment_id"]} for r in batch_rows],
            net,
            earliest_date,
            f"Batch {batch_id} ({len(batch_rows)} rows, net {net}p) is expected to settle after DATE_TO.",
            "No action needed; will settle in a future period.",
        )

    # --- tier 1: settlement_id + UTR known ---
    for settlement_id, batch_rows in sorted(tier1_groups.items()):
        utr = batch_rows[0]["utr"]
        bank_line = next((bl for bl in credit_lines.values() if bl[5] == utr), None)
        evidence = {
            "tier": 1,
            "settlement_id": settlement_id,
            "utr": utr,
            "rows": [
                {"id": r["payment_id"], "kind": r["kind"], "amount_p": r["amount_p"], "fee_p": r["fee_p"], "gst_p": r["gst_p"], "net_p": r["net_p"]}
                for r in batch_rows
            ],
            "tolerance_p": config.AMOUNT_TOL_P,
        }
        if bank_line is None:
            # No bank line's utr_extracted matches this batch's utr at all —
            # not expected in the seeded world; treat like tier-2 NoSolution
            # for a batch (nothing to reconcile against).
            continue
        line_id, value_date, narration, credit_p, debit_p, utr_extracted = bank_line
        subtotal = sum(r["net_p"] for r in batch_rows)
        delta = subtotal - credit_p
        evidence["target_p"] = credit_p
        evidence["subtotal_p"] = subtotal
        evidence["delta_p"] = delta

        # Recompute each capture's fee from the official rate card — ALWAYS,
        # not only when the aggregate is off. A wrong-fee batch can still
        # net out to exactly the bank's credit_p (the bank settles whatever
        # fee was actually applied, right or wrong), so the aggregate check
        # alone can miss it; the per-row audit against our own rate card is
        # the only thing that catches it. Detect only, never auto-correct.
        culprits = []
        for r in batch_rows:
            if r["kind"] != "capture":
                continue
            correct_fee = moneymath.fee_p(r["amount_p"], config.FEE_BPS[r["method"]])
            correct_gst = moneymath.gst_p(correct_fee, config.GST_BPS_ON_FEE)
            if correct_fee != r["fee_p"] or correct_gst != r["gst_p"]:
                culprits.append(
                    {
                        "id": r["payment_id"],
                        "stored_fee_p": r["fee_p"],
                        "correct_fee_p": correct_fee,
                        "fee_delta_p": r["fee_p"] - correct_fee,
                        "implied_bps": round(r["fee_p"] * 10_000 / r["amount_p"]) if r["amount_p"] else None,
                        "official_bps": config.FEE_BPS[r["method"]],
                    }
                )
        evidence["fee_recomputation"] = culprits

        if not culprits and abs(delta) <= config.AMOUNT_TOL_P:
            stats.tier1_matched_batches += 1
            for r in batch_rows:
                propose_link(r["payment_id"], line_id, tier=1, confidence=1.0, reason="tier1_utr_match", evidence=evidence)
            continue

        stats.tier1_fee_variance_batches += 1
        if len(culprits) == 1:
            amount_at_risk_p = abs(culprits[0]["fee_delta_p"])
            explanation = (
                f"Batch {settlement_id}: row {culprits[0]['id']} was charged fee {culprits[0]['stored_fee_p']}p "
                f"(implied ~{culprits[0]['implied_bps']}bps) instead of the official "
                f"{culprits[0]['official_bps']}bps ({culprits[0]['correct_fee_p']}p)."
            )
        else:
            amount_at_risk_p = abs(delta)
            explanation = (
                f"Batch {settlement_id}: settled net {subtotal}p != bank credit {credit_p}p "
                f"(delta {delta}p); fee recomputation did not isolate a single explaining row."
            )
        add_exception(
            "FEE_VARIANCE",
            [{"src": "bank", "id": line_id}] + [{"src": "gw", "id": c["id"]} for c in culprits],
            amount_at_risk_p,
            date.fromisoformat(batch_rows[0]["captured_on"]),
            explanation,
            "Detected only — confirm the correct rate card with the gateway; do not auto-adjust.",
        )
        for r in batch_rows:
            propose_link(r["payment_id"], line_id, tier=1, confidence=1.0, reason="tier1_fee_variance", evidence=evidence)

    # --- tier 2: UTR unusable, driven by unmatched credit bank lines ---
    matched_utrs = {batch_rows[0]["utr"] for batch_rows in tier1_groups.values()}
    unmatched_lines = sorted(
        (bl for bl in credit_lines.values() if bl[5] not in matched_utrs), key=lambda bl: bl[0]
    )
    for line_id, value_date_s, narration, credit_p, debit_p, utr_extracted in unmatched_lines:
        value_date = date.fromisoformat(value_date_s)
        pool = [
            r
            for r in tier2_pool
            if 0 <= busdays.bday_diff(date.fromisoformat(r["captured_on"]), value_date) <= config.DATE_WINDOW_BDAYS
        ]
        pool = sorted(pool, key=lambda r: (-abs(r["net_p"]), r["payment_id"]))[: config.SUBSET_MAX_ITEMS]
        for r in pool:
            tier2_pool_touched.add(r["payment_id"])

        items = [(r["payment_id"], r["net_p"]) for r in pool]
        result = reconstruct(credit_p, items, config.AMOUNT_TOL_P, config.SUBSET_MAX_ITEMS)
        pool_evidence = [{"id": r["payment_id"], "captured_on": r["captured_on"], "net_p": r["net_p"]} for r in pool]

        if isinstance(result, Unique):
            subtotal = sum(v for _, v in result.subset)
            evidence = {
                "tier": 2,
                "target_p": credit_p,
                "candidate_pool": pool_evidence,
                "subset": [{"id": pid, "net_p": v} for pid, v in result.subset],
                "subtotal_p": subtotal,
                "delta_p": subtotal - credit_p,
                "tolerance_p": config.AMOUNT_TOL_P,
            }
            stats.tier2_unique += 1
            for pid, _ in result.subset:
                propose_link(pid, line_id, tier=2, confidence=0.98, reason="tier2_subset_sum_unique", evidence=evidence)
            stats.evidence_log.append({"outcome": "tier2_unique", "bank_line": line_id, "evidence": evidence})

        elif isinstance(result, Multiple):
            subtotal_a = sum(v for _, v in result.subset_a)
            subtotal_b = sum(v for _, v in result.subset_b)
            evidence = {
                "tier": 2,
                "target_p": credit_p,
                "candidate_pool": pool_evidence,
                "subset_a": [{"id": pid, "net_p": v} for pid, v in result.subset_a],
                "subset_b": [{"id": pid, "net_p": v} for pid, v in result.subset_b],
                "subtotal_a_p": subtotal_a,
                "subtotal_b_p": subtotal_b,
                "tolerance_p": config.AMOUNT_TOL_P,
            }
            stats.tier2_ambiguous += 1
            add_exception(
                "AMBIGUOUS_SETTLEMENT",
                [{"src": "bank", "id": line_id}],
                credit_p,
                value_date,
                f"Bank line {line_id} (credit {credit_p}p) reconstructs to at least two disjoint candidate "
                f"subsets of gateway rows — engine must refuse; selecting either would be a false match.",
                "Confirm settlement ID in gateway dashboard.",
            )
            stats.evidence_log.append({"outcome": "tier2_ambiguous", "bank_line": line_id, "evidence": evidence})

        else:  # NoSolution
            evidence = {
                "tier": 2,
                "target_p": credit_p,
                "candidate_pool": pool_evidence,
                "reason": result.reason,
                "tolerance_p": config.AMOUNT_TOL_P,
            }
            stats.tier2_unexplained_credit += 1
            add_exception(
                "UNEXPLAINED_BANK_CREDIT",
                [{"src": "bank", "id": line_id}],
                credit_p,
                value_date,
                f"Bank credit {line_id} ({narration!r}, {credit_p}p) matches no gateway batch, "
                f"even after subset-sum reconstruction over {len(pool)} nearby candidates.",
                "Route to invoice queue; confirm source of funds.",
            )
            stats.evidence_log.append({"outcome": "tier2_unexplained_credit", "bank_line": line_id, "evidence": evidence})

    # --- MISSING_IN_BANK sweep: tier-2 rows never pulled into any pool ---
    # (SPEC names this explicitly; the seeded world doesn't currently
    # produce it — no defect leaves a batch with zero nearby bank lines.)
    never_pooled = [r for r in tier2_pool if r["payment_id"] not in tier2_pool_touched]
    by_batch: dict[str | None, list[dict]] = {}
    for r in never_pooled:
        by_batch.setdefault(r["settlement_id"], []).append(r)
    for batch_id, batch_rows in sorted(by_batch.items(), key=lambda kv: (kv[0] or "")):
        stats.missing_in_bank += 1
        net = sum(r["net_p"] for r in batch_rows)
        add_exception(
            "MISSING_IN_BANK",
            [{"src": "gw", "id": r["payment_id"]} for r in batch_rows],
            net,
            date.fromisoformat(min(r["captured_on"] for r in batch_rows)),
            f"Batch {batch_id} ({len(batch_rows)} rows, net {net}p) should have settled but no bank line "
            f"exists within {config.DATE_WINDOW_BDAYS} business days of any candidate value_date.",
            "Check for a missing bank statement line or a mis-dated settlement.",
        )

    conn.commit()
    return stats
