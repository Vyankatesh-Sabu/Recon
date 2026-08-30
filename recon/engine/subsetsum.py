"""subsetsum.py — bounded DFS subset-sum reconstruction for hop-2 tier 2 (SPEC §6.3).

Pure, standalone, no DB/config dependency — hop2.py is the only caller.
Deterministic for a given input (no randomness). Correctness rule (CLAUDE.md
rule 6): MUST keep searching after the first hit until a second, distinct
solution is found or the space is exhausted — returning `Unique` when a
second solution exists is a false-match bug, not a performance trade-off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NoSolution:
    reason: str = "exhausted"  # or "over_cap" — refused without searching


@dataclass(frozen=True)
class Unique:
    subset: tuple[tuple[str, int], ...]  # ((row_id, net_p), ...)


@dataclass(frozen=True)
class Multiple:
    # The two distinct witnessing subsets found. Carrying them is reporting
    # the ambiguity, not resolving it — hop2.py must never prefer one.
    subset_a: tuple[tuple[str, int], ...]
    subset_b: tuple[tuple[str, int], ...]


Result = NoSolution | Unique | Multiple


def reconstruct(
    target_p: int, items: list[tuple[str, int]], tol_p: int, max_items: int = 12
) -> Result:
    """Find a subset of `items` (row_id, net_p) summing to target_p within tol_p.

    DFS over the include/exclude tree, sorted by descending |net_p|, pruned
    by the best-/worst-case sum still reachable from each remaining suffix.
    Refuses outright (NoSolution(reason="over_cap")) for more than
    max_items candidates — no search is attempted at all in that case.
    """
    if len(items) > max_items:
        return NoSolution(reason="over_cap")

    ordered = sorted(items, key=lambda it: -abs(it[1]))
    n = len(ordered)

    # suffix_pos[i] / suffix_neg[i]: best-case / worst-case additional sum
    # achievable from index i onward (only-positive / only-negative items).
    suffix_pos = [0] * (n + 1)
    suffix_neg = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        net = ordered[i][1]
        suffix_pos[i] = suffix_pos[i + 1] + (net if net > 0 else 0)
        suffix_neg[i] = suffix_neg[i + 1] + (net if net < 0 else 0)

    solutions: list[tuple[tuple[str, int], ...]] = []

    def dfs(i: int, current_sum: int, chosen: list[tuple[str, int]]) -> None:
        if len(solutions) >= 2:
            return
        if i == n:
            if abs(current_sum - target_p) <= tol_p:
                solutions.append(tuple(chosen))
            return
        lo = current_sum + suffix_neg[i]
        hi = current_sum + suffix_pos[i]
        if hi < target_p - tol_p or lo > target_p + tol_p:
            return  # unreachable even in the best/worst case — prune
        dfs(i + 1, current_sum, chosen)  # exclude items[i]
        if len(solutions) >= 2:
            return
        chosen.append(ordered[i])  # include items[i]
        dfs(i + 1, current_sum + ordered[i][1], chosen)
        chosen.pop()

    dfs(0, 0, [])

    if not solutions:
        return NoSolution()
    if len(solutions) >= 2:
        return Multiple(subset_a=solutions[0], subset_b=solutions[1])
    return Unique(subset=solutions[0])
