"""Компрессия отката: тот же % движения за больше свечей = слабость."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar


@dataclass(frozen=True)
class PullbackCompression:
    impulse_bars: int
    pullback_bars: int
    impulse_pct: float
    pullback_pct: float
    ratio: float
    weak_pullback: bool
    label_ru: str


def measure_pullback_compression(
    bars: Sequence[KlineBar],
    *,
    lookback: int = 48,
) -> PullbackCompression | None:
    if len(bars) < 20:
        return None
    window = list(bars[-min(lookback, len(bars)) :])
    n = len(window)
    # последний импульс: серия однонаправленных close
    i = n - 1
    pull_dir = 0
    pull_start = i
    while i > 0:
        d = window[i].close - window[i - 1].close
        if pull_dir == 0:
            pull_dir = 1 if d >= 0 else -1
            pull_start = i
        elif (d >= 0 and pull_dir < 0) or (d <= 0 and pull_dir > 0):
            break
        i -= 1
    pull_end = pull_start
    pull_start = max(0, i + 1)
    if pull_end - pull_start < 2:
        return None
    p0 = window[pull_start].open
    p1 = window[pull_end].close
    if p0 <= 0:
        return None
    pull_pct = abs(p1 - p0) / p0 * 100.0
    if pull_pct < 0.15:
        return None

    imp_end = pull_start
    imp_start = max(0, imp_end - 24)
    imp_dir = -pull_dir
    best_i, best_j = imp_start, imp_end
    best_pct = 0.0
    for j in range(imp_end, imp_start, -1):
        for i2 in range(max(imp_start, j - 20), j):
            o = window[i2].open
            c = window[j - 1].close
            if o <= 0:
                continue
            pct = (c - o) / o * 100.0
            if imp_dir > 0 and pct > best_pct:
                best_pct, best_i, best_j = pct, i2, j
            if imp_dir < 0 and -pct > best_pct:
                best_pct, best_i, best_j = -pct, i2, j
    if best_pct < 0.2:
        return None
    imp_bars = max(1, best_j - best_i)
    pull_bars = max(1, pull_end - pull_start + 1)
    ratio = pull_bars / imp_bars
    weak = ratio >= 1.35 and pull_pct >= best_pct * 0.55
    label = (
        f"откат {pull_pct:.2f}% за {pull_bars} св. vs импульс {best_pct:.2f}% за {imp_bars} св."
    )
    if weak:
        label += " · откат слабее (компрессия времени)"
    return PullbackCompression(
        impulse_bars=imp_bars,
        pullback_bars=pull_bars,
        impulse_pct=best_pct,
        pullback_pct=pull_pct,
        ratio=ratio,
        weak_pullback=weak,
        label_ru=label,
    )
