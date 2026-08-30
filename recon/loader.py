"""loader.py — normalisation + load into SQLite (SPEC §6.1).

Lives at the top of `recon/` rather than under `engine/` — `engine/pipeline.py`
(SPEC's intended home for "load → normalise → hop1 → ...") doesn't exist
until P2, so this stands alone now, same reasoning as `recon/db.py` in P0.

No float, no Decimal anywhere (CLAUDE.md rule 1) — amount parsing is pure
string/int arithmetic. A row that fails normalisation is quarantined (skipped)
and recorded on the returned `LoadReport`; the load continues (§6.1: "row
quarantined, pipeline continues"). Wiring quarantined rows into the
`exceptions` table waits for P2's `pipeline.py`/`run_id`.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from recon.db import DB_PATH

_CURRENCY_STRIP_RE = re.compile(r"[₹,\s]")
_UTR_RE = re.compile(r"[A-Z]{4}0?[A-Z0-9]{6,18}")


def normalise_amount_to_paise(raw: str) -> int:
    """Parse an amount string into integer paise — no float, no Decimal.

    Accepts our own plain-integer-paise convention ("150000") as well as a
    rupee-decimal display string with optional "₹" and thousands commas
    ("₹12,000.00") — SPEC §6.1's D-14b case. Rejects anything with more or
    fewer than 2 decimal digits ("doesn't round exactly to paise").
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("empty amount")
    sign = 1
    body = raw
    if body[0] in "+-":
        sign = -1 if body[0] == "-" else 1
        body = body[1:]
    if not body:
        raise ValueError(f"not a valid amount: {raw!r}")
    has_decimal_style = "." in body or "," in body or "₹" in body
    cleaned = _CURRENCY_STRIP_RE.sub("", body)
    if not cleaned:
        raise ValueError(f"not a valid amount: {raw!r}")
    if has_decimal_style:
        whole, sep, frac = cleaned.partition(".")
        if not whole.isdigit():
            raise ValueError(f"not a valid amount: {raw!r}")
        if sep == "":
            frac = "00"
        if not frac.isdigit() or len(frac) != 2:
            raise ValueError(f"amount does not round exactly to paise: {raw!r}")
        paise = int(whole) * 100 + int(frac)
    else:
        if not cleaned.isdigit():
            raise ValueError(f"not a valid amount: {raw!r}")
        paise = int(cleaned)
    return sign * paise


def extract_utr(narration: str) -> str | None:
    """Extract a UTR-shaped token from a bank narration (SPEC §6.1 regex), else None.

    SPEC's own narration template puts the real UTR last ("RAZORPAY
    SETTLEMENT <setl_id> <utr>"), and the regex is loose enough to also
    match plain words like "SETTLEMENT" earlier in the string — so this
    takes the *last* match, not the first (SPEC doesn't say which; "last"
    is the only choice that ever recovers the real UTR from that template).
    """
    matches = _UTR_RE.findall(narration.upper())
    return matches[-1] if matches else None


def normalise_reference(raw: str) -> str:
    """Trim + uppercase a reference field (UTR, settlement id) — SPEC §6.1."""
    return raw.strip().upper()


def normalise_date(raw: str) -> str:
    """Parse to ISO-8601; raises ValueError on anything else (ISO in, ISO out)."""
    return date.fromisoformat(raw.strip()).isoformat()


@dataclass
class QuarantinedRow:
    table: str
    row_index: int  # 1-based, excluding header
    reason: str


@dataclass
class LoadReport:
    rows_loaded: dict[str, int] = field(default_factory=dict)
    quarantined: list[QuarantinedRow] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"  {table}: {n} rows loaded" for table, n in self.rows_loaded.items()]
        if self.quarantined:
            lines.append(f"  {len(self.quarantined)} row(s) quarantined:")
            for q in self.quarantined:
                lines.append(f"    {q.table}[{q.row_index}]: {q.reason}")
        return "\n".join(lines)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_orders(conn: sqlite3.Connection, data_dir: Path, report: LoadReport) -> None:
    rows = _read_rows(data_dir / "orders.csv")
    loaded = 0
    for i, row in enumerate(rows, start=1):
        try:
            conn.execute(
                "INSERT INTO orders (order_id, customer, amount_p, method, status, created_on) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["order_id"].strip(),
                    row["customer"].strip(),
                    normalise_amount_to_paise(row["amount_p"]),
                    row["method"].strip(),
                    row["status"].strip(),
                    normalise_date(row["created_on"]),
                ),
            )
            loaded += 1
        except (ValueError, sqlite3.Error) as exc:
            report.quarantined.append(QuarantinedRow("orders", i, str(exc)))
    report.rows_loaded["orders"] = loaded


def _load_gateway(conn: sqlite3.Connection, data_dir: Path, report: LoadReport) -> None:
    rows = _read_rows(data_dir / "gateway.csv")
    loaded = 0
    for i, row in enumerate(rows, start=1):
        try:
            order_id = row["order_id"].strip() or None
            settlement_id = row["settlement_id"].strip() or None
            utr = row["utr"].strip() or None
            conn.execute(
                "INSERT INTO gw_payments "
                "(payment_id, order_id, kind, amount_p, fee_p, gst_p, method, captured_on, settlement_id, utr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["payment_id"].strip(),
                    order_id,
                    row["kind"].strip(),
                    normalise_amount_to_paise(row["amount_p"]),
                    normalise_amount_to_paise(row["fee_p"]),
                    normalise_amount_to_paise(row["gst_p"]),
                    row["method"].strip(),
                    normalise_date(row["captured_on"]),
                    normalise_reference(settlement_id) if settlement_id else None,
                    normalise_reference(utr) if utr else None,
                ),
            )
            loaded += 1
        except (ValueError, sqlite3.Error) as exc:
            report.quarantined.append(QuarantinedRow("gateway", i, str(exc)))
    report.rows_loaded["gw_payments"] = loaded


def _load_bank(conn: sqlite3.Connection, data_dir: Path, report: LoadReport) -> None:
    rows = _read_rows(data_dir / "bank.csv")
    loaded = 0
    for i, row in enumerate(rows, start=1):
        try:
            narration = row["narration"].strip()
            conn.execute(
                "INSERT INTO bank_lines (line_id, value_date, narration, credit_p, debit_p, utr_extracted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["line_id"].strip(),
                    normalise_date(row["value_date"]),
                    narration,
                    normalise_amount_to_paise(row["credit_p"]),
                    normalise_amount_to_paise(row["debit_p"]),
                    extract_utr(narration),
                ),
            )
            loaded += 1
        except (ValueError, sqlite3.Error) as exc:
            report.quarantined.append(QuarantinedRow("bank", i, str(exc)))
    report.rows_loaded["bank_lines"] = loaded


def _load_gl(conn: sqlite3.Connection, data_dir: Path, report: LoadReport) -> None:
    rows = _read_rows(data_dir / "gl.csv")
    loaded = 0
    for i, row in enumerate(rows, start=1):
        try:
            conn.execute(
                "INSERT INTO gl_entries (voucher_no, line_no, entry_date, account, debit_p, credit_p, memo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["voucher_no"].strip(),
                    int(row["line_no"]),
                    normalise_date(row["entry_date"]),
                    row["account"].strip(),
                    normalise_amount_to_paise(row["debit_p"]),
                    normalise_amount_to_paise(row["credit_p"]),
                    row["memo"].strip(),
                ),
            )
            loaded += 1
        except (ValueError, sqlite3.Error) as exc:
            report.quarantined.append(QuarantinedRow("gl", i, str(exc)))
    report.rows_loaded["gl_entries"] = loaded


def load_all(conn: sqlite3.Connection, data_dir: Path | str = Path("data")) -> LoadReport:
    """Normalise and load data/*.csv into the already-migrated SQLite DB.

    Each table's rows are inserted in their own try/except per row — a bad
    row is quarantined and skipped, the load continues (SPEC §6.1).
    """
    data_dir = Path(data_dir)
    report = LoadReport()
    _load_orders(conn, data_dir, report)
    _load_gateway(conn, data_dir, report)
    _load_bank(conn, data_dir, report)
    _load_gl(conn, data_dir, report)
    conn.commit()
    return report


if __name__ == "__main__":
    import recon.db as db

    connection = db.connect(DB_PATH)
    result = load_all(connection, Path("data"))
    print(result.summary())
