"""config.py — seed, tolerances, fee card, dates, holidays, chart of accounts.

Every constant the generator/loader needs (SPEC §5.1, §5.2, §5.3, §4) lives
here — nowhere else in the codebase should contain a bare magic number for
an amount, count, rate, or date range that SPEC pins down.
"""

from __future__ import annotations

from datetime import date

# --- SPEC §5.1 fixed parameters, verbatim ---
SEED = 42
DATE_FROM = date(2026, 8, 3)  # Mon
DATE_TO = date(2026, 8, 14)  # Fri
SETTLEMENT_LAG_BDAYS = 2
FEE_BPS = {"card": 200, "upi": 0, "nb": 150}
GST_BPS_ON_FEE = 1800
AMOUNT_TOL_P = 100
DATE_WINDOW_BDAYS = 3
SUBSET_MAX_ITEMS = 12
METHOD_MIX = {"card": 0.45, "upi": 0.40, "nb": 0.15}
AMOUNT_RANGE_P = (150_00, 25_000_00)

# --- Business-day calendar (SPEC §0.8) ---
WEEKENDS = {5, 6}  # Sat, Sun — date.weekday() values
HOLIDAYS: set[date] = set()  # empty for the demo dataset; function must still exist

# --- Chart of accounts (SPEC §4), fixed strings ---
CHART_OF_ACCOUNTS = [
    "BANK",
    "PG_RECEIVABLE",
    "SALES",
    "SALES_RETURNS",
    "FEE_EXPENSE",
    "INPUT_GST",
    "BANK_CHARGES",
    "CHARGEBACK_LOSS",
    "SUSPENSE",
]

# --- World shape (SPEC §5.2) ---
N_ORDERS = 60
N_COD_ORDERS = 2
ORDERS_PER_DAY_RANGE = (4, 9)  # inclusive
N_REFUNDS = 4
REFUND_OFFSET_BDAYS_RANGE = (1, 5)  # inclusive, business days after capture
MAINTENANCE_CHARGE_P = 236_00
DIRECT_NEFT_CREDIT_CLEAN_P = 5_000_00  # clean-world placeholder; D-07 overwrites it

# --- Defect parameters (SPEC §5.3) ---
# "Count" column verbatim — how many records/instances each injector touches.
DEFECT_COUNTS = {
    "D-01": 1,
    "D-02": 1,  # "1 pair" -> 2 bank lines / settlements
    "D-03": 2,
    "D-04": 1,
    "D-05": 1,
    "D-06": 2,
    "D-07": 1,
    "D-08": 1,
    "D-09": 1,
    "D-10": 1,
    "D-11": 1,
    "D-12": 1,
    "D-13": 1,
    "D-14": 2,
}
D01_BATCH_ROWS_RANGE = (5, 7)  # inclusive gateway-row count for D-01's target batch
D07_AMOUNT_P = 18_000_00
D10_WRONG_FEE_BPS = 210  # vs FEE_BPS["card"] == 200
D12_ORDER_AMOUNT_P = 10_000_00
D12_CAPTURE_AMOUNT_P = 8_000_00
