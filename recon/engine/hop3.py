"""hop3.py — bank lines ↔ GL journals (SPEC §6.4).

Runs after hop2, before the verifier (SPEC §6's pipeline order: hop1 -> hop2
-> hop3 -> verifier finalisation) — so "settlement line" here means a bank
line with a hop-2 link still in status='proposed', not yet verifier-accepted.
Writes match_link rows with status='proposed' ONLY (CLAUDE.md rule 7).
GL_DECOMPOSITION_FAIL still proposes the bank<->voucher link (the pairing is
certain — we found the matching BANK debit; only the decomposition is wrong)
— same "accept the link, flag the delta" pattern as hop1/hop2.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

import config
from recon import busdays
from recon.engine.events import OnEvent

_SEVERITY = {
    "GL_DECOMPOSITION_FAIL": "warn",
    "GL_MISSING": "critical",
    "GL_DUPLICATE": "warn",
    "GL_AMBIGUOUS_MATCH": "critical",
    "UNLINKED_REFUND": "warn",
    "CHARGEBACK_UNRESOLVED": "critical",
}


@dataclass
class Hop3Stats:
    matched: int = 0
    decomposition_fail: int = 0
    gl_missing: int = 0
    gl_duplicate: int = 0
    gl_ambiguous_match: int = 0
    unlinked_refund: int = 0
    chargeback_unresolved: int = 0
    links_proposed: int = 0
    exceptions_by_code: dict[str, int] = field(default_factory=dict)


def _voucher_signature(conn: sqlite3.Connection, voucher_no: str) -> tuple:
    """Full GL content of a voucher, ignoring voucher_no/line_no/memo — two
    vouchers with this signature equal are byte-for-byte the same journal
    (D-13's real duplicate). Two vouchers that merely share a BANK debit
    amount and date but differ here are genuinely distinct vouchers that
    coincidentally collide (D-02's twin settlements: same net, same date,
    but different FEE_EXPENSE/INPUT_GST/PG_RECEIVABLE underneath) — hop3
    has no signal to tell which bank line owns which, and must refuse
    rather than guess (CLAUDE.md rule 6), not call the second one a
    "duplicate" it never was."""
    return tuple(
        sorted(
            (account, debit_p, credit_p)
            for account, debit_p, credit_p in conn.execute(
                "SELECT account, debit_p, credit_p FROM gl_entries WHERE voucher_no = ?", (voucher_no,)
            )
        )
    )


def run_hop3(conn: sqlite3.Connection, run_id: str, on_event: OnEvent | None = None) -> Hop3Stats:
    stats = Hop3Stats()
    link_seq = 0
    exc_seq = 0

    def next_link_id() -> str:
        nonlocal link_seq
        link_seq += 1
        return f"{run_id}-ML3-{link_seq:04d}"

    # Guards against reporting the same finding twice: a voucher pair can be
    # independently rediscovered by more than one bank line iteration (this
    # is exactly how the GL_AMBIGUOUS_MATCH bug below was found — two
    # bank lines with identical credit/date both re-query the same voucher
    # pool and each, on its own, thinks it found something new).
    reported_duplicate_vouchers: set[str] = set()
    reported_ambiguous_groups: set[tuple[str, ...]] = set()

    def add_exception(code: str, records: list[dict], amount_at_risk_p: int, event_date: date, explanation: str, suggested_action: str) -> None:
        nonlocal exc_seq
        exc_seq += 1
        exc_id = f"{run_id}-EXC3-{exc_seq:04d}"
        conn.execute(
            "INSERT INTO exceptions "
            "(exc_id, run_id, code, severity, hop, records, amount_at_risk_p, age_days, explanation, suggested_action, status) "
            "VALUES (?, ?, ?, ?, 3, ?, ?, ?, ?, ?, 'open')",
            (
                exc_id,
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
        if on_event is not None:
            on_event(
                {
                    "kind": "exception",
                    "hop": 3,
                    "exc_id": exc_id,
                    "code": code,
                    "severity": _SEVERITY[code],
                    "amount_at_risk_p": amount_at_risk_p,
                    "records": records,
                }
            )

    def propose_link(line_id: str, voucher_no: str, evidence: dict) -> None:
        conn.execute(
            "INSERT INTO match_link (link_id, hop, src_a, id_a, src_b, id_b, tier, confidence, status, reason, evidence, run_id) "
            "VALUES (?, 3, 'bank', ?, 'gl', ?, 1, 1.0, 'proposed', 'hop3_bank_gl_match', ?, ?)",
            (next_link_id(), line_id, voucher_no, json.dumps(evidence), run_id),
        )
        stats.links_proposed += 1

    # --- bank lines with a hop-2 resolution (proposed, not yet verifier-
    # accepted — hop3 runs before the verifier per SPEC §6's pipeline order) ---
    hop2_proposed = conn.execute(
        "SELECT id_a, id_b FROM match_link WHERE run_id = ? AND hop = 2 AND status = 'proposed'", (run_id,)
    ).fetchall()
    batch_payments: dict[str, list[str]] = {}
    for id_a, id_b in hop2_proposed:
        batch_payments.setdefault(id_b, []).append(id_a)

    for line_id, payment_ids in sorted(batch_payments.items()):
        value_date_s, credit_p = conn.execute(
            "SELECT value_date, credit_p FROM bank_lines WHERE line_id = ?", (line_id,)
        ).fetchone()
        value_date = date.fromisoformat(value_date_s)

        rows = [
            (pid,) + conn.execute(
                "SELECT kind, amount_p, fee_p, gst_p FROM gw_payments WHERE payment_id = ?", (pid,)
            ).fetchone()
            for pid in payment_ids
        ]
        fee_total = sum(r[3] for r in rows if r[1] == "capture")
        gst_total = sum(r[4] for r in rows if r[1] == "capture")
        # PG_RECEIVABLE tracks captures/refunds only — a chargeback hits
        # CHARGEBACK_LOSS instead (world.py's resync_settlement), since it's
        # a direct cash deduction against an already-cleared receivable
        # from some earlier, unrelated settlement.
        receivable_total = sum(r[2] for r in rows if r[1] != "chargeback")

        window_start = busdays.add_bdays(value_date, -config.DATE_WINDOW_BDAYS)
        window_end = busdays.add_bdays(value_date, config.DATE_WINDOW_BDAYS)
        voucher_nos = sorted(
            v[0]
            for v in conn.execute(
                "SELECT DISTINCT voucher_no FROM gl_entries WHERE account = 'BANK' "
                "AND debit_p BETWEEN ? AND ? AND entry_date BETWEEN ? AND ?",
                (
                    credit_p - config.AMOUNT_TOL_P,
                    credit_p + config.AMOUNT_TOL_P,
                    window_start.isoformat(),
                    window_end.isoformat(),
                ),
            )
        )

        if not voucher_nos:
            stats.gl_missing += 1
            add_exception(
                "GL_MISSING",
                [{"src": "bank", "id": line_id}],
                receivable_total,
                value_date,
                f"No GL journal found with a BANK debit matching bank line {line_id} "
                f"({credit_p}p) within tolerance in a ±{config.DATE_WINDOW_BDAYS}-bday window.",
                "Investigate for a missing or mis-dated settlement voucher; do not fabricate one.",
            )
            continue

        if len(voucher_nos) > 1:
            # More than one voucher's BANK debit falls within tolerance and
            # the date window. That's D-13's real duplicate case (a
            # byte-for-byte copy) — but it's ALSO exactly what D-02's twin
            # settlements look like from the GL side alone: two genuinely
            # distinct vouchers (different FEE_EXPENSE/INPUT_GST/
            # PG_RECEIVABLE underneath) that happen to share BANK amount
            # and date because their bank lines do too. Only the first is a
            # real duplicate; guessing on the second is exactly the false
            # match CLAUDE.md rule 6 forbids. Full-content comparison
            # (ignoring voucher_no/memo) tells the two cases apart.
            signatures = {v: _voucher_signature(conn, v) for v in voucher_nos}
            distinct_signatures = set(signatures.values())
            if len(distinct_signatures) > 1:
                # Structurally different vouchers coincidentally sharing an
                # amount+date — hop3 has no signal to say which bank line
                # owns which. Refuse for this bank line rather than pick
                # one arbitrarily; report the ambiguous group once, no
                # matter how many bank lines independently rediscover it.
                group_key = tuple(voucher_nos)
                if group_key not in reported_ambiguous_groups:
                    reported_ambiguous_groups.add(group_key)
                    stats.gl_ambiguous_match += 1
                    total_receivable = sum(
                        c
                        for v in voucher_nos
                        for a, _d, c in conn.execute(
                            "SELECT account, debit_p, credit_p FROM gl_entries WHERE voucher_no = ?", (v,)
                        )
                        if a == "PG_RECEIVABLE"
                    )
                    add_exception(
                        "GL_AMBIGUOUS_MATCH",
                        [{"src": "gl", "id": v} for v in voucher_nos] + [{"src": "bank", "id": line_id}],
                        total_receivable,
                        value_date,
                        f"{len(voucher_nos)} distinct GL vouchers ({', '.join(voucher_nos)}) all have a BANK "
                        f"debit matching bank line {line_id} ({credit_p}p) within tolerance in the same "
                        f"±{config.DATE_WINDOW_BDAYS}-bday window, and are NOT duplicates of each other "
                        "(their other lines differ) — engine must refuse; picking any one would be a false match.",
                        "Confirm which settlement voucher belongs to which bank line before posting further.",
                    )
                continue  # no hop3 link proposed for this bank line — refusal, not a guess
            chosen, *duplicates = voucher_nos
        else:
            chosen, duplicates = voucher_nos[0], []

        for extra in duplicates:
            if extra in reported_duplicate_vouchers:
                continue
            reported_duplicate_vouchers.add(extra)
            stats.gl_duplicate += 1
            extra_receivable = sum(
                c
                for a, _d, c in conn.execute(
                    "SELECT account, debit_p, credit_p FROM gl_entries WHERE voucher_no = ?", (extra,)
                )
                if a == "PG_RECEIVABLE"
            )
            add_exception(
                "GL_DUPLICATE",
                [{"src": "gl", "id": extra}],
                extra_receivable,
                value_date,
                f"Voucher {extra} duplicates {chosen}'s BANK debit for bank line {line_id}; "
                "the clearing account is over-credited by this voucher's PG_RECEIVABLE line.",
                "Reverse the duplicate voucher.",
            )

        found = {
            account: (debit_p, credit_p_)
            for account, debit_p, credit_p_ in conn.execute(
                "SELECT account, debit_p, credit_p FROM gl_entries WHERE voucher_no = ?", (chosen,)
            )
        }
        found_fee = found.get("FEE_EXPENSE", (0, 0))[0]
        found_gst = found.get("INPUT_GST", (0, 0))[0]
        found_receivable = found.get("PG_RECEIVABLE", (0, 0))[1]

        evidence = {
            "bank_line": line_id,
            "voucher_no": chosen,
            "target_credit_p": credit_p,
            "expected": {"fee_p": fee_total, "gst_p": gst_total, "receivable_p": receivable_total},
            "found": {"fee_p": found_fee, "gst_p": found_gst, "receivable_p": found_receivable},
        }
        propose_link(line_id, chosen, evidence)

        mismatches = []
        if found_fee != fee_total:
            mismatches.append(f"FEE_EXPENSE: expected {fee_total}p, found {found_fee}p")
        if found_gst != gst_total:
            mismatches.append(f"INPUT_GST: expected {gst_total}p, found {found_gst}p")
        if found_receivable != receivable_total:
            mismatches.append(f"PG_RECEIVABLE credit: expected {receivable_total}p, found {found_receivable}p")

        if mismatches:
            stats.decomposition_fail += 1
            gst_shortfall = abs(gst_total - found_gst)
            fee_shortfall = abs(fee_total - found_fee)
            add_exception(
                "GL_DECOMPOSITION_FAIL",
                [{"src": "gl", "id": chosen}],
                gst_shortfall or fee_shortfall,
                value_date,
                f"Voucher {chosen} decomposition mismatch ({'; '.join(mismatches)}). Fee and GST appear "
                "lumped into a single account instead of separate FEE_EXPENSE/INPUT_GST lines — this "
                "forfeits the input tax credit on the GST component.",
                "Repost the voucher with FEE_EXPENSE and INPUT_GST as separate lines; do not auto-correct.",
            )
        else:
            stats.matched += 1

    # --- refund GL check: independent of the bank-line loop above, over
    # every refund row regardless of its batch's settlement outcome ---
    for payment_id, amount_p, captured_on in conn.execute(
        "SELECT payment_id, amount_p, captured_on FROM gw_payments WHERE kind = 'refund'"
    ):
        amt = abs(amount_p)
        found = conn.execute(
            "SELECT voucher_no FROM gl_entries WHERE entry_date = ? AND account = 'SALES_RETURNS' AND debit_p = ?",
            (captured_on, amt),
        ).fetchone()
        if found is None:
            stats.unlinked_refund += 1
            add_exception(
                "UNLINKED_REFUND",
                [{"src": "gw", "id": payment_id}],
                amt,
                date.fromisoformat(captured_on),
                f"Refund {payment_id} has no matching SALES_RETURNS/PG_RECEIVABLE GL journal.",
                "Post the missing refund journal; flag for accounting review.",
            )

    # --- chargeback check: every chargeback needs human review regardless
    # of whether its CHARGEBACK_LOSS voucher line is correctly booked (it
    # always is, by construction — this isn't a GL-repair exception, it's a
    # business one: a dispute landed and someone has to respond to it) ---
    for payment_id, amount_p, captured_on, order_id in conn.execute(
        "SELECT payment_id, amount_p, captured_on, order_id FROM gw_payments WHERE kind = 'chargeback'"
    ):
        stats.chargeback_unresolved += 1
        records = [{"src": "gw", "id": payment_id}]
        if order_id:
            records.append({"src": "orders", "id": order_id})
        add_exception(
            "CHARGEBACK_UNRESOLVED",
            records,
            abs(amount_p),
            date.fromisoformat(captured_on),
            f"Chargeback {payment_id} ({abs(amount_p)}p) needs a dispute response.",
            "Respond to the chargeback with the gateway before the deadline; assess for a write-off.",
        )

    conn.commit()
    return stats
