"""test_loader.py — D-14 fixtures normalise with zero quarantine (T-8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon.db import migrate
from recon.loader import extract_utr, load_all, normalise_amount_to_paise


@pytest.mark.parametrize(
    "raw, expected_p",
    [
        ("150000", 150000),
        ("-150000", -150000),
        ("0", 0),
        ("12,000.00", 1_200_000),  # D-14b: comma rupee string
        ("₹12,000.00", 1_200_000),
        ("286.00", 28600),
        ("1,000", 100000),  # comma, no decimal -> still rupee-format
    ],
)
def test_normalise_amount_to_paise_accepts(raw, expected_p):
    assert normalise_amount_to_paise(raw) == expected_p


@pytest.mark.parametrize("raw", ["", "abc", "12,000.005", "12.5.6", "₹"])
def test_normalise_amount_to_paise_rejects(raw):
    with pytest.raises(ValueError):
        normalise_amount_to_paise(raw)


def test_extract_utr_prefers_last_match_over_english_words():
    # SPEC's own regex also matches plain words like "SETTLEMENT" earlier
    # in the narration; the real UTR must still win.
    assert extract_utr("RAZORPAY SETTLEMENT setl_0805 UTIB09737631165") == "UTIB09737631165"


def test_extract_utr_case_insensitive_and_whitespace_tolerant():
    assert extract_utr("  Razorpay Settlement setl_0811 Utib06773602606  ") == "UTIB06773602606"


def test_extract_utr_none_when_no_plausible_token():
    assert extract_utr("123456") is None
    assert extract_utr("ab cd") is None


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def test_d14_fixtures_load_with_zero_quarantine(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_csv(
        data_dir / "orders.csv",
        ["order_id", "customer", "amount_p", "method", "status", "created_on"],
        [["ORD-1001", "CUST-001", "150000", "card", "confirmed", "2026-08-03"]],
    )
    _write_csv(
        data_dir / "gateway.csv",
        [
            "payment_id",
            "order_id",
            "kind",
            "amount_p",
            "fee_p",
            "gst_p",
            "method",
            "captured_on",
            "settlement_id",
            "utr",
        ],
        [
            # D-14b: comma rupee string amount
            ["PAY-0001", "ORD-1001", "capture", "12,000.00", "2400", "432", "nb", "2026-08-03", "setl_0803", "UTIB01111111111"],
        ],
    )
    _write_csv(
        data_dir / "bank.csv",
        ["line_id", "value_date", "narration", "credit_p", "debit_p"],
        [
            # D-14a: mixed-case UTR + surrounding whitespace
            ["setl_0803", "2026-08-05", "  RAZORPAY SETTLEMENT setl_0803 Utib01111111111  ", "1197168", "0"],
        ],
    )
    _write_csv(
        data_dir / "gl.csv",
        ["voucher_no", "line_no", "entry_date", "account", "debit_p", "credit_p", "memo"],
        [["V-1", "1", "2026-08-03", "PG_RECEIVABLE", "150000", "0", ""], ["V-1", "2", "2026-08-03", "SALES", "0", "150000", ""]],
    )

    db_path = tmp_path / "recon.db"
    repo_root = Path(__file__).resolve().parents[2]
    migrate(db_path=db_path, migrations_dir=repo_root / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    try:
        report = load_all(conn, data_dir)
        assert report.quarantined == []
        row = conn.execute("SELECT amount_p FROM gw_payments WHERE payment_id = 'PAY-0001'").fetchone()
        assert row[0] == 1_200_000
        row = conn.execute("SELECT utr_extracted FROM bank_lines WHERE line_id = 'setl_0803'").fetchone()
        assert row[0] == "UTIB01111111111"
    finally:
        conn.close()


def test_genuinely_bad_row_is_quarantined_not_fatal(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_csv(
        data_dir / "orders.csv",
        ["order_id", "customer", "amount_p", "method", "status", "created_on"],
        [
            ["ORD-1001", "CUST-001", "150000", "card", "confirmed", "2026-08-03"],
            ["ORD-1002", "CUST-002", "not-a-number", "card", "confirmed", "2026-08-03"],  # bad amount
            ["ORD-1003", "CUST-003", "150000", "card", "confirmed", "not-a-date"],  # bad date
        ],
    )
    for name, header in (
        ("gateway.csv", ["payment_id", "order_id", "kind", "amount_p", "fee_p", "gst_p", "method", "captured_on", "settlement_id", "utr"]),
        ("bank.csv", ["line_id", "value_date", "narration", "credit_p", "debit_p"]),
        ("gl.csv", ["voucher_no", "line_no", "entry_date", "account", "debit_p", "credit_p", "memo"]),
    ):
        _write_csv(data_dir / name, header, [])

    db_path = tmp_path / "recon.db"
    repo_root = Path(__file__).resolve().parents[2]
    migrate(db_path=db_path, migrations_dir=repo_root / "db" / "migrations")
    conn = sqlite3.connect(db_path)
    try:
        report = load_all(conn, data_dir)
        assert report.rows_loaded["orders"] == 1
        assert len(report.quarantined) == 2
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    finally:
        conn.close()
