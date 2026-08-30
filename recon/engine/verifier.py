"""verifier.py — invariants V1..V5; the only code path allowed to accept a match_link (SPEC §6.5).

CLAUDE.md rule 7: only this module may set match_link.status='accepted'.
V1/V4 re-run the arithmetic themselves from raw DB rows — never trusting a
proposer's `evidence` blob (that's the whole point: a wrong tier-4/LLM
proposal, later, must be caught here the same way a wrong hop2/hop3
proposal would be). V2 is enforced by the SQLite partial unique indexes (P0
001_schema.sql); a violation surfaces as sqlite3.IntegrityError, which this
module catches, rejects the later proposal, and raises DUPLICATE_CLAIM.
V3 (GL voucher balance) is conceptually "run at load" (SPEC §6.5) but needs
a run_id to write exceptions against, so `check_v3_gl_balance` is exposed
here for pipeline.py to call as its first step, before any hop runs.
V5 (the clearing-account control) is exposed as `check_v5_clearing_control`,
called by pipeline.py after the verifier's own accept/reject pass.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import config
from recon import moneymath


class ClearingControlFailure(RuntimeError):
    """Raised by check_v5_clearing_control when residual_p != exposure_p.

    This control exists to catch OUR bugs, not the synthetic world's
    defects — a mismatch means something in the pipeline is wrong. Never
    caught-and-ignored, never special-cased away, never given a fudge term.
    """


# V5 inclusion map: exception code -> signed multiplier on amount_at_risk_p
# for its contribution to exposure_p. +1 means the code represents a real,
# currently-uncleared PG_RECEIVABLE debit; -1 means an unwarranted extra
# credit (over-crediting the account); 0 (absent from the map) means the
# code doesn't touch PG_RECEIVABLE at all and is excluded. Every entry is
# commented with why.
V5_INCLUSION_MAP: dict[str, int] = {
    # Captured but not yet settled: the day's CAP voucher already posted
    # Dr PG_RECEIVABLE, and the offsetting Cr won't post until settlement
    # (which is legitimately beyond DATE_TO). This is the ONLY reason a
    # perfectly clean world still shows a nonzero residual at DATE_TO.
    "UNSETTLED_IN_TRANSIT": +1,
    # The settlement's entire GL journal was deleted (D-05): its Cr
    # PG_RECEIVABLE never posted, so that day's Dr is permanently stuck
    # open until someone re-books the voucher.
    "GL_MISSING": +1,
    # The refund's Cr PG_RECEIVABLE never posted (D-03): the original
    # capture's Dr is still on the books, uncancelled by the refund.
    "UNLINKED_REFUND": +1,
    # An extra, unwarranted Cr PG_RECEIVABLE was posted (D-13): the
    # clearing account is over-credited by exactly this voucher's amount.
    "GL_DUPLICATE": -1,
    # --- excluded, each because the money is already fully cleared
    # through the GL, or never touched PG_RECEIVABLE in the first place ---
    # ORPHAN_ORDER: no capture exists at all, so no PG_RECEIVABLE debit was
    #   ever posted for this order — nothing to reconcile.
    # ORPHAN_PAYMENT / DUPLICATE_PAYMENT: real captures, but they settle
    #   normally like any other row in their batch — the day's CAP debit
    #   and the settlement's Cr fully cancel regardless of order attribution.
    # PARTIAL_CAPTURE_MISMATCH: the GL only ever records the actual
    #   captured amount, which clears normally; the order/capture
    #   discrepancy never touches the GL at all.
    # FEE_VARIANCE: fee/gst affect BANK/FEE_EXPENSE/INPUT_GST, never
    #   PG_RECEIVABLE.
    # AMBIGUOUS_SETTLEMENT: both synthetic settlement vouchers still exist
    #   and their Cr PG_RECEIVABLE together fully cancel the day's Dr —
    #   the ambiguity is purely about WHICH bank line pairs with which
    #   subset, not an accounting residual.
    # UNEXPLAINED_BANK_CREDIT: the clean-world GL entry behind this line is
    #   Dr BANK / Cr SALES — it never involves PG_RECEIVABLE.
    # CHARGEBACK_UNRESOLVED: a chargeback always gets its own CHARGEBACK_LOSS
    #   debit line (world.py's resync_settlement) — it never touches
    #   PG_RECEIVABLE, and the exception exists purely because a dispute
    #   needs a human response, not because anything is unbalanced.
    # GL_DECOMPOSITION_FAIL: only the debit side (FEE_EXPENSE/INPUT_GST vs
    #   BANK_CHARGES) is wrong; PG_RECEIVABLE's credit is untouched and the
    #   voucher still balances.
    # DATA_QUALITY / DUPLICATE_CLAIM: pipeline-internal-error signals, not
    #   business exceptions about a specific money movement — if these
    #   fire, the GL is already untrustworthy and V5 SHOULD fail for that
    #   reason too, not be papered over.
}


def compute_residual_p(conn: sqlite3.Connection) -> int:
    """V5: residual_p = Σ PG_RECEIVABLE debits - Σ credits, from gl_entries ALONE."""
    row = conn.execute(
        "SELECT COALESCE(SUM(debit_p), 0) - COALESCE(SUM(credit_p), 0) FROM gl_entries "
        "WHERE account = 'PG_RECEIVABLE'"
    ).fetchone()
    return row[0]


def compute_exposure_p(conn: sqlite3.Connection, run_id: str) -> tuple[int, dict[str, int]]:
    """V5: exposure_p = Σ (signed) amount_at_risk_p over open, receivable-affecting exceptions.

    Returns (exposure_p, per_code_breakdown).
    """
    breakdown: dict[str, int] = {}
    for code, amount_at_risk_p in conn.execute(
        "SELECT code, amount_at_risk_p FROM exceptions WHERE run_id = ? AND status = 'open'", (run_id,)
    ):
        sign = V5_INCLUSION_MAP.get(code, 0)
        if sign == 0:
            continue
        breakdown[code] = breakdown.get(code, 0) + sign * amount_at_risk_p
    return sum(breakdown.values()), breakdown


def check_v5_clearing_control(conn: sqlite3.Connection, run_id: str) -> None:
    """V5, the clearing-account control (demo beat 6). Aborts loudly on mismatch.

    Never special-cased away, never given a fudge term — this control
    exists specifically to catch bugs in this pipeline, not in the
    synthetic world's defects.
    """
    residual_p = compute_residual_p(conn)
    exposure_p, breakdown = compute_exposure_p(conn, run_id)
    if residual_p != exposure_p:
        lines = [
            f"V5 CLEARING CONTROL FAILED: residual_p={residual_p} != exposure_p={exposure_p} "
            f"(diff={residual_p - exposure_p}p)",
            "Per-code exposure breakdown:",
        ]
        for code, amount in sorted(breakdown.items()):
            lines.append(f"  {code}: {amount}p")
        message = "\n".join(lines)
        raise ClearingControlFailure(message)


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


def _verify_hop3(conn: sqlite3.Connection, id_a: str, id_b: str) -> tuple[bool, str]:
    """V4 for a hop-3 link: re-derive the bank<->voucher pairing from raw rows.

    Only re-confirms the fundamental pairing fact (the voucher's BANK debit
    really does match the bank line's credit within tolerance) — the fuller
    decomposition check (FEE_EXPENSE/INPUT_GST/PG_RECEIVABLE) is a business
    exception (GL_DECOMPOSITION_FAIL), not a reason to reject the pairing,
    same "accept the link, flag the delta" pattern as hop1/hop2.
    """
    bank_line = conn.execute("SELECT credit_p FROM bank_lines WHERE line_id = ?", (id_a,)).fetchone()
    if bank_line is None:
        return False, "bank line no longer exists"
    (credit_p,) = bank_line
    row = conn.execute(
        "SELECT debit_p FROM gl_entries WHERE voucher_no = ? AND account = 'BANK'", (id_b,)
    ).fetchone()
    if row is None:
        return False, "voucher has no BANK debit line"
    (bank_debit_p,) = row
    if abs(bank_debit_p - credit_p) > config.AMOUNT_TOL_P:
        return False, f"voucher BANK debit {bank_debit_p}p != bank line credit {credit_p}p"
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

    def reject_v1(link_id: str, reason: str, tier: int | None = None) -> None:
        # Tier 4 (LLM) proposals get a distinguishing rejection message —
        # SPEC §8/V6: "rejection demotes to the original exception with
        # llm_rejected=true noted." The original exception itself is left
        # untouched (never written by this rejection path) — it's simply
        # never resolved, which is what "demotes to" means in practice.
        prefix = "V1_failed (tier4/llm proposal rejected)" if tier == 4 else "V1_failed"
        conn.execute(
            "UPDATE match_link SET status = 'rejected', reason = ? WHERE link_id = ?",
            (f"{prefix}: {reason}", link_id),
        )
        stats.rejected += 1

    proposed = conn.execute(
        "SELECT link_id, hop, src_a, id_a, src_b, id_b, tier FROM match_link "
        "WHERE run_id = ? AND status = 'proposed' ORDER BY link_id",
        (run_id,),
    ).fetchall()
    hop1_links = [r for r in proposed if r[1] == 1]
    hop2_links = [r for r in proposed if r[1] == 2]
    hop3_links = [r for r in proposed if r[1] == 3]

    # --- hop 1: each link is independently verifiable ---
    for link_id, hop, src_a, id_a, src_b, id_b, _tier in hop1_links:
        ok, reason = _verify_hop1(conn, id_a, id_b)
        if not ok:
            reject_v1(link_id, reason)
            continue
        accept_or_reject(link_id, hop, src_a, id_a, src_b, id_b)

    # --- hop 2: a batch (all rows sharing a bank line) is verified, then
    # accepted or rejected, atomically — a bank line's reconstruction is
    # only meaningful as a whole. This is also where tier-4 (LLM) proposals
    # land: same hop, same grouping, same re-derivation from raw rows — a
    # tier-4 proposal must pass V1+V2 like any other (SPEC §8/V6). ---
    batches: dict[str, list[tuple]] = {}
    for r in hop2_links:
        batches.setdefault(r[5], []).append(r)  # group by id_b
    for id_b, links in sorted(batches.items()):
        id_a_list = [r[3] for r in links]
        ok, reason = _verify_hop2_batch(conn, id_a_list, id_b)
        tier = links[0][6]
        if not ok:
            for link_id, *_ in links:
                reject_v1(link_id, reason, tier=tier)
            continue
        for link_id, hop, src_a, id_a, src_b, id_b, _tier in links:
            accept_or_reject(link_id, hop, src_a, id_a, src_b, id_b)

    # --- hop 3 (V4): each bank<->voucher pairing is independently verifiable ---
    for link_id, hop, src_a, id_a, src_b, id_b, _tier in hop3_links:
        ok, reason = _verify_hop3(conn, id_a, id_b)
        if not ok:
            reject_v1(link_id, reason)
            continue
        accept_or_reject(link_id, hop, src_a, id_a, src_b, id_b)

    conn.commit()
    return stats
