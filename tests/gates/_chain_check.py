"""_chain_check.py — brute-force chain checker for gate G1(b).

Completely independent of `recon/engine/*` (which doesn't exist until P2
anyway) and of `recon/generator/*`'s own bookkeeping — it re-derives every
non-COD order's full chain (order -> capture -> settlement -> GL) straight
from the loaded SQLite tables, using only `moneymath`/`busdays`, and does not
consult `ground_truth.json` at all. Used against the `--no-defects` world
only: SPEC's clean world must reconcile fully by construction.

Returns a list of violation strings; empty means every check passed.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import config
from recon import busdays, moneymath


def _fetchone(conn: sqlite3.Connection, sql: str, params: tuple) -> tuple | None:
    return conn.execute(sql, params).fetchone()


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def _check_voucher_balances(conn: sqlite3.Connection) -> list[str]:
    violations = []
    for voucher_no, debit, credit in _fetchall(
        conn, "SELECT voucher_no, SUM(debit_p), SUM(credit_p) FROM gl_entries GROUP BY voucher_no"
    ):
        if debit != credit:
            violations.append(f"voucher {voucher_no} does not balance: debit={debit} credit={credit}")
    return violations


def _check_refund_vouchers(conn: sqlite3.Connection) -> list[str]:
    violations = []
    for payment_id, amount_p, captured_on in _fetchall(
        conn, "SELECT payment_id, amount_p, captured_on FROM gw_payments WHERE kind = 'refund'"
    ):
        amt = abs(amount_p)
        row = _fetchone(
            conn,
            "SELECT voucher_no FROM gl_entries WHERE entry_date = ? AND account = 'SALES_RETURNS' AND debit_p = ?",
            (captured_on, amt),
        )
        if row is None:
            violations.append(f"refund {payment_id}: no SALES_RETURNS GL voucher for {amt}p on {captured_on}")
    return violations


def _check_daily_capture_vouchers(conn: sqlite3.Connection) -> list[str]:
    violations = []
    for day, total in _fetchall(
        conn,
        "SELECT captured_on, SUM(amount_p) FROM gw_payments WHERE kind = 'capture' GROUP BY captured_on",
    ):
        row = _fetchone(
            conn,
            "SELECT debit_p FROM gl_entries WHERE entry_date = ? AND account = 'PG_RECEIVABLE' AND debit_p = ?",
            (day, total),
        )
        if row is None:
            violations.append(f"day {day}: no daily-capture PG_RECEIVABLE voucher for total {total}p")
    return violations


def check_clean_world_chains(conn: sqlite3.Connection) -> list[str]:
    violations: list[str] = []
    violations += _check_voucher_balances(conn)
    violations += _check_refund_vouchers(conn)
    violations += _check_daily_capture_vouchers(conn)

    for order_id, amount_p, method, created_on in _fetchall(
        conn, "SELECT order_id, amount_p, method, created_on FROM orders"
    ):
        if method == "cod":
            n = _fetchone(conn, "SELECT COUNT(*) FROM gw_payments WHERE order_id = ?", (order_id,))[0]
            if n != 0:
                violations.append(f"order {order_id}: cod order has {n} gateway rows, expected 0")
            continue

        captures = _fetchall(
            conn, "SELECT payment_id, amount_p, fee_p, gst_p, captured_on, settlement_id, utr "
            "FROM gw_payments WHERE order_id = ? AND kind = 'capture'",
            (order_id,),
        )
        if len(captures) != 1:
            violations.append(f"order {order_id}: expected exactly 1 capture, found {len(captures)}")
            continue
        payment_id, cap_amount_p, fee_p, gst_p, captured_on_s, settlement_id, utr = captures[0]
        if cap_amount_p != amount_p:
            violations.append(f"order {order_id}: capture {payment_id} amount {cap_amount_p} != order {amount_p}")

        captured_on = date.fromisoformat(captured_on_s)
        expected_settle = busdays.add_bdays(captured_on, config.SETTLEMENT_LAG_BDAYS)

        if expected_settle > config.DATE_TO:
            if utr is not None:
                violations.append(f"order {order_id}: capture {payment_id} is in-transit but has a utr")
            continue  # legitimately unsettled — SPEC §5.2.7, not a failure

        if utr is None:
            violations.append(f"order {order_id}: capture {payment_id} should be settled but has no utr")
            continue

        bank_lines = _fetchall(
            conn, "SELECT line_id, value_date, credit_p FROM bank_lines WHERE utr_extracted = ?", (utr,)
        )
        if len(bank_lines) != 1:
            violations.append(f"order {order_id}: expected exactly 1 bank line for utr {utr}, found {len(bank_lines)}")
            continue
        line_id, value_date_s, credit_p = bank_lines[0]
        if value_date_s != expected_settle.isoformat():
            violations.append(
                f"order {order_id}: bank line {line_id} value_date {value_date_s} != expected {expected_settle}"
            )

        batch_rows = _fetchall(
            conn,
            "SELECT kind, amount_p, fee_p, gst_p FROM gw_payments WHERE settlement_id = ?",
            (settlement_id,),
        )
        net = 0
        fee_total = 0
        gst_total = 0
        receivable_total = 0
        for kind, a_p, f_p, g_p in batch_rows:
            receivable_total += a_p
            if kind == "capture":
                net += moneymath.net_p(a_p, f_p, g_p)
                fee_total += f_p
                gst_total += g_p
            else:
                net += a_p
        if net != credit_p:
            violations.append(f"order {order_id}: batch net {net} != bank line {line_id} credit {credit_p}")

        # voucher_no isn't derivable from line_id alone (checker stays
        # independent of the generator's naming convention) — find it by
        # its BANK debit line matching this settlement's date and amount.
        settl_voucher_rows = _fetchall(
            conn,
            "SELECT voucher_no FROM gl_entries WHERE account = 'BANK' AND debit_p = ? AND entry_date = ?",
            (credit_p, value_date_s),
        )
        if len(settl_voucher_rows) != 1:
            violations.append(
                f"order {order_id}: expected exactly 1 settlement voucher for bank line {line_id}, "
                f"found {len(settl_voucher_rows)}"
            )
            continue
        voucher_no = settl_voucher_rows[0][0]
        lines = {
            account: (debit_p, credit_p_)
            for account, debit_p, credit_p_ in _fetchall(
                conn, "SELECT account, debit_p, credit_p FROM gl_entries WHERE voucher_no = ?", (voucher_no,)
            )
        }
        if lines.get("FEE_EXPENSE", (None, None))[0] != fee_total:
            violations.append(f"order {order_id}: voucher {voucher_no} FEE_EXPENSE != recomputed {fee_total}")
        if lines.get("INPUT_GST", (None, None))[0] != gst_total:
            violations.append(f"order {order_id}: voucher {voucher_no} INPUT_GST != recomputed {gst_total}")
        if lines.get("PG_RECEIVABLE", (None, None))[1] != receivable_total:
            violations.append(
                f"order {order_id}: voucher {voucher_no} PG_RECEIVABLE credit != recomputed {receivable_total}"
            )

    return violations
