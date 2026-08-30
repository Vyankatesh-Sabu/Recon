"""verifier.py — invariants V1..V3; the only code path allowed to accept a match_link (SPEC §6.5).

CLAUDE.md rule 7: only this module may set match_link.status='accepted'.
V1 re-runs the arithmetic itself from raw DB rows — it never trusts a
proposer's `evidence` blob (that's the whole point: a wrong tier-4/LLM
proposal, later, must be caught here the same way a wrong hop2 proposal
would be). V2 is enforced by the SQLite partial unique indexes (P0
001_schema.sql); a violation surfaces as sqlite3.IntegrityError, which this
module catches, rejects the later proposal, and raises DUPLICATE_CLAIM.
V3 (GL voucher balance) is conceptually "run at load" (SPEC §6.5) but needs
a run_id to write exceptions against, so `check_v3_gl_balance` is exposed
here for pipeline.py to call as its first step, before any hop runs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import config
from recon import moneymath


@dataclass
class VerifierStats:
    accepted: int = 0
    rejected: int = 0
    duplicate_claims: int = 0


def check_v3_gl_balance(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """V3: every GL voucher must balance (Σdebit == Σcredit).

    Returns (voucher_no, total_debit_p, total_credit_p) for each violator.
    """
    return conn.execute(
        "SELECT voucher_no, SUM(debit_p), SUM(credit_p) FROM gl_entries "
        "GROUP BY voucher_no HAVING SUM(debit_p) != SUM(credit_p)"
    ).fetchall()


def _verify_hop1(conn: sqlite3.Connection, id_a: str, id_b: str) -> tuple[bool, str]:
    """V1 for a hop-1 link: re-derive the order<->capture pairing from raw rows."""
    order = conn.execute("SELECT order_id FROM orders WHERE order_id = ?", (id_a,)).fetchone()
    capture = conn.execute(
        "SELECT order_id, kind FROM gw_payments WHERE payment_id = ?", (id_b,)
    ).fetchone()
    if order is None or capture is None:
        return False, "referenced record no longer exists"
    cap_order_id, kind = capture
    if kind != "capture":
        return False, "id_b is not a capture"
    if cap_order_id != id_a:
        return False, "capture's order_id does not match id_a"
    return True, ""


def _verify_hop2_batch(conn: sqlite3.Connection, id_a_list: list[str], id_b: str) -> tuple[bool, str]:
    """V1 for a hop-2 batch: re-derive Σnet from raw gw_payments rows, not the evidence blob."""
    bank_line = conn.execute("SELECT credit_p FROM bank_lines WHERE line_id = ?", (id_b,)).fetchone()
    if bank_line is None:
        return False, "bank line no longer exists"
    (credit_p,) = bank_line
    total = 0
    for id_a in id_a_list:
        row = conn.execute(
            "SELECT kind, amount_p, fee_p, gst_p FROM gw_payments WHERE payment_id = ?", (id_a,)
        ).fetchone()
        if row is None:
            return False, f"gateway row {id_a} no longer exists"
        kind, amount_p, fee_p, gst_p = row
        total += moneymath.net_p(amount_p, fee_p, gst_p) if kind == "capture" else amount_p
    if abs(total - credit_p) > config.AMOUNT_TOL_P:
        return False, f"recomputed batch net {total}p != bank credit {credit_p}p"
    return True, ""


def run_verifier(conn: sqlite3.Connection, run_id: str) -> VerifierStats:
    stats = VerifierStats()
    exc_seq = 0

    def add_duplicate_claim(hop: int, records: list[dict], link_id: str) -> None:
        nonlocal exc_seq
        exc_seq += 1
        conn.execute(
            "INSERT INTO exceptions "
            "(exc_id, run_id, code, severity, hop, records, amount_at_risk_p, age_days, explanation, suggested_action, status) "
            "VALUES (?, ?, 'DUPLICATE_CLAIM', 'critical', ?, ?, 0, 0, ?, ?, 'open')",
            (
                f"{run_id}-VEXC-{exc_seq:04d}",
                run_id,
                hop,
                json.dumps(records),
                f"Link {link_id} rejected: one of its records is already claimed (accepted) at hop {hop}.",
                "Investigate why two proposals claimed the same record; this should never happen by construction.",
            ),
        )

    def accept_or_reject(link_id: str, hop: int, src_a: str, id_a: str, src_b: str, id_b: str) -> None:
        try:
            conn.execute("UPDATE match_link SET status = 'accepted' WHERE link_id = ?", (link_id,))
            stats.accepted += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE match_link SET status = 'rejected', reason = 'V2_duplicate_claim' WHERE link_id = ?",
                (link_id,),
            )
            stats.rejected += 1
            stats.duplicate_claims += 1
            add_duplicate_claim(hop, [{"src": src_a, "id": id_a}, {"src": src_b, "id": id_b}], link_id)

    def reject_v1(link_id: str, reason: str) -> None:
        conn.execute(
            "UPDATE match_link SET status = 'rejected', reason = ? WHERE link_id = ?",
            (f"V1_failed: {reason}", link_id),
        )
        stats.rejected += 1

    proposed = conn.execute(
        "SELECT link_id, hop, src_a, id_a, src_b, id_b FROM match_link "
        "WHERE run_id = ? AND status = 'proposed' ORDER BY link_id",
        (run_id,),
    ).fetchall()
    hop1_links = [r for r in proposed if r[1] == 1]
    hop2_links = [r for r in proposed if r[1] == 2]

    # --- hop 1: each link is independently verifiable ---
    for link_id, hop, src_a, id_a, src_b, id_b in hop1_links:
        ok, reason = _verify_hop1(conn, id_a, id_b)
        if not ok:
            reject_v1(link_id, reason)
            continue
        accept_or_reject(link_id, hop, src_a, id_a, src_b, id_b)

    # --- hop 2: a batch (all rows sharing a bank line) is verified, then
    # accepted or rejected, atomically — a bank line's reconstruction is
    # only meaningful as a whole ---
    batches: dict[str, list[tuple]] = {}
    for r in hop2_links:
        batches.setdefault(r[5], []).append(r)  # group by id_b
    for id_b, links in sorted(batches.items()):
        id_a_list = [r[3] for r in links]
        ok, reason = _verify_hop2_batch(conn, id_a_list, id_b)
        if not ok:
            for link_id, *_ in links:
                reject_v1(link_id, reason)
            continue
        for link_id, hop, src_a, id_a, src_b, id_b in links:
            accept_or_reject(link_id, hop, src_a, id_a, src_b, id_b)

    conn.commit()
    return stats
