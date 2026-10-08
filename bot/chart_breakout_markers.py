"""Пробой / ретест на свечах — как на учебных карточках (кружок + подпись)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .human_trade_brief import preferred_trade_side
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

CHART_BG = "#0d1117"
COL_BREAK = "#ff7b72"
COL_RETEST = "#ffd33d"
COL_FALSE = "#ffa657"


@dataclass(frozen=True)
class BreakoutRetestEvent:
    bar_idx: int
    price: float
    kind: Literal["breakout", "retest", "false_break"]
    label: str


def _idx_to_x(bars: list[KlineBar], idx: int) -> float:
    idx = max(0, min(idx, len(bars) - 1))
    return mdates.date2num(datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc))


def _find_break_down(bars: list[KlineBar], level: float, *, lookback: int = 52) -> tuple[int | None, int | None]:
    if level <= 0 or len(bars) < 6:
        return None, None
    n = len(bars)
    start = max(1, n - lookback)
    break_i: int | None = None
    for i in range(n - 1, start - 1, -1):
        prev_c = float(bars[i - 1].close)
        c = float(bars[i].close)
        if prev_c >= level * 0.999 and c < level * 0.9995:
            break_i = i
            break
    if break_i is None:
        return None, None
    retest_i: int | None = None
    for j in range(break_i + 1, n):
        b = bars[j]
        if float(b.low) <= level * 1.004 and float(b.high) >= level * 0.996:
            retest_i = j
            break
    return break_i, retest_i


def _find_break_up(bars: list[KlineBar], level: float, *, lookback: int = 52) -> tuple[int | None, int | None]:
    if level <= 0 or len(bars) < 6:
        return None, None
    n = len(bars)
    start = max(1, n - lookback)
    break_i: int | None = None
    for i in range(n - 1, start - 1, -1):
        prev_c = float(bars[i - 1].close)
        c = float(bars[i].close)
        if prev_c <= level * 1.001 and c > level * 1.0005:
            break_i = i
            break
    if break_i is None:
        return None, None
    retest_i: int | None = None
    for j in range(break_i + 1, n):
        b = bars[j]
        if float(b.high) >= level * 0.996 and float(b.low) <= level * 1.004:
            retest_i = j
            break
    return break_i, retest_i


def _false_break_from_smc(bars: list[KlineBar], ta: TAAnalysisResult) -> BreakoutRetestEvent | None:
    smc = getattr(ta, "smc", None)
    if smc is None or not getattr(smc, "liquidity_sweep", False):
        return None
    marker = next(
        (m for m in reversed(getattr(smc, "markers", []) or []) if getattr(m, "kind", "") == "sweep"),
        None,
    )
    if marker is None or not (0 <= marker.index < len(bars)):
        return None
    return BreakoutRetestEvent(
        bar_idx=int(marker.index),
        price=float(marker.price),
        kind="false_break",
        label="снятие ликв.",
    )


def _pattern_break_event(bars: list[KlineBar], ta: TAAnalysisResult) -> BreakoutRetestEvent | None:
    pat = getattr(ta, "primary_chart_pattern", None)
    if pat is None or getattr(pat, "status", "") != "confirmed":
        return None
    pts = list(getattr(pat, "points", None) or [])
    if not pts:
        return None
    last = pts[-1]
    idx = int(getattr(last, "index", len(bars) - 1))
    idx = max(0, min(idx, len(bars) - 1))
    kind = str(getattr(pat, "kind", "") or "")
    if kind == "false_breakout":
        return BreakoutRetestEvent(idx, float(last.price), "false_break", "ложный пробой")
    neck = getattr(pat, "neckline", None)
    if neck is not None:
        p = float(getattr(neck, "end_price", 0) or last.price)
        return BreakoutRetestEvent(idx, p, "breakout", "пробой")
    return None


def collect_breakout_retest_events(
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    max_events: int = 2,
) -> list[BreakoutRetestEvent]:
    if not bars:
        return []
    events: list[BreakoutRetestEvent] = []
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    side = preferred_trade_side(ta) or "wait"
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)

    rbr = get_rbr_from_ta(ta)
    if rbr:
        floor = float(rbr.get("range_bottom") or 0)
        if floor > 0:
            bi, ri = _find_break_down(bars, floor)
            if bi is not None:
                events.append(BreakoutRetestEvent(bi, floor, "breakout", f"пробой ≤ {fmt_price(floor)}"))
            if ri is not None and len(events) < max_events:
                events.append(BreakoutRetestEvent(ri, floor, "retest", "ретест"))

    if len(events) < max_events and side in {"short", "wait"} and brdn > 0:
        bi, ri = _find_break_down(bars, brdn)
        if bi is not None and not any(e.bar_idx == bi for e in events):
            events.append(BreakoutRetestEvent(bi, brdn, "breakout", f"пробой ↓ {fmt_price(brdn)}"))
        if ri is not None and len(events) < max_events:
            events.append(BreakoutRetestEvent(ri, brdn, "retest", "ретест"))

    if len(events) < max_events and side in {"long", "wait"} and brk > 0:
        bi, ri = _find_break_up(bars, brk)
        if bi is not None:
            events.append(BreakoutRetestEvent(bi, brk, "breakout", f"пробой ↑ {fmt_price(brk)}"))
        if ri is not None and len(events) < max_events:
            events.append(BreakoutRetestEvent(ri, brk, "retest", "ретест"))

    if len(events) < max_events:
        fb = _false_break_from_smc(bars, ta)
        if fb is not None:
            events.append(fb)
    if len(events) < max_events:
        pe = _pattern_break_event(bars, ta)
        if pe is not None and not any(e.bar_idx == pe.bar_idx for e in events):
            events.append(pe)

    # ближе к текущей цене — приоритет
    events.sort(key=lambda e: (-e.bar_idx, 0 if e.kind == "breakout" else 1))
    out: list[BreakoutRetestEvent] = []
    seen_idx: set[int] = set()
    for e in events:
        if e.bar_idx in seen_idx:
            continue
        seen_idx.add(e.bar_idx)
        out.append(e)
        if len(out) >= max_events:
            break
    return out


def draw_breakout_retest_markers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    max_markers: int = 2,
) -> None:
    events = collect_breakout_retest_events(bars, ta, max_events=max_markers)
    if not events:
        return
    y0, y1 = ax.get_ylim()
    span = max(y1 - y0, 1e-9)
    for k, ev in enumerate(events):
        x = _idx_to_x(bars, ev.bar_idx)
        if ev.kind == "breakout":
            color = COL_BREAK
        elif ev.kind == "retest":
            color = COL_RETEST
        else:
            color = COL_FALSE
        ax.scatter(
            [x],
            [ev.price],
            s=140,
            facecolors="none",
            edgecolors=color,
            linewidths=2.0,
            zorder=11,
        )
        dy = span * (0.035 if k % 2 == 0 else -0.04)
        ax.annotate(
            f"  {ev.label}  ",
            xy=(x, ev.price),
            xytext=(x, ev.price + dy),
            color=color,
            fontsize=7.0,
            fontweight="bold",
            ha="center",
            va="bottom" if dy > 0 else "top",
            zorder=12,
            arrowprops=dict(arrowstyle="-|>", color=color, lw=1.0, shrinkA=2, shrinkB=2),
            bbox=dict(
                boxstyle="round,pad=0.22",
                facecolor=CHART_BG,
                edgecolor=color,
                alpha=0.92,
                linewidth=0.6,
            ),
        )
