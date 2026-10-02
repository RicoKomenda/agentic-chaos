"""Small statistics helpers for judging non-deterministic systems."""

from __future__ import annotations

import math
from statistics import NormalDist

__all__ = [
    "runs_needed",
    "wilson_interval",
]


def wilson_interval(passes: int, runs: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a pass rate. Well-behaved for small ``runs`` and rates near 0 or 1."""
    if runs == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    rate = passes / runs
    denominator = 1 + z * z / runs
    centre = (rate + z * z / (2 * runs)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / runs + z * z / (4 * runs * runs)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def runs_needed(threshold: float, confidence: float = 0.95) -> int:
    """Smallest number of runs whose all-pass Wilson lower bound reaches ``threshold`` (< 1)."""
    if not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1 (exclusive)")
    runs = 1
    while wilson_interval(runs, runs, confidence)[0] < threshold:
        runs += 1
    return runs
