"""moneymath.py — paise helpers, fee+GST computation, rounding (SPEC §5.1).

Every function here works on plain Python `int` paise. No float, no
Decimal — ever (CLAUDE.md rule 1). `format_rupees` is the one place a
"₹x,xxx.xx" string gets built; call it only at display time.
"""

from __future__ import annotations


def round_half_up(numerator: int, denominator: int) -> int:
    """Round numerator/denominator to the nearest int, ties away from zero.

    Pure integer arithmetic — no float, no Decimal. `denominator` must be
    positive; `numerator` may be negative (rounds e.g. -0.5 -> -1).
    """
    if denominator <= 0:
        raise ValueError(f"round_half_up: denominator must be positive, got {denominator}")
    sign = -1 if numerator < 0 else 1
    n = abs(numerator)
    return sign * ((2 * n + denominator) // (2 * denominator))


def fee_p(amount_p: int, fee_bps: int) -> int:
    """Gateway fee in paise for a capture of amount_p at fee_bps basis points."""
    return round_half_up(amount_p * fee_bps, 10_000)


def gst_p(fee_amount_p: int, gst_bps_on_fee: int) -> int:
    """GST in paise levied on a fee amount, at gst_bps_on_fee basis points."""
    return round_half_up(fee_amount_p * gst_bps_on_fee, 10_000)


def net_p(amount_p: int, fee_amount_p: int, gst_amount_p: int) -> int:
    """Net settled amount in paise for one capture: amount - fee - gst."""
    return amount_p - fee_amount_p - gst_amount_p


def settlement_net_p(capture_net_ps: list[int], refund_amount_ps: list[int]) -> int:
    """Total settlement value for a batch: Σ net_p(captures) + Σ amount_p(refunds, negative)."""
    return sum(capture_net_ps) + sum(refund_amount_ps)


def format_rupees(amount_p: int) -> str:
    """Format paise as "₹x,xxx.xx" (Indian digit grouping), the only display conversion."""
    sign = "-" if amount_p < 0 else ""
    whole, frac = divmod(abs(amount_p), 100)
    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.append(head[-2:])
            head = head[:-2]
        parts.append(head)
        grouped = ",".join(reversed(parts)) + "," + tail
    return f"{sign}₹{grouped}.{frac:02d}"
