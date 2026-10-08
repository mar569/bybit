"""Story frame: цепочка сценария русскими подписями на графике (до 5 пунктов)."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_analysis_text import structure_break_label_ru
from .human_trade_brief import preferred_trade_side
from .ta_analysis import TAAnalysisResult


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_left(bars: list[KlineBar], *, frac: float = 0.04) -> float:
    i0 = max(0, len(bars) - min(len(bars), 120))
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    return x0 + (x1 - x0) * frac


def collect_story_labels(ta: TAAnalysisResult, bars: list[KlineBar]) -> list[tuple[str, float]]:
    """(текст RU, y-цена для якоря подписи)."""
    if not bars:
        return []
    current = float(getattr(ta, "current_price", 0) or bars[-1].close)
    side = preferred_trade_side(ta) or "long"
    out: list[tuple[str, float]] = []

    seg = bars[-min(96, len(bars)) :]
    loc_hi = max(b.high for b in seg)
    loc_lo = min(b.low for b in seg)
    structure = str(getattr(ta, "structure", "") or "").lower()
    phase = str(getattr(ta, "phase", "") or "").lower()

    if "down" in structure or phase in {"impulse_down", "correction_up"}:
        if current <= loc_lo * 1.015:
            out.append(("новый минимум", loc_lo))
        elif current >= loc_hi * 0.985 and loc_hi > current:
            out.append(("откат", current))
    elif "up" in structure or phase in {"impulse_up", "correction_down"}:
        if current >= loc_hi * 0.985:
            out.append(("новый максимум", loc_hi))
        elif current <= loc_lo * 1.015:
            out.append(("откат", current))

    wave = getattr(ta, "wave", None)
    wp = str(getattr(wave, "wave_phase", "") or "") if wave else ""
    if wp in {"shallow_pullback", "deep_pullback", "fib_golden_zone", "correction_down", "correction_up"}:
        if not any("откат" in t for t, _ in out):
            out.append(("откат", current))

    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        p = float(smc.structure_break_level)
        lbl = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"))
        out.append((f"импульс сломан · {lbl}", p))

    pat = getattr(ta, "primary_chart_pattern", None)
    if pat is not None and getattr(pat, "kind", "") == "false_breakout":
        py = float(pat.points[-1].price) if getattr(pat, "points", None) else current
        out.append(("ложный пробой", py))

    from .trade_decision_gate import detect_location

    loc = detect_location(ta, side)
    if loc == "retest":
        ref = getattr(ta, "breakout_level", None) if side == "long" else getattr(ta, "breakdown_level", None)
        if ref:
            out.append(("ретест уровня", float(ref)))
        else:
            out.append(("ретест", current))

    brk = getattr(ta, "breakout_level", None)
    brdn = getattr(ta, "breakdown_level", None)
    if side == "long" and brk and brdn and float(brk) > float(brdn):
        if current >= float(brdn) * 0.998 and current <= float(brk) * 1.01:
            out.append(("сопр. → поддержка", float(brdn)))
    elif side == "short" and brk and brdn and float(brdn) < float(brk):
        if current <= float(brk) * 1.002 and current >= float(brdn) * 0.99:
            out.append(("поддерж. → сопр.", float(brk)))

    # дедуп по тексту
    seen: set[str] = set()
    deduped: list[tuple[str, float]] = []
    for text, price in out:
        key = text.split("·")[0].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((text, price))
    return deduped[:5]


def draw_story_frame(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    labels = collect_story_labels(ta, bars)
    if not labels:
        return
    x = _x_left(bars, frac=0.03)
    for i, (text, price) in enumerate(labels):
        y_off = price * (1.0 + 0.0015 * (i % 3))
        ax.annotate(
            f"  {text}  ",
            xy=(x, price),
            xytext=(x, y_off),
            color="#e6edf3",
            fontsize=7.2,
            fontweight="bold",
            ha="left",
            va="bottom",
            zorder=9,
            bbox=dict(
                boxstyle="round,pad=0.2",
                facecolor="#161b22ee",
                edgecolor="#58a6ff",
                alpha=0.92,
                linewidth=0.6,
            ),
            arrowprops=dict(arrowstyle="->", color="#58a6ff", lw=0.85),
        )
