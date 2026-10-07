"""Лёгкий POC по объёму свечей (без полного VPVR)."""
from __future__ import annotations

from typing import Sequence

from .bybit_klines import KlineBar


def volume_poc_price(
    bars: Sequence[KlineBar],
    *,
    lookback: int = 48,
    bins: int = 24,
) -> tuple[float | None, str]:
    if len(bars) < 12:
        return None, ""
    window = list(bars[-min(lookback, len(bars)) :])
    lo = min(b.low for b in window)
    hi = max(b.high for b in window)
    if hi <= lo:
        return None, ""
    step = (hi - lo) / max(bins, 8)
    if step <= 0:
        return None, ""
    acc = [0.0] * bins
    for b in window:
        mid = (b.high + b.low + b.close) / 3.0
        idx = int((mid - lo) / step)
        idx = max(0, min(bins - 1, idx))
        acc[idx] += max(0.0, float(b.volume))
    best_i = max(range(bins), key=lambda i: acc[i])
    if acc[best_i] <= 0:
        return None, ""
    poc = lo + (best_i + 0.5) * step
    cur = window[-1].close
    dist = abs(cur - poc) / cur * 100.0 if cur else 0.0
    if dist <= 0.35:
        label = f"POC ≈ {poc:.6g} — цена у fair value"
    elif cur > poc:
        label = f"POC ≈ {poc:.6g} — выше fair value (+{dist:.1f}%)"
    else:
        label = f"POC ≈ {poc:.6g} — ниже fair value (−{dist:.1f}%)"
    return poc, label
