"""Complexity fitting: (input size, duration) samples -> estimated O(n^k).

Least squares on log(duration) = k*log(n) + c. Per distinct n the median
duration is used, which resists one-off pauses (GC, page faults). Needs sizes
spanning at least a decade, otherwise the exponent is noise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

MIN_SIZES = 4
MIN_SPAN = 10.0
MIN_MAX_DUR = 0.001  # below ~1 ms the timer overhead dominates


@dataclass(frozen=True)
class Fit:
    arg: str
    exponent: float
    r2: float
    n_min: float
    n_max: float
    samples: int
    label: str


def label(k: float) -> str:
    if k < 0.2:
        return "O(1)"
    if k < 0.8:
        return "sub-linear"
    if k < 1.3:
        return "O(n)"
    if k < 1.7:
        return "O(n log n) to O(n^1.5)"
    if k < 2.5:
        return "O(n^2)"
    if k < 3.5:
        return "O(n^3)"
    return f"O(n^{k:.1f})"


def fit_power(points: list[tuple[float, float]], arg: str = "n") -> Fit | None:
    by_n: dict[float, list[float]] = {}
    for n, d in points:
        if n > 0 and d > 0:
            by_n.setdefault(n, []).append(d)
    if len(by_n) < MIN_SIZES or max(by_n) / min(by_n) < MIN_SPAN:
        return None
    xs = [math.log(n) for n in sorted(by_n)]
    ys = [math.log(median(by_n[n])) for n in sorted(by_n)]
    if max(median(v) for v in by_n.values()) < MIN_MAX_DUR:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    k = sxy / sxx
    c = my - k * mx
    ss_res = sum((y - (k * x + c)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot else 1.0
    return Fit(arg, round(k, 2), round(r2, 3), min(by_n), max(by_n), len(points), label(k))


def best_fit(calls) -> Fit | None:
    """Try each recorded argument; keep the one that explains duration best."""
    args = {a for c in calls for a in c.shapes}
    fits = [f for a in args if (f := fit_power([(c.shapes[a], c.dur) for c in calls if a in c.shapes], a))]
    return max(fits, key=lambda f: f.r2) if fits else None
