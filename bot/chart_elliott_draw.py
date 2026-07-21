"""Отрисовка волн Эллиотта (1–5 + ABC/ABCDE) на matplotlib-графике."""
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
    "htf_impulse": "#79c0ff",
    "htf_correction": "#d2a8ff",
    "entry": "#7ee787",
    "stop": "#ff7b72",
    "label_bg": "#0d1117",
    "forecast": "#a5d6ff",
}

_ABC_LABELS = {"A", "B", "C", "D", "E"}
_IMPULSE_LABELS = {"0", "1", "2", "3", "4", "5"}


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
    style: str = "ltf",
) -> None:
    """Линии 0-1-2-3-4-5 + A-B-C(-D-E) и метки; зона входа при ready.

    style: ltf (яркий) | htf (пунктир старшего ТФ).
    """
    if ew is None or not ew.draw_points or not bars:
        return

    pts = [p for p in ew.draw_points if 0 <= p.index < len(bars)][:max_points]
    if len(pts) < 2:
        return

    is_htf = style == "htf"
    impulse_color = EW_STYLE["htf_impulse"] if is_htf else EW_STYLE["impulse"]
    corr_color = EW_STYLE["htf_correction"] if is_htf else EW_STYLE["correction"]
    lw = 0.95 if is_htf else 1.2
    ls = "--" if is_htf else "-"
    alpha = 0.55 if is_htf else 0.85
    fs = 6.0 if is_htf else 7.2

    impulse_pts = [p for p in pts if p.label in _IMPULSE_LABELS]
    abc_pts = [p for p in pts if p.label in _ABC_LABELS]

    def _polyline(seq: list[ElliottPoint], color: str, line_w: float = lw) -> None:
        if len(seq) < 2:
            return
        xs = [_x_at(bars, p.index) for p in seq]
        ys = [p.price for p in seq]
        ax.plot(xs, ys, color=color, linewidth=line_w, alpha=alpha, linestyle=ls)
        for p in seq:
            x = _x_at(bars, p.index)
            is_abc = p.label in _ABC_LABELS
            c = corr_color if is_abc else color
            ax.plot(x, p.price, marker="o", color=c, markersize=4.5 if is_htf else 5.5, alpha=0.95)
            prefix = "H" if is_htf and p.label in _IMPULSE_LABELS else ""
            label = f"{prefix}{p.label}" if prefix else p.label
            y_off = p.price * (1.0025 if p.label in {"1", "3", "5", "B", "D"} else 0.9975)
            ax.text(
                x,
                y_off,
                f" {label}",
                color=c,
                fontsize=fs,
                fontweight="bold",
                va="center",
                ha="left",
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor=EW_STYLE["label_bg"],
                    edgecolor=c,
                    alpha=0.7,
                    linewidth=0.6,
                ),
            )

    _polyline(impulse_pts, impulse_color, line_w=lw)
    if abc_pts:
        bridge: list[ElliottPoint] = []
        if impulse_pts:
            bridge.append(impulse_pts[-1])
        bridge.extend(abc_pts)
        _polyline(bridge if len(bridge) >= 2 else abc_pts, corr_color, line_w=lw * 0.9)

    if is_htf:
        return

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


def draw_setup_forecast_path(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    prices: list[float],
    labels: list[str] | None = None,
) -> None:
    """Прогнозный путь entry→path→tp (как стрелка на «золотом» примере)."""
    if not bars or len(prices) < 2:
        return
    labels = labels or []
    # invalidation рисуем отдельно пунктиром вниз/вверх — не в основной стрелке
    path_prices: list[float] = []
    path_labels: list[str] = []
    inv: float | None = None
    for i, p in enumerate(prices):
        lab = labels[i] if i < len(labels) else ""
        if lab == "invalidation":
            inv = float(p)
            continue
        path_prices.append(float(p))
        path_labels.append(lab)

    if len(path_prices) < 2:
        return

    start_x = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    # шаг ≈ 4–5 баров вперёд
    if len(bars) >= 2:
        step = mdates.date2num(_idx_to_date(bars, -1)) - mdates.date2num(
            _idx_to_date(bars, max(0, len(bars) - 6))
        )
        step = max(step / 5.0, 1e-6)
    else:
        step = 5.0 / (24 * 60)

    xs = [start_x + step * i for i in range(len(path_prices))]
    color = EW_STYLE["forecast"]
    ax.plot(xs, path_prices, color=color, linestyle="--", linewidth=1.35, alpha=0.9, zorder=5)
    ax.annotate(
        "",
        xy=(xs[-1], path_prices[-1]),
        xytext=(xs[-2], path_prices[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.35, linestyle="dashed", alpha=0.9),
    )
    for x, y, lab in zip(xs, path_prices, path_labels):
        if not lab or lab == "path":
            continue
        ax.text(
            x,
            y,
            f" {lab}",
            color=color,
            fontsize=6.5,
            fontweight="bold",
            va="bottom" if path_prices[-1] >= path_prices[0] else "top",
        )
    if inv is not None:
        ax.axhline(inv, color=EW_STYLE["stop"], linestyle=":", linewidth=0.7, alpha=0.55)
        ax.text(
            xs[0],
            inv,
            " inv ",
            color=EW_STYLE["stop"],
            fontsize=6,
            va="top",
            alpha=0.8,
        )
