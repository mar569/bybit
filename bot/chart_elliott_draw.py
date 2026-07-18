"""Отрисовка волн Эллиотта (1–5 + ABC) на matplotlib-графике."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import matplotlib.dates as mdates

from .bybit_klines import KlineBar
from .elliott_wave import ElliottPoint, ElliottWaveResult

if TYPE_CHECKING:
    import matplotlib.axes as maxes

EW_STYLE = {
    "impulse": "#58a6ff",
    "correction": "#ffa657",
    "entry": "#7ee787",
    "stop": "#ff7b72",
    "label_bg": "#0d1117",
}


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_at(bars: list[KlineBar], idx: int) -> float:
    return mdates.date2num(_idx_to_date(bars, idx))


def draw_elliott_waves(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    ew: ElliottWaveResult | None,
    *,
    max_points: int = 12,
) -> None:
    """Линии 0-1-2-3-4-5 + A-B-C и метки; зона входа при ready."""
    if ew is None or not ew.draw_points or not bars:
        return

    pts = [p for p in ew.draw_points if 0 <= p.index < len(bars)][:max_points]
    if len(pts) < 2:
        return

    # разделить импульс и коррекцию
    impulse_pts = [p for p in pts if p.label in {"0", "1", "2", "3", "4", "5"}]
    abc_pts = [p for p in pts if p.label in {"A", "B", "C"}]

    def _polyline(seq: list[ElliottPoint], color: str, lw: float = 1.15) -> None:
        if len(seq) < 2:
            return
        xs = [_x_at(bars, p.index) for p in seq]
        ys = [p.price for p in seq]
        ax.plot(xs, ys, color=color, linewidth=lw, alpha=0.85, linestyle="-")
        for p in seq:
            x = _x_at(bars, p.index)
            is_abc = p.label in {"A", "B", "C"}
            c = EW_STYLE["correction"] if is_abc else color
            ax.plot(x, p.price, marker="o", color=c, markersize=5.5, alpha=0.95)
            # смещение подписи вверх/вниз
            y_off = p.price * (1.0025 if p.label in {"1", "3", "5", "B"} else 0.9975)
            ax.text(
                x,
                y_off,
                f" {p.label}",
                color=c,
                fontsize=7.2,
                fontweight="bold",
                va="center",
                ha="left",
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor=EW_STYLE["label_bg"],
                    edgecolor=c,
                    alpha=0.75,
                    linewidth=0.6,
                ),
            )

    _polyline(impulse_pts, EW_STYLE["impulse"], lw=1.2)
    if abc_pts:
        # соединить конец импульса с A
        bridge: list[ElliottPoint] = []
        if impulse_pts:
            bridge.append(impulse_pts[-1])
        bridge.extend(abc_pts)
        _polyline(bridge if len(bridge) >= 2 else abc_pts, EW_STYLE["correction"], lw=1.05)

    plan = ew.entry_plan
    if plan and plan.entry_price and plan.mode in {"conservative", "aggressive"}:
        ax.axhline(
            plan.entry_price,
            color=EW_STYLE["entry"],
            linestyle="--",
            linewidth=0.9,
            alpha=0.75,
        )
        x1 = _x_at(bars, len(bars) - 1)
        mode_ru = "конс." if plan.mode == "conservative" else "агр."
        ax.text(
            x1,
            plan.entry_price,
            f" EW {mode_ru} вход ",
            color=EW_STYLE["entry"],
            fontsize=6.5,
            va="bottom",
            ha="left",
            alpha=0.95,
            fontweight="bold",
        )
        if plan.stop_price:
            ax.axhline(
                plan.stop_price,
                color=EW_STYLE["stop"],
                linestyle=":",
                linewidth=0.7,
                alpha=0.65,
            )
