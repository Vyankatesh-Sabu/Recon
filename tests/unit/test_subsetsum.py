"""test_subsetsum.py — T-3: unique / multiple / none / over-cap / negatives."""

from __future__ import annotations

import time

from recon.engine.subsetsum import Multiple, NoSolution, Unique, reconstruct


def test_unique_solution():
    items = [("a", 500), ("b", 300), ("c", 150), ("d", 90)]
    # only {a, c} == 650
    result = reconstruct(target_p=650, items=items, tol_p=0)
    assert isinstance(result, Unique)
    assert dict(result.subset) == {"a": 500, "c": 150}


def test_no_solution():
    items = [("a", 500), ("b", 300), ("c", 150)]
    result = reconstruct(target_p=999_999, items=items, tol_p=0)
    assert isinstance(result, NoSolution)
    assert result.reason == "exhausted"


def test_multiple_solutions_two_disjoint_subsets():
    # {a, b} == 800 and {c, d} == 800 — two genuinely different subsets.
    items = [("a", 500), ("b", 300), ("c", 450), ("d", 350)]
    result = reconstruct(target_p=800, items=items, tol_p=0)
    assert isinstance(result, Multiple)
    set_a = frozenset(dict(result.subset_a).items())
    set_b = frozenset(dict(result.subset_b).items())
    assert set_a != set_b
    assert sum(v for _, v in result.subset_a) == 800
    assert sum(v for _, v in result.subset_b) == 800


def test_over_cap_refuses_without_searching():
    items = [(f"row{i}", 100) for i in range(13)]  # 13 > max_items=12
    start = time.monotonic()
    result = reconstruct(target_p=1300, items=items, tol_p=0, max_items=12)
    elapsed = time.monotonic() - start
    assert result == NoSolution(reason="over_cap")
    assert elapsed < 0.05  # refused immediately, no 2^13 search


def test_over_cap_even_when_a_solution_exists_among_all_items():
    # A trivial solution (all 13 items) exists, but the cap must still refuse.
    items = [(f"row{i}", 100) for i in range(13)]
    result = reconstruct(target_p=1300, items=items, tol_p=0, max_items=12)
    assert result == NoSolution(reason="over_cap")


def test_exactly_at_cap_is_allowed():
    items = [(f"row{i}", 100) for i in range(12)]
    result = reconstruct(target_p=1200, items=items, tol_p=0, max_items=12)
    assert isinstance(result, Unique)


def test_negative_items_refunds_and_chargebacks():
    # 3 distinct-amount captures, one refund, one chargeback; the target is
    # only reachable using all 5 rows together (no sub-combination hits it).
    items = [("cap1", 900), ("cap2", 950), ("cap3", 1050), ("refund", -700), ("chargeback", -300)]
    result = reconstruct(target_p=1900, items=items, tol_p=0)
    assert isinstance(result, Unique)
    assert dict(result.subset) == {"cap1": 900, "cap2": 950, "cap3": 1050, "refund": -700, "chargeback": -300}


def test_negative_items_no_solution():
    items = [("cap1", 1000), ("refund", -700)]
    result = reconstruct(target_p=5000, items=items, tol_p=0)
    assert isinstance(result, NoSolution)


def test_tolerance_boundary():
    items = [("a", 500), ("b", 301)]  # sums to 801
    assert isinstance(reconstruct(target_p=800, items=items, tol_p=1), Unique)  # delta==1==tol
    assert isinstance(reconstruct(target_p=800, items=items, tol_p=0), NoSolution)  # delta==1>tol


def test_empty_items():
    assert reconstruct(target_p=0, items=[], tol_p=0) == Unique(subset=())
    assert isinstance(reconstruct(target_p=100, items=[], tol_p=0), NoSolution)
