"""Раскладка подписей на PNG: без наложений, без потери уровней."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .ta_analysis import fmt_price

CHART_BG = "#0d1117"


@dataclass
class PriceLabel:
    price: float
    text: str
    color: str
    side: str = "right"  # left | right
    priority: int = 50
    kind: str = "level"
    draw_line: bool = True


@dataclass
class LabelBoard:
    """Собирает уровни, склеивает близкие цены, разносит Y."""

    items: list[PriceLabel] = field(default_factory=list)
    reserved: list[float] = field(default_factory=list)

    def reserve(self, price: float | None) -> None:
        if price and float(price) > 0:
            self.reserved.append(float(price))

    def occupied(self, price: float, *, ref: float, tol: float = 0.0035) -> bool:
        if price <= 0 or ref <= 0:
            return False
        band = max(ref * tol, 1e-9)
        return any(abs(price - p) <= band for p in self.reserved)

    def add(
        self,
        price: float | None,
        text: str,
        color: str,
        *,
        side: str = "right",
        priority: int = 50,
        kind: str = "level",
        draw_line: bool = True,
        ref: float = 0.0,
        skip_if_reserved: bool = True,
    ) -> None:
        if not price or float(price) <= 0 or not text:
            return
        p = float(price)
        if skip_if_reserved and self.occupied(p, ref=ref or p):
            return
        self.items.append(
            PriceLabel(
                price=p,
                text=text.strip(),
                color=color,
                side=side,
                priority=priority,
                kind=kind,
                draw_line=draw_line,
            )
        )
        self.reserved.append(p)

    def compact(self, *, current: float, max_labels: int = 8) -> list[PriceLabel]:
        """Склеить почти одинаковые цены, оставить сильные уровни."""
        if not self.items:
            return []
        ref = current if current > 0 else max(abs(i.price) for i in self.items)
        tol = ref * 0.0032
        ranked = sorted(self.items, key=lambda i: (-i.priority, i.price))
        kept: list[PriceLabel] = []
        for item in ranked:
            twin = next((k for k in kept if abs(k.price - item.price) <= tol), None)
            if twin is None:
                kept.append(item)
                continue
            if item.priority >= twin.priority - 5 and item.text.lower() not in twin.text.lower():
                combo = f"{twin.text} · {item.text}"
                if len(combo) <= 42:
                    twin.text = combo
            if item.priority > twin.priority:
                twin.priority = item.priority
                twin.color = item.color
                twin.draw_line = twin.draw_line or item.draw_line
        kept.sort(key=lambda i: (-i.priority, i.price))
        return kept[:max_labels]


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _deconflict_ys(
    prices: list[float],
    *,
    y_min: float,
    y_max: float,
    min_frac: float = 0.034,
) -> list[float]:
    if not prices:
        return []
    span = max(y_max - y_min, abs(prices[0]) * 0.02, 1e-9)
    gap = span * min_frac
    order = sorted(range(len(prices)), key=lambda i: prices[i])
    ys = list(prices)
    for k in range(1, len(order)):
        prev, cur = order[k - 1], order[k]
        if ys[cur] - ys[prev] < gap:
            ys[cur] = ys[prev] + gap
    if ys[order[-1]] > y_max:
        shift = ys[order[-1]] - y_max
        for i in order:
            ys[i] -= shift
    for k in range(1, len(order)):
        prev, cur = order[k - 1], order[k]
        if ys[cur] - ys[prev] < gap:
            ys[cur] = ys[prev] + gap
    if ys[order[0]] < y_min:
        shift = y_min - ys[order[0]]
        for i in order:
            ys[i] += shift
    return ys


def draw_label_board(
    ax: plt.Axes,
    bars: list[KlineBar],
    board: LabelBoard,
    *,
    current: float,
) -> None:
    if not bars or not board.items:
        return
    labels = board.compact(current=current)
    if not labels:
        return
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    span = max(x1 - x0, 0.001)
    x_last = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    # Правые подписи — сразу за свечами, левее блока TP/SL.
    x_right = min(x_last + span * 0.012, x1 - span * 0.16)
    x_left = x0 + span * 0.012
    x_line_end = min(x_last, x_right - span * 0.004)

    left = [i for i in labels if i.side == "left"]
    right = [i for i in labels if i.side != "left"]
    for group, x_text, ha in ((left, x_left, "left"), (right, x_right, "left")):
        if not group:
            continue
        ys = _deconflict_ys(
            [i.price for i in group],
            y_min=y0 + (y1 - y0) * 0.04,
            y_max=y1 - (y1 - y0) * 0.08,
        )
        for item, y_text in zip(group, ys):
            if item.draw_line:
                ax.hlines(
                    item.price,
                    x0,
                    x_line_end,
                    colors=item.color,
                    linestyles="-" if item.priority >= 80 else ":",
                    linewidth=1.15 if item.priority >= 80 else 0.85,
                    alpha=0.78 if item.priority >= 80 else 0.55,
                    zorder=2,
                )
            if abs(y_text - item.price) > (y1 - y0) * 0.012:
                ax.plot(
                    [x_line_end, x_text],
                    [item.price, y_text],
                    color=item.color,
                    linewidth=0.7,
                    alpha=0.55,
                    zorder=6,
                )
            ax.text(
                x_text,
                y_text,
                f" {item.text} ",
                color=item.color,
                fontsize=7.1 if item.priority >= 80 else 6.5,
                fontweight="bold" if item.priority >= 75 else "normal",
                va="center",
                ha=ha,
                zorder=8,
                bbox=dict(
                    boxstyle="round,pad=0.16",
                    facecolor=CHART_BG,
                    edgecolor=item.color,
                    alpha=0.9,
                    linewidth=0.55,
                ),
            )


def format_level_text(label: str, price: float) -> str:
    if not label:
        return fmt_price(price)
    if any(ch.isdigit() for ch in label) and fmt_price(price) in label:
        return label
    return f"{label} {fmt_price(price)}"
