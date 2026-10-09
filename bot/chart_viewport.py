"""Y/X viewport для PNG: свечи не обрезать по цене «вокруг сейчас»."""
from __future__ import annotations

from datetime import datetime, timezone

import matplotlib.dates as mdates

from .bybit_klines import KlineBar


def bars_in_xlim(bars: list[KlineBar], ax) -> list[KlineBar]:
    if not bars:
        return []
    x0, x1 = ax.get_xlim()
    if x1 <= x0:
        return bars
    out: list[KlineBar] = []
    for b in bars:
        x = mdates.date2num(datetime.fromtimestamp(b.open_time, tz=timezone.utc))
        if x0 - 1e-9 <= x <= x1 + 1e-9:
            out.append(b)
    return out or bars


def apply_ylim_to_visible_bars(
    ax,
    bars: list[KlineBar],
    *,
    pad_ratio: float = 0.10,
    extra_prices: list[float] | None = None,
) -> None:
    vis = bars_in_xlim(bars, ax)
    if not vis:
        return
    lo = min(float(b.low) for b in vis)
    hi = max(float(b.high) for b in vis)
    for p in extra_prices or []:
        if p and p > 0:
            lo = min(lo, float(p))
            hi = max(hi, float(p))
    if hi <= lo:
        return
    pad = max((hi - lo) * pad_ratio, hi * 0.0015)
    ax.set_ylim(max(0.0, lo - pad), hi + pad)
