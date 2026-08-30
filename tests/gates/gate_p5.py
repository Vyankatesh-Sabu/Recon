#!/usr/bin/env python3
"""gate_p5.py — Phase P5 gate (SPEC.md G5). Run directly: python tests/gates/gate_p5.py

G5: the five §9.3 questions answered correctly, each number verified against
direct SQL (independent of the tool functions and of the scripted answer
text). MockLLM stands in for the model — it's scripted to call the right
tool(s) and return an answer whose figures were read off this run's real
data (verified below, not assumed); this gate is a check on the TOOLS and
the DATA, not on any provider's prose-generation quality.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import config
from recon.db import migrate
from recon.engine import verifier
from recon.engine.pipeline import run_pipeline
from recon.generator import generate_world
from recon.generator.io import write_csvs
from recon.generator.truth import write_ground_truth
from recon.llm import qa
from recon.llm.client import MockLLM
from recon.loader import load_all

FAILURES: list[str] = []
ANSWERS: list[tuple[str, str]] = []  # (question, answer) for the final verbatim printout


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def ask(conn: sqlite3.Connection, run_id: str, question: str, mock: MockLLM) -> dict:
    result = qa.answer_question(conn, question, mock, run_id=run_id)
    ANSWERS.append((question, result["answer"]))
    return result


def question_1(conn: sqlite3.Connection, run_id: str) -> None:
    question = "Why is the credit for batch setl_0812 (settling 14 Aug) short of that day's raw captures?"
    mock = MockLLM(
        qa_script={
            "setl_0812": [
                {"tool_call": {"name": "explain_settlement", "input": {"ref": "setl_0812"}}},
                {
                    "answer": (
                        "Batch setl_0812 nets ₹37,697.36 (bank line setl_0812), not the ₹51,486.00 of raw "
                        "captures (PAY-0043, PAY-0044, PAY-0045, PAY-0046), because refund PAY-0059 "
                        "(order ORD-1038, -₹13,349.00) was netted into the same batch, plus ₹439.64 in "
                        "fees/GST — ₹51,486.00 - ₹13,349.00 - ₹439.64 = ₹37,697.36, matching the bank credit exactly."
                    )
                },
            ]
        }
    )
    result = ask(conn, run_id, question, mock)
    check(result["tool_calls"] == [{"name": "explain_settlement", "input": {"ref": "setl_0812"}}], "Q1: expected exactly one explain_settlement call")
    check("PAY-0059" in result["record_ids"], "Q1: PAY-0059 must be a cited record id")

    # independent SQL verification
    refund = conn.execute(
        "SELECT order_id, amount_p FROM gw_payments WHERE payment_id = 'PAY-0059' AND kind = 'refund'"
    ).fetchone()
    check(refund is not None, "Q1 SQL: PAY-0059 must exist as a refund")
    check(refund and refund[0] == "ORD-1038", f"Q1 SQL: PAY-0059's order should be ORD-1038, got {refund}")
    check(refund and refund[1] == -1_334_900, f"Q1 SQL: PAY-0059 should be -₹13,349.00, got {refund}")
    captures_total = conn.execute(
        "SELECT SUM(amount_p) FROM gw_payments WHERE settlement_id = 'SETL_0812' AND kind = 'capture'"
    ).fetchone()[0] or conn.execute(
        "SELECT SUM(g.amount_p) FROM gw_payments g "
        "JOIN match_link m ON m.id_a = g.payment_id AND m.hop = 2 "
        "WHERE m.id_b = 'setl_0812' AND m.run_id = ? AND g.kind = 'capture'",
        (run_id,),
    ).fetchone()[0]
    credit_p = conn.execute("SELECT credit_p FROM bank_lines WHERE line_id = 'setl_0812'").fetchone()[0]
    fee_gst_total = conn.execute(
        "SELECT SUM(g.fee_p) + SUM(g.gst_p) FROM gw_payments g "
        "JOIN match_link m ON m.id_a = g.payment_id AND m.hop = 2 "
        "WHERE m.id_b = 'setl_0812' AND m.run_id = ? AND g.kind = 'capture'",
        (run_id,),
    ).fetchone()[0]
    check(
        captures_total + refund[1] - fee_gst_total == credit_p,
        f"Q1 SQL: captures({captures_total}) + refund({refund[1]}) - fees/gst({fee_gst_total}) should equal credit_p({credit_p})",
    )


def question_2(conn: sqlite3.Connection, run_id: str) -> None:
    question = "Trace ORD-1017 end to end."
    mock = MockLLM(
        qa_script={
            "ORD-1017": [
                {"tool_call": {"name": "trace_order", "input": {"order_id": "ORD-1017"}}},
                {
                    "answer": (
                        "ORD-1017 (CUST-017, ₹12,447.00, card) was captured as PAY-0017 on 2026-08-05, "
                        "settled in batch SETL_0805 via UTR UTIB09737631165 (bank line setl_0805), and "
                        "posted to GL vouchers V-20260805-CAP and V-20260805-SETL. Hop1/hop2/hop3 are all "
                        "accepted; no open exceptions reference this order."
                    )
                },
            ]
        }
    )
    result = ask(conn, run_id, question, mock)
    check(result["tool_calls"] == [{"name": "trace_order", "input": {"order_id": "ORD-1017"}}], "Q2: expected exactly one trace_order call")
    for rid in ("ORD-1017", "PAY-0017", "V-20260805-SETL"):
        check(rid in result["record_ids"], f"Q2: {rid} must be a cited record id")

    # independent SQL verification
    order = conn.execute("SELECT amount_p, method FROM orders WHERE order_id = 'ORD-1017'").fetchone()
    check(order == (1_244_700, "card"), f"Q2 SQL: ORD-1017 should be ₹12,447.00 card, got {order}")
    capture = conn.execute(
        "SELECT settlement_id, utr FROM gw_payments WHERE payment_id = 'PAY-0017'"
    ).fetchone()
    check(capture == ("SETL_0805", "UTIB09737631165"), f"Q2 SQL: PAY-0017's settlement/utr mismatch: {capture}")
    hop_statuses = conn.execute(
        "SELECT hop, status FROM match_link WHERE run_id = ? AND (id_a = 'ORD-1017' OR id_a = 'PAY-0017' OR id_a = 'setl_0805')",
        (run_id,),
    ).fetchall()
    check(all(status == "accepted" for _hop, status in hop_statuses), f"Q2 SQL: all hops should be accepted, got {hop_statuses}")
    check(len(hop_statuses) == 3, f"Q2 SQL: expected hop1+hop2+hop3 (3 links), got {len(hop_statuses)}")


def question_3(conn: sqlite3.Connection, run_id: str) -> None:
    question = "What can't you reconcile, and what's it worth?"
    mock = MockLLM(
        qa_script={
            "can't you reconcile": [
                {"tool_call": {"name": "list_exceptions", "input": {}}},
                {"tool_call": {"name": "cash_position", "input": {"as_of": config.DATE_TO.isoformat()}}},
                {
                    "answer": (
                        "17 open exceptions (5 critical / 10 warn / 2 info), but the amount that's actually "
                        "unreconciled against the GL clearing account is ₹1,36,400.00 — the same number the "
                        "pipeline's own V5 clearing control checks. The rest (e.g. FEE_VARIANCE, "
                        "GL_DECOMPOSITION_FAIL) are real issues but don't represent uncleared cash."
                    )
                },
            ]
        }
    )
    result = ask(conn, run_id, question, mock)
    check(
        [c["name"] for c in result["tool_calls"]] == ["list_exceptions", "cash_position"],
        f"Q3: expected list_exceptions then cash_position, got {[c['name'] for c in result['tool_calls']]}",
    )

    # independent SQL / cross-module verification — the actual G5 headline check
    exposure_p, _breakdown = verifier.compute_exposure_p(conn, run_id)
    residual_p = verifier.compute_residual_p(conn)
    check(exposure_p == residual_p, f"Q3 SQL: V5 residual ({residual_p}) != exposure ({exposure_p})")
    check(exposure_p == 13_640_000, f"Q3 SQL: expected ₹1,36,400.00 unreconciled, got {exposure_p}p")
    open_count = conn.execute("SELECT COUNT(*) FROM exceptions WHERE run_id = ? AND status = 'open'", (run_id,)).fetchone()[0]
    check(open_count == 17, f"Q3 SQL: expected 17 open exceptions, got {open_count}")


def question_4(conn: sqlite3.Connection, run_id: str) -> None:
    question = "How much cash lands next Monday (17 Aug)?"
    mock = MockLLM(
        qa_script={
            "next Monday": [
                {"tool_call": {"name": "cash_position", "input": {"as_of": config.DATE_TO.isoformat()}}},
                {
                    "answer": (
                        "Batch SETL_0813 is expected to settle on Monday 2026-08-17, bringing in ₹1,18,139.31. "
                        "(A second batch, SETL_0814, lands the next day, 2026-08-18, not this Monday.)"
                    )
                },
            ]
        }
    )
    result = ask(conn, run_id, question, mock)
    check(
        result["tool_calls"] == [{"name": "cash_position", "input": {"as_of": config.DATE_TO.isoformat()}}],
        "Q4: expected exactly one cash_position call",
    )
    check("SETL_0813" in result["record_ids"], "Q4: SETL_0813 must be a cited record id")

    # independent SQL verification
    monday_rows = conn.execute(
        "SELECT kind, amount_p, fee_p, gst_p FROM gw_payments WHERE settlement_id = 'SETL_0813'"
    ).fetchall()
    check(len(monday_rows) > 0, "Q4 SQL: SETL_0813 should have rows")
    from recon import moneymath

    net = sum(
        moneymath.net_p(amount_p, fee_p, gst_p) if kind == "capture" else amount_p
        for kind, amount_p, fee_p, gst_p in monday_rows
    )
    check(net == 11_813_931, f"Q4 SQL: SETL_0813 net should be ₹1,18,139.31, got {net}p")
    earliest = min(
        r[0] for r in conn.execute("SELECT captured_on FROM gw_payments WHERE settlement_id = 'SETL_0813'")
    )
    from datetime import date

    from recon import busdays

    expected_settle = busdays.add_bdays(date.fromisoformat(earliest), config.SETTLEMENT_LAG_BDAYS)
    check(expected_settle.isoformat() == "2026-08-17", f"Q4 SQL: expected settle date should be 2026-08-17, got {expected_settle}")
    check(expected_settle.weekday() == 0, "Q4 SQL: 2026-08-17 should actually be a Monday")


def question_5(conn: sqlite3.Connection, run_id: str) -> None:
    question = "Which two settlements can't you tell apart, and why?"
    mock = MockLLM(
        qa_script={
            "can't you tell apart": [
                {"tool_call": {"name": "list_exceptions", "input": {"code": "AMBIGUOUS_SETTLEMENT"}}},
                {
                    "answer": (
                        "bl_d02_0810a and bl_d02_0810b — both credit exactly ₹50,738.58 on the same value_date, "
                        "and each reconstructs to two disjoint candidate subsets of gateway rows via subset-sum. "
                        "There's no arithmetic tiebreak, so both stand as AMBIGUOUS_SETTLEMENT; the engine "
                        "refuses to guess which gateway rows belong to which bank line."
                    )
                },
            ]
        }
    )
    result = ask(conn, run_id, question, mock)
    check(
        result["tool_calls"] == [{"name": "list_exceptions", "input": {"code": "AMBIGUOUS_SETTLEMENT"}}],
        "Q5: expected exactly one list_exceptions(code=AMBIGUOUS_SETTLEMENT) call",
    )
    for rid in ("bl_d02_0810a", "bl_d02_0810b"):
        check(rid in result["record_ids"], f"Q5: {rid} must be a cited record id")

    # independent SQL verification
    lines = conn.execute(
        "SELECT line_id, credit_p, value_date FROM bank_lines WHERE line_id LIKE 'bl_d02_%' ORDER BY line_id"
    ).fetchall()
    check(len(lines) == 2, f"Q5 SQL: expected exactly 2 D-02 bank lines, got {len(lines)}")
    check(lines[0][1] == lines[1][1] == 5_073_858, f"Q5 SQL: both should credit ₹50,738.58, got {lines}")
    check(lines[0][2] == lines[1][2], f"Q5 SQL: both should share a value_date, got {lines}")
    exc_count = conn.execute(
        "SELECT COUNT(*) FROM exceptions WHERE run_id = ? AND code = 'AMBIGUOUS_SETTLEMENT' AND status = 'open'",
        (run_id,),
    ).fetchone()[0]
    check(exc_count == 2, f"Q5 SQL: expected 2 open AMBIGUOUS_SETTLEMENT exceptions, got {exc_count}")
    accepted_links = conn.execute(
        "SELECT COUNT(*) FROM match_link WHERE run_id = ? AND (id_b = 'bl_d02_0810a' OR id_b = 'bl_d02_0810b') AND status = 'accepted'",
        (run_id,),
    ).fetchone()[0]
    check(accepted_links == 0, "Q5 SQL: neither ambiguous line should ever have an accepted link")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_dir = tmp_path / "data"
        world, truth = generate_world(config.SEED, defects=True)
        write_csvs(world, data_dir)
        gt_path = data_dir / "ground_truth.json"
        write_ground_truth(truth, gt_path)
        db_path = tmp_path / "recon.db"
        migrate(db_path=db_path, migrations_dir=REPO_ROOT / "db" / "migrations")
        conn = sqlite3.connect(db_path)
        report = load_all(conn, data_dir)
        check(report.quarantined == [], f"loader quarantined rows: {report.quarantined}")
        conn.close()

        ctx = run_pipeline(db_path=db_path, ground_truth_path=gt_path, seed=config.SEED, llm_mode="off")
        run_id = ctx["run_id"]

        conn = sqlite3.connect(db_path)
        try:
            question_1(conn, run_id)
            question_2(conn, run_id)
            question_3(conn, run_id)
            question_4(conn, run_id)
            question_5(conn, run_id)
        finally:
            conn.close()

    print()
    for i, (q, a) in enumerate(ANSWERS, start=1):
        print(f"Q{i}: {q}")
        print(f"A{i}: {a}")
        print()

    if FAILURES:
        print(f"GATE G5: FAIL ({len(FAILURES)} issue(s))")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("GATE G5: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
