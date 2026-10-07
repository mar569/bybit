"""Горизонтальные опорные уровни на графике: день, неделя, ключевые S/R — как на ручном разборе."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .ta_analysis import TAAnalysisResult, fmt_price


@dataclass(frozen=True)
class ReferenceLevel:
    price: float
    label: str
    kind: str  # daily_high, daily_low, prev_daily_high, ...
    priority: int = 50


def _utc_day_key(open_time: float) -> str:
    return datetime.fromtimestamp(open_time, tz=timezone.utc).strftime("%Y-%m-%d")


def session_reference_levels(bars: list[KlineBar]) -> list[ReferenceLevel]:
    """High/low текущего UTC-дня и предыдущего (если есть история)."""
    if not bars:
        return []
    by_day: dict[str, list[KlineBar]] = {}
    for b in bars:
        by_day.setdefault(_utc_day_key(b.open_time), []).append(b)
    days = sorted(by_day.keys())
    out: list[ReferenceLevel] = []

    def _day_levels(day: str, *, prefix: str, prio_high: int, prio_low: int) -> None:
        seg = by_day.get(day)
        if not seg:
            return
        hi = max(b.high for b in seg)
        lo = min(b.low for b in seg)
        out.append(ReferenceLevel(hi, f"{prefix} макс.", "daily_high", prio_high))
        out.append(ReferenceLevel(lo, f"{prefix} мин.", "daily_low", prio_low))

    if days:
        _day_levels(days[-1], prefix="День", prio_high=90, prio_low=90)
    if len(days) >= 2:
        seg = by_day[days[-2]]
        hi = max(b.high for b in seg)
        lo = min(b.low for b in seg)
        out.append(ReferenceLevel(hi, "Вчера макс.", "prev_daily_high", 55))
        out.append(ReferenceLevel(lo, "Вчера мин.", "prev_daily_low", 55))

    if len(days) >= 3:
        week_seg = [b for d in days[-7:] for b in by_day[d]]
        if week_seg:
            out.append(
                ReferenceLevel(
                    max(b.high for b in week_seg),
                    "Неделя макс.",
                    "weekly_high",
                    45,
                )
            )
            out.append(
                ReferenceLevel(
                    min(b.low for b in week_seg),
                    "Неделя мин.",
                    "weekly_low",
                    45,
                )
            )
    return out


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _merge_levels(
    refs: list[ReferenceLevel],
    ta: TAAnalysisResult,
    *,
    current: float,
    max_dist_pct: float = 0.18,
) -> list[ReferenceLevel]:
    """Добавить liquidity / key_levels без дубликатов по цене."""
    if current <= 0:
        return refs
    tol = current * 0.0008
    out = list(refs)

    def _add(price: float, label: str, kind: str, priority: int) -> None:
        if price <= 0 or abs(price - current) / current > max_dist_pct:
            return
        if any(abs(price - r.price) <= tol for r in out):
            return
        out.append(ReferenceLevel(price, label, kind, priority))

    smc = getattr(ta, "smc", None)
    if smc is not None:
        for lv in getattr(smc, "liquidity_levels", None) or []:
            if lv.kind in {"daily_high", "daily_low"}:
                continue  # уже рисуем из UTC-сессии
            _add(float(lv.price), str(lv.label or lv.kind), str(lv.kind), 40)

    for kl in getattr(ta, "key_levels", None) or []:
        _add(float(kl.price), str(kl.label), str(kl.role), 70)

    if ta.nearest_support:
        _add(float(ta.nearest_support), "Поддержка", "nearest_support", 75)
    if ta.nearest_resistance:
        _add(float(ta.nearest_resistance), "Сопротивление", "nearest_resistance", 75)

    out.sort(key=lambda r: (-r.priority, r.price))
    return out[:12]


def draw_reference_horizontals(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    max_lines: int = 8,
) -> None:
    if not bars:
        return
    current = float(getattr(ta, "current_price", 0) or bars[-1].close)
    refs = _merge_levels(session_reference_levels(bars), ta, current=current)
    if not refs:
        return

    x0 = mdates.date2num(_idx_to_date(bars, max(0, len(bars) - 80)))
    x1 = mdates.date2num(_idx_to_date(bars, len(bars) - 1))

    style_by_kind: dict[str, tuple[str, str, float]] = {
        "daily_high": ("#58a6ff", "-", 1.15),
        "daily_low": ("#3fb950", "-", 1.15),
        "weekly_high": ("#79c0ff", ":", 0.95),
        "weekly_low": ("#56d364", ":", 0.95),
        "prev_daily_high": ("#484f58", ":", 0.75),
        "prev_daily_low": ("#484f58", ":", 0.75),
        "nearest_support": ("#3fb950", "-.", 1.0),
        "nearest_resistance": ("#f85149", "-.", 1.0),
    }

    drawn = 0
    for ref in refs:
        if drawn >= max_lines:
            break
        kind = ref.kind
        if kind.startswith("prev") or "Вчера" in ref.label:
            color, ls, lw = style_by_kind.get("prev_daily_high", ("#6e7681", ":", 0.7))
        else:
            color, ls, lw = style_by_kind.get(kind, ("#8b949e", ":", 0.85))

        alpha = 0.92 if ref.priority >= 85 else 0.72 if ref.priority >= 60 else 0.55
        ax.hlines(
            ref.price,
            x0,
            x1,
            colors=color,
            linestyles=ls,
            linewidth=lw,
            alpha=alpha,
            zorder=2,
        )
        ax.text(
            x1,
            ref.price,
            f" {ref.label} {fmt_price(ref.price)} ",
            color=color,
            fontsize=6.8 if ref.priority >= 85 else 6.2,
            fontweight="bold" if ref.priority >= 85 else "normal",
            va="bottom" if ref.price >= current else "top",
            ha="right",
            zorder=3,
            bbox=dict(
                boxstyle="round,pad=0.12",
                facecolor="#0d1117",
                edgecolor=color,
                alpha=0.82,
                linewidth=0.35,
            ),
        )
        drawn += 1
