"""Сжатие касаний у границы range — предвестник пробоя."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar


@dataclass(frozen=True)
class TouchCompression:
    active: bool
    touches: int
    span_bars: int
    level: float
    label_ru: str


def detect_touch_compression(
    bars: Sequence[KlineBar],
    *,
    level: float | None,
    tolerance_pct: float = 0.35,
    lookback: int = 36,
) -> TouchCompression | None:
    if not bars or level is None or level <= 0:
        return None
    window = list(bars[-min(lookback, len(bars)) :])
    touches = 0
    first_touch = None
    for i, b in enumerate(window):
        near = abs(b.high - level) / level * 100.0 <= tolerance_pct
        near |= abs(b.low - level) / level * 100.0 <= tolerance_pct
        if near:
            touches += 1
            if first_touch is None:
                first_touch = i
    if touches < 3 or first_touch is None:
        return None
    span = len(window) - first_touch
    active = touches >= 4 and span >= 8
    return TouchCompression(
        active=active,
        touches=touches,
        span_bars=span,
        level=float(level),
        label_ru=(
            f"сжатие касаний ({touches}×) у {level:.6g} за {span} св."
            + (" · ждать пробой+ретest" if active else "")
        ),
    )
