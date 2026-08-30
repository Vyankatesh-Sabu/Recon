"""test_moneymath.py — table-driven tests incl. paisa rounding edge cases (T-2)."""

import pytest

from recon import moneymath


@pytest.mark.parametrize(
    "numerator, denominator, expected",
    [
        (0, 100, 0),
        (50, 100, 1),  # exact tie: 0.5 -> 1 (half-up)
        (49, 100, 0),
        (51, 100, 1),
        (150, 100, 2),  # exact tie: 1.5 -> 2
        (250, 100, 3),  # exact tie: 2.5 -> 3 (always away from zero, not banker's)
        (-50, 100, -1),  # ties away from zero on the negative side too
        (-49, 100, 0),
        (-51, 100, -1),
        (1, 3, 0),
        (2, 3, 1),
        (10_00, 200, 5),  # 1000/200 exact
        (1, 1, 1),
    ],
)
def test_round_half_up(numerator, denominator, expected):
    assert moneymath.round_half_up(numerator, denominator) == expected


def test_round_half_up_rejects_nonpositive_denominator():
    with pytest.raises(ValueError):
        moneymath.round_half_up(100, 0)
    with pytest.raises(ValueError):
        moneymath.round_half_up(100, -5)


@pytest.mark.parametrize(
    "amount_p, fee_bps, expected_fee_p",
    [
        (0, 200, 0),
        (100_00, 200, 200),  # ₹100 at 2% = ₹2.00 = 200p
        (100_00, 0, 0),  # upi: zero fee
        (150_00, 150, 225),  # ₹150 at 1.5% = ₹2.25 = 225p
        (1, 200, 0),  # 1p * 200bps / 10000 = 0.02 -> rounds to 0
        (25, 200, 1),  # 25p * 200/10000 = 0.5 -> rounds up to 1 (half-up)
    ],
)
def test_fee_p(amount_p, fee_bps, expected_fee_p):
    assert moneymath.fee_p(amount_p, fee_bps) == expected_fee_p


@pytest.mark.parametrize(
    "fee_amount_p, gst_bps, expected_gst_p",
    [
        (0, 1800, 0),
        (200, 1800, 36),  # 200 * 1800/10000 = 36 exact
        (100, 1800, 18),  # 100*1800/10000 = 18 exact
        (1, 1800, 0),  # 1*1800/10000 = 0.18 -> 0
        (5, 1800, 1),  # 5*1800/10000 = 0.9 -> 1
    ],
)
def test_gst_p(fee_amount_p, gst_bps, expected_gst_p):
    assert moneymath.gst_p(fee_amount_p, gst_bps) == expected_gst_p


def test_net_p():
    assert moneymath.net_p(100_00, 200, 36) == 100_00 - 200 - 36


def test_settlement_net_p():
    assert moneymath.settlement_net_p([9764, 5000], [-2000]) == 9764 + 5000 - 2000
    assert moneymath.settlement_net_p([], []) == 0


@pytest.mark.parametrize(
    "amount_p, expected",
    [
        (0, "₹0.00"),
        (1, "₹0.01"),
        (100, "₹1.00"),
        (150_00, "₹150.00"),
        (999_00, "₹999.00"),
        (1_000_00, "₹1,000.00"),
        (12_000_00, "₹12,000.00"),
        (1_23_456_00, "₹1,23,456.00"),  # Indian digit grouping
        (-150_00, "-₹150.00"),
    ],
)
def test_format_rupees(amount_p, expected):
    assert moneymath.format_rupees(amount_p) == expected
