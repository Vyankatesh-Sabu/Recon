"""busdays.py — business-day arithmetic (SPEC §0.8).

All functions take `weekends`/`holidays` explicitly rather than importing
config directly, so they stay independently testable; callers pass
config.WEEKENDS / config.HOLIDAYS.
"""

from __future__ import annotations

from datetime import date, timedelta

import config


def is_business_day(
    d: date, weekends: set[int] = config.WEEKENDS, holidays: set[date] = config.HOLIDAYS
) -> bool:
    """True if d is not a weekend (per weekends, date.weekday() values) and not a holiday."""
    return d.weekday() not in weekends and d not in holidays


def add_bdays(
    d: date,
    n: int,
    weekends: set[int] = config.WEEKENDS,
    holidays: set[date] = config.HOLIDAYS,
) -> date:
    """Move n business days forward (n >= 0) or backward (n < 0) from d.

    d itself need not be a business day; only the steps taken are counted
    against business days.
    """
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    cur = d
    while remaining > 0:
        cur += timedelta(days=step)
        if is_business_day(cur, weekends, holidays):
            remaining -= 1
    return cur


def bday_diff(
    d1: date,
    d2: date,
    weekends: set[int] = config.WEEKENDS,
    holidays: set[date] = config.HOLIDAYS,
) -> int:
    """Number of business days from d1 to d2 (positive if d2 is later), counting steps only."""
    if d1 == d2:
        return 0
    step = 1 if d2 > d1 else -1
    cur = d1
    count = 0
    while cur != d2:
        cur += timedelta(days=step)
        if is_business_day(cur, weekends, holidays):
            count += step
    return count


def business_days_in_range(
    start: date,
    end: date,
    weekends: set[int] = config.WEEKENDS,
    holidays: set[date] = config.HOLIDAYS,
) -> list[date]:
    """All business days in [start, end] inclusive, in order."""
    days = []
    cur = start
    while cur <= end:
        if is_business_day(cur, weekends, holidays):
            days.append(cur)
        cur += timedelta(days=1)
    return days
