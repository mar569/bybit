"""Отрисовка волн Эллиотта (1–5 + ABC/ABCDE) на matplotlib-графике.

TradeRevolution (частые визуалы из lovepdf):
- Сетка коррекции: Fib 23.6/38.2/50/61.8/78.6 от импульса 0→5
- Расширение импульса: FE 61.8/78.6/100/127.2/161.8 от точек 0-1-2
- Диагональ: сходящиеся границы 0-2-4 / 1-3-5
- Треугольник ABCDE: границы + заливка
- Импульс синий / коррекция оранжевая
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import matplotlib.dates as mdates
from matplotlib.patches import Polygon

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
    "extension": "#e3b341",
    "truncation": "#f85149",
    "diagonal": "#a371f7",
    "corr_type": "#ffa657",
    "fib_grid": "#79c0ff",
    "fib_gold": "#ff7b72",  # 38.2 / 61.8 как в TradeRev
    "fib_ext": "#e3b341",
}

# TradeRevolution — сетка коррекции (от импульса)
FIB_CORR_LEVELS = (0.0, 0.236, 0.382, 0.50, 0.618, 0.786, 1.0)
FIB_CORR_GOLD = {0.382, 0.618}  # чаще всего: плоская / глубокая

# TradeRevolution — расширение импульса (цели W3 / C)
FIB_IMP_EXT_LEVELS = (0.618, 0.786, 1.0, 1.272, 1.618)
FIB_IMP_EXT_GOLD = {1.0, 1.618}  # 1:1 чаще всего; 161.8 для сильной 3

_ABC_LABELS = {"A", "B", "C", "D", "E", "a", "b", "c", "d", "e"}
_IMPULSE_LABELS = {"0", "1", "2", "3", "4", "5", "·0", "i", "ii", "iii", "iv", "v"}
_COMPLEX_LABELS = {"W", "X", "Y", "X2", "Z", "w", "x", "y", "x2", "z"}
_LOCAL_IMPULSE = {"·0", "i", "ii", "iii", "iv", "v"}
_LOCAL_ABC = {"a", "b", "c", "d", "e"}
_LOCAL_COMPLEX = {"w", "x", "y", "x2", "z"}

# Какая пара точек образует импульсную волну N
_EXT_SEGMENTS = {
    "1": ("0", "1"),
    "3": ("2", "3"),
    "5": ("4", "5"),
}


def _idx_to_date(bars: list[KlineBar], idx: int) -> datetime:
    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def _x_at(bars: list[KlineBar], idx: int) -> float:
    return mdates.date2num(_idx_to_date(bars, idx))


def _by_label(pts: list[ElliottPoint]) -> dict[str, ElliottPoint]:
    return {p.label: p for p in pts}


def _draw_extension_highlight(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    by: dict[str, ElliottPoint],
    extension: str,
    *,
    is_htf: bool,
) -> None:
    """Жёлтая толстая линия по растянутой волне + подпись."""
    seg = _EXT_SEGMENTS.get(extension)
    if not seg:
        return
    a, b = by.get(seg[0]), by.get(seg[1])
    if a is None or b is None:
        return
    if not (0 <= a.index < len(bars) and 0 <= b.index < len(bars)):
        return
    color = EW_STYLE["extension"]
    xs = [_x_at(bars, a.index), _x_at(bars, b.index)]
    ys = [a.price, b.price]
    ax.plot(
        xs,
        ys,
        color=color,
        linewidth=3.2 if not is_htf else 2.2,
        alpha=0.55,
        solid_capstyle="round",
        zorder=4,
    )
    mid_x = (xs[0] + xs[1]) / 2
    mid_y = (ys[0] + ys[1]) / 2
    ax.text(
        mid_x,
        mid_y,
        f" растяжение {extension} ",
        color=color,
        fontsize=6.0 if is_htf else 6.8,
        fontweight="bold",
        va="bottom",
        ha="center",
        bbox=dict(
            boxstyle="round,pad=0.15",
            facecolor=EW_STYLE["label_bg"],
            edgecolor=color,
            alpha=0.75,
            linewidth=0.6,
        ),
        zorder=6,
    )


def _draw_truncation_marker(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    by: dict[str, ElliottPoint],
    *,
    is_htf: bool,
) -> None:
    p3, p5 = by.get("3"), by.get("5")
    if p3 is None or p5 is None:
        return
    if not (0 <= p5.index < len(bars)):
        return
    color = EW_STYLE["truncation"]
    x = _x_at(bars, p5.index)
    ax.plot(x, p5.price, marker="x", color=color, markersize=9, markeredgewidth=1.6, zorder=6)
    # Пунктир к экстремуму 3 — «не дотянули»
    if 0 <= p3.index < len(bars):
        ax.plot(
            [_x_at(bars, p3.index), x],
            [p3.price, p5.price],
            color=color,
            linestyle=":",
            linewidth=0.9,
            alpha=0.7,
            zorder=4,
        )
    ax.text(
        x,
        p5.price,
        " усечение 5 ",
        color=color,
        fontsize=6.0 if is_htf else 6.8,
        fontweight="bold",
        va="top",
        ha="left",
        bbox=dict(
            boxstyle="round,pad=0.12",
            facecolor=EW_STYLE["label_bg"],
            edgecolor=color,
            alpha=0.75,
            linewidth=0.6,
        ),
        zorder=6,
    )


def _draw_diagonal_guides(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    by: dict[str, ElliottPoint],
    diagonal: str,
    *,
    is_htf: bool,
) -> None:
    """Границы клина 0-2-4 и 1-3-5 (TradeRev конечная/начальная диагональ)."""
    color = EW_STYLE["diagonal"]
    lower_labs = ["0", "2", "4"]
    upper_labs = ["1", "3", "5"]
    for labs in (lower_labs, upper_labs):
        seq = [by[l] for l in labs if l in by and 0 <= by[l].index < len(bars)]
        if len(seq) < 2:
            continue
        ax.plot(
            [_x_at(bars, p.index) for p in seq],
            [p.price for p in seq],
            color=color,
            linestyle=(0, (4.0, 2.5)),
            linewidth=1.25 if is_htf else 1.45,
            alpha=0.85,
            zorder=3,
        )
    # Лёгкая заливка клина
    if all(k in by for k in ("0", "1", "2", "3")):
        poly: list[tuple[float, float]] = []
        for lab in ("0", "1", "3", "2"):
            p = by[lab]
            if 0 <= p.index < len(bars):
                poly.append((_x_at(bars, p.index), p.price))
        if "4" in by and 0 <= by["4"].index < len(bars):
            poly.append((_x_at(bars, by["4"].index), by["4"].price))
        if "5" in by and 0 <= by["5"].index < len(bars):
            poly.append((_x_at(bars, by["5"].index), by["5"].price))
        if len(poly) >= 3:
            ax.add_patch(
                Polygon(
                    poly,
                    closed=True,
                    facecolor=color,
                    edgecolor="none",
                    alpha=0.08,
                    zorder=2,
                )
            )
    title = "конечная диагональ" if diagonal == "ending" else "начальная диагональ"
    anchor = by.get("5") or by.get("4") or by.get("3")
    if anchor is None or not (0 <= anchor.index < len(bars)):
        return
    ax.text(
        _x_at(bars, anchor.index),
        anchor.price,
        f" {title} ",
        color=color,
        fontsize=6.0 if is_htf else 6.8,
        fontweight="bold",
        va="bottom",
        ha="left",
        bbox=dict(
            boxstyle="round,pad=0.12",
            facecolor=EW_STYLE["label_bg"],
            edgecolor=color,
            alpha=0.75,
            linewidth=0.6,
        ),
        zorder=6,
    )


def _draw_corr_type_badge(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    abc_pts: list[ElliottPoint],
    corr_type: str,
    *,
    is_htf: bool,
) -> None:
    if not corr_type or corr_type == "unknown" or not abc_pts:
        return
    p = abc_pts[-1]
    if not (0 <= p.index < len(bars)):
        return
    names = {
        "zigzag": "зигзаг",
        "flat": "флет",
        "expanded_flat": "расш.флет",
        "running_flat": "бегущий флет",
        "triangle": "треугольник",
    }
    name = names.get(corr_type, corr_type)
    color = EW_STYLE["corr_type"]
    ax.text(
        _x_at(bars, p.index),
        p.price,
        f" ABC {name} ",
        color=color,
        fontsize=5.8 if is_htf else 6.5,
        fontweight="bold",
        va="top",
        ha="right",
        alpha=0.95,
        bbox=dict(
            boxstyle="round,pad=0.12",
            facecolor=EW_STYLE["label_bg"],
            edgecolor=color,
            alpha=0.7,
            linewidth=0.5,
        ),
        zorder=6,
    )


def _draw_triangle_boundaries(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    tri: object,
    *,
    is_htf: bool,
) -> None:
    """Границы треугольника A–C / B–D + заливка (как на TradeRev слайде)."""
    color = "#ff7b72"
    fill = "#ffa657"
    la = getattr(tri, "lower_a", None)
    lc = getattr(tri, "lower_c", None)
    ub = getattr(tri, "upper_b", None)
    ud = getattr(tri, "upper_d", None)
    pts = list(getattr(tri, "points", None) or [])

    # Заливка по вершинам A-B-C-D-E
    if len(pts) >= 4:
        poly_xy = []
        for p in pts[:5]:
            if 0 <= p.index < len(bars):
                poly_xy.append((_x_at(bars, p.index), p.price))
        if len(poly_xy) >= 3:
            ax.add_patch(
                Polygon(
                    poly_xy,
                    closed=True,
                    facecolor=fill,
                    edgecolor="none",
                    alpha=0.12,
                    zorder=2,
                )
            )

    for a, b in ((la, lc), (ub, ud)):
        if a is None or b is None:
            continue
        if not (0 <= a.index < len(bars) and 0 <= b.index < len(bars)):
            continue
        ax.plot(
            [_x_at(bars, a.index), _x_at(bars, b.index)],
            [a.price, b.price],
            color=color,
            linewidth=1.45 if not is_htf else 1.05,
            alpha=0.9,
            zorder=4,
        )
        if pts and len(pts) >= 5:
            e = pts[4]
            if 0 <= e.index < len(bars) and b.index != a.index:
                t = (e.index - a.index) / max(1, b.index - a.index)
                y = a.price + (b.price - a.price) * t
                ax.plot(
                    [_x_at(bars, b.index), _x_at(bars, e.index)],
                    [b.price, y],
                    color=color,
                    linewidth=1.05,
                    alpha=0.6,
                    linestyle="--",
                    zorder=3,
                )
    kind = getattr(tri, "kind", "") or ""
    titles = {
        "contracting": "симм. △",
        "expanding": "расход. △",
        "ascending": "восход. △",
    }
    title = titles.get(kind, "△ ABCDE")
    if pts:
        p = pts[0]
        if 0 <= p.index < len(bars):
            ax.text(
                _x_at(bars, p.index),
                p.price,
                f" {title} ",
                color=color,
                fontsize=6.2,
                fontweight="bold",
                va="bottom",
                bbox=dict(
                    boxstyle="round,pad=0.12",
                    facecolor=EW_STYLE["label_bg"],
                    edgecolor=color,
                    alpha=0.75,
                    linewidth=0.5,
                ),
                zorder=6,
            )


def _impulse_anchor_points(ew: ElliottWaveResult) -> dict[str, ElliottPoint]:
    """Точки 0..5 из impulse или global/draw_points."""
    by: dict[str, ElliottPoint] = {}
    if ew.impulse is not None:
        by.update({p.label: p for p in ew.impulse.points if p.label in {"0", "1", "2", "3", "4", "5"}})
    if len(by) < 3:
        for src in (
            getattr(ew, "global_draw_points", None) or [],
            getattr(ew, "draw_points", None) or [],
        ):
            for p in src:
                if p.label in {"0", "1", "2", "3", "4", "5"} and p.label not in by:
                    by[p.label] = p
            if len(by) >= 3:
                break
    return by


def _draw_correction_grid(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    by: dict[str, ElliottPoint],
    *,
    abc_pts: list[ElliottPoint] | None = None,
    gold_only: bool = False,
) -> None:
    """Сетка коррекции TradeRev: Fib от Точка1(0) → Точка2(конец импульса).

    Чаще всего: глубокая на 61.8%, плоская на 38.2%.
    gold_only: только 38.2 / 50 / 61.8 — для читаемого wave-chart.
    """
    p0 = by.get("0")
    p_end = by.get("5") or by.get("3") or by.get("1")
    if p0 is None or p_end is None:
        return
    if not (0 <= p0.index < len(bars) and 0 <= p_end.index < len(bars)):
        return
    span = p_end.price - p0.price
    if abs(span) < 1e-12:
        return

    x0 = _x_at(bars, p_end.index)
    x1 = _x_at(bars, len(bars) - 1)
    # сетка только справа от конца импульса
    if x1 <= x0:
        x1 = x0 + max(abs(x1 - _x_at(bars, max(0, len(bars) - 20))), 1e-6)

    # Точка касания ABC с уровнем — подсветить
    touch_level: float | None = None
    if abc_pts:
        last = abc_pts[-1]
        for lvl in FIB_CORR_GOLD | {0.50}:
            price = p_end.price - span * lvl  # 0% = end, 100% = start
            if abs(last.price - price) / max(abs(p_end.price), 1e-9) <= 0.012:
                touch_level = lvl
                break

    levels = (0.382, 0.50, 0.618) if gold_only else FIB_CORR_LEVELS
    for lvl in levels:
        # TradeRev: 0% у Точки 2 (пик), 100% у Точки 1 (старт)
        price = p_end.price - span * lvl
        is_gold = lvl in FIB_CORR_GOLD or lvl == 0.50
        is_touch = touch_level is not None and abs(lvl - touch_level) < 1e-9
        color = EW_STYLE["fib_gold"] if (is_gold or is_touch) else EW_STYLE["fib_grid"]
        lw = 1.35 if gold_only else (1.15 if is_gold or is_touch else 0.65)
        alpha = 0.9 if gold_only or is_gold or is_touch else 0.40
        ax.hlines(
            price,
            xmin=x0,
            xmax=x1,
            colors=color,
            linestyles="-",
            linewidth=lw,
            alpha=alpha,
            zorder=2,
        )
        pct = f"{lvl * 100:.1f}".rstrip("0").rstrip(".")
        label = f" Fib {pct}%"
        if is_touch:
            label += " ← сюда"
        ax.text(
            x1,
            price,
            label,
            color=color,
            fontsize=7.2 if gold_only else (5.6 if is_gold else 5.2),
            fontweight="bold" if is_gold or is_touch or gold_only else "normal",
            va="center",
            ha="left",
            alpha=0.95 if is_gold or gold_only else 0.7,
            zorder=6,
        )

    if not gold_only:
        # Метки Точка 1 / Точка 2
        ax.text(
            _x_at(bars, p0.index),
            p0.price,
            " Т1 ",
            color=EW_STYLE["fib_grid"],
            fontsize=5.5,
            fontweight="bold",
            va="top",
            ha="left",
            alpha=0.8,
            zorder=6,
        )
        ax.text(
            _x_at(bars, p_end.index),
            p_end.price,
            " Т2 ",
            color=EW_STYLE["fib_grid"],
            fontsize=5.5,
            fontweight="bold",
            va="bottom",
            ha="left",
            alpha=0.8,
            zorder=6,
        )


def _draw_impulse_extension(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    by: dict[str, ElliottPoint],
) -> None:
    """Расширение импульса TradeRev: точки 0-1-2 → цели FE для волны 3/C.

    Чаще всего работает 100% (1:1), затем 127.2 / 161.8.
    """
    p0, p1, p2 = by.get("0"), by.get("1"), by.get("2")
    if p0 is None or p1 is None or p2 is None:
        return
    if not all(0 <= p.index < len(bars) for p in (p0, p1, p2)):
        return
    # Уже есть полная 5 — сетка коррекции важнее; extension для формирующейся 3
    if "5" in by and "4" in by:
        return
    w1 = p1.price - p0.price
    if abs(w1) < 1e-12:
        return

    x0 = _x_at(bars, p2.index)
    x1 = _x_at(bars, len(bars) - 1)
    if x1 <= x0:
        x1 = x0 + max(abs(x1 - _x_at(bars, max(0, len(bars) - 20))), 1e-6)

    p3 = by.get("3")
    touch_fe: float | None = None
    for fe in FIB_IMP_EXT_GOLD:
        price = p2.price + w1 * fe
        if p3 is not None and abs(p3.price - price) / max(abs(p3.price), 1e-9) <= 0.012:
            touch_fe = fe
            break

    for fe in FIB_IMP_EXT_LEVELS:
        price = p2.price + w1 * fe
        is_gold = fe in FIB_IMP_EXT_GOLD
        is_touch = touch_fe is not None and abs(fe - touch_fe) < 1e-9
        color = EW_STYLE["fib_gold"] if (is_gold or is_touch) else EW_STYLE["fib_ext"]
        ax.hlines(
            price,
            xmin=x0,
            xmax=x1,
            colors=color,
            linestyles="--",
            linewidth=1.1 if is_gold or is_touch else 0.7,
            alpha=0.8 if is_gold else 0.45,
            zorder=2,
        )
        pct = f"{fe * 100:.1f}".rstrip("0").rstrip(".")
        label = f" FE {pct}%"
        if is_touch:
            label += " ← W3"
        ax.text(
            x1,
            price,
            label,
            color=color,
            fontsize=5.5 if is_gold else 5.1,
            fontweight="bold" if is_gold or is_touch else "normal",
            va="center",
            ha="left",
            alpha=0.95 if is_gold else 0.7,
            zorder=6,
        )


def _draw_abc_channel(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    abc_pts: list[ElliottPoint],
    impulse_end: ElliottPoint | None,
) -> None:
    """Канал зигзага: линия start–B параллельна A–C (TradeRev рекомендация)."""
    by = {p.label: p for p in abc_pts}
    if not all(k in by for k in ("A", "B", "C")):
        return
    a, b, c = by["A"], by["B"], by["C"]
    if not all(0 <= p.index < len(bars) for p in (a, b, c)):
        return
    start = impulse_end
    if start is None or not (0 <= start.index < len(bars)):
        start = a
    color = EW_STYLE["correction"]
    # A–C
    ax.plot(
        [_x_at(bars, a.index), _x_at(bars, c.index)],
        [a.price, c.price],
        color=color,
        linestyle=":",
        linewidth=0.9,
        alpha=0.55,
        zorder=3,
    )
    # start–B
    ax.plot(
        [_x_at(bars, start.index), _x_at(bars, b.index)],
        [start.price, b.price],
        color=color,
        linestyle=":",
        linewidth=0.9,
        alpha=0.55,
        zorder=3,
    )


def _draw_fib_targets(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    prices: list[float],
    labels: list[str],
) -> None:
    if not prices or not bars:
        return
    x1 = _x_at(bars, len(bars) - 1)
    color = "#e3b341"
    # Разнести близкие Fib-цели по Y, чтобы tp1/tp2/tp3 не слипались
    y_lim = ax.get_ylim()
    span = max(y_lim[1] - y_lim[0], abs(prices[0]) * 0.02, 1e-9)
    min_gap = span * 0.012
    order = sorted(range(min(3, len(prices))), key=lambda i: prices[i])
    display_y = {i: prices[i] for i in order}
    for k in range(1, len(order)):
        i_prev, i_cur = order[k - 1], order[k]
        if display_y[i_cur] - display_y[i_prev] < min_gap:
            display_y[i_cur] = display_y[i_prev] + min_gap
    for i, (price, lab) in enumerate(zip(prices[:3], (labels + [""] * 3)[:3])):
        ax.axhline(price, color=color, linestyle="--", linewidth=0.75, alpha=0.55 - i * 0.08)
        ax.text(
            x1,
            display_y.get(i, price),
            f" {lab or f'Fib5 #{i+1}'} ",
            color=color,
            fontsize=5.8,
            va="center",
            ha="left",
            alpha=0.95,
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.12",
                facecolor="#0d1117",
                edgecolor=color,
                alpha=0.72,
                linewidth=0.45,
            ),
            zorder=7,
        )


def draw_elliott_waves(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    ew: ElliottWaveResult | None,
    *,
    max_points: int = 24,
    style: str = "ltf",
    emphasis: bool = False,
) -> None:
    """Линии 0-1-2-3-4-5 + A-B-C(-D-E)/WXY; глобальный + локальный слой.

    style: ltf | htf | global | local
    emphasis: крупные метки для wave-chat (читаемый график).
    """
    if ew is None or not bars:
        return

    is_htf = style == "htf"
    # Явные слои, если переданы
    g_pts = list(getattr(ew, "global_draw_points", None) or [])
    l_pts = list(getattr(ew, "local_draw_points", None) or [])

    # Fallback: точки импульса из объекта, если draw_points пустые
    if not g_pts and not l_pts and not (ew.draw_points or []):
        if ew.impulse is not None and ew.impulse.points:
            g_pts = list(ew.impulse.points)
        if ew.abc is not None and ew.abc.points:
            # ABC дописываем в global слой для видимости
            g_pts = list(g_pts) + list(ew.abc.points)

    if style == "global" and g_pts:
        pts = [p for p in g_pts if 0 <= p.index < len(bars)][:max_points]
        _draw_ew_layer(ax, bars, ew, pts, layer="global", is_htf=False, emphasis=emphasis)
        return
    if style == "local" and l_pts:
        pts = [p for p in l_pts if 0 <= p.index < len(bars)][:max_points]
        _draw_ew_layer(ax, bars, ew, pts, layer="local", is_htf=False, emphasis=emphasis)
        return

    # Авто: если есть оба слоя — рисуем оба
    if g_pts or l_pts:
        if g_pts:
            pts_g = [p for p in g_pts if 0 <= p.index < len(bars)][:max_points]
            _draw_ew_layer(
                ax, bars, ew, pts_g, layer="global", is_htf=is_htf, emphasis=emphasis,
            )
        if l_pts and not is_htf and not emphasis:
            # На wave-chart локальный слой часто шумит — только global крупно
            pts_l = [p for p in l_pts if 0 <= p.index < len(bars)][:max_points]
            _draw_ew_layer(ax, bars, ew, pts_l, layer="local", is_htf=False, emphasis=False)
        elif l_pts and not is_htf and emphasis and not g_pts:
            pts_l = [p for p in l_pts if 0 <= p.index < len(bars)][:max_points]
            _draw_ew_layer(
                ax, bars, ew, pts_l, layer="local", is_htf=False, emphasis=True,
            )
        if not is_htf:
            _draw_ew_overlays(ax, bars, ew, simple=emphasis)
        return

    # Fallback: единый draw_points
    if not ew.draw_points:
        return
    pts = [p for p in ew.draw_points if 0 <= p.index < len(bars)][:max_points]
    if len(pts) < 2:
        return
    # Разделить по типу меток если смешаны
    local_pts = [
        p for p in pts
        if p.label in _LOCAL_IMPULSE or p.label in _LOCAL_ABC or p.label in _LOCAL_COMPLEX
    ]
    global_pts = [p for p in pts if p not in local_pts]
    if global_pts:
        _draw_ew_layer(
            ax, bars, ew, global_pts,
            layer="global" if not is_htf else "htf",
            is_htf=is_htf,
            emphasis=emphasis,
        )
    if local_pts and not is_htf and not emphasis:
        _draw_ew_layer(ax, bars, ew, local_pts, layer="local", is_htf=False, emphasis=False)
    if not global_pts and not local_pts:
        _draw_ew_layer(ax, bars, ew, pts, layer="ltf", is_htf=is_htf, emphasis=emphasis)
    if not is_htf:
        _draw_ew_overlays(ax, bars, ew, simple=emphasis)


def _draw_ew_overlays(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    ew: ElliottWaveResult,
    *,
    simple: bool = False,
) -> None:
    by = _impulse_anchor_points(ew)
    abc_pts = [
        p for p in (getattr(ew, "global_draw_points", None) or getattr(ew, "draw_points", None) or [])
        if p.label in {"A", "B", "C", "D", "E"}
    ]
    if ew.abc is not None:
        abc_pts = [p for p in ew.abc.points if p.label in {"A", "B", "C", "D", "E"}] or abc_pts

    # Wave-chart: только золотые Fib 38.2/50/61.8 — без сетки на весь экран
    if "0" in by and ("5" in by or "3" in by):
        if simple:
            _draw_correction_grid(ax, bars, by, abc_pts=abc_pts, gold_only=True)
        else:
            _draw_correction_grid(ax, bars, by, abc_pts=abc_pts)

    if not simple and "0" in by and "1" in by and "2" in by:
        _draw_impulse_extension(ax, bars, by)

    if len(abc_pts) >= 3 and not simple:
        _draw_abc_channel(ax, bars, abc_pts, by.get("5") or by.get("3"))

    tri = getattr(ew, "triangle_obj", None)
    if tri and getattr(tri, "valid", False):
        _draw_triangle_boundaries(ax, bars, tri, is_htf=False)
    fib_p = list(getattr(ew, "fib_target_prices", None) or [])
    fib_l = list(getattr(ew, "fib_target_labels", None) or [])
    if fib_p and not simple:
        _draw_fib_targets(ax, bars, fib_p, fib_l)
    path_p = list(getattr(ew, "path_prices", None) or [])
    path_l = list(getattr(ew, "path_labels", None) or [])
    if len(path_p) >= 2 and getattr(ew, "path_bias", "") in {"long", "short"}:
        draw_wave_horizon_path(
            ax,
            bars,
            path_p,
            path_l,
            bias=str(getattr(ew, "path_bias", "") or "neutral"),
            horizon_hours=float(getattr(ew, "path_horizon_hours", 0) or 2.0),
            reason=str(getattr(ew, "path_reason_ru", "") or ""),
            invalidation=getattr(ew, "path_invalidation", None),
        )
    plan = ew.entry_plan
    if plan and plan.entry_price and plan.mode in {"conservative", "aggressive"}:
        ax.axhline(
            plan.entry_price,
            color=EW_STYLE["entry"],
            linestyle="--",
            linewidth=1.35 if simple else 0.9,
            alpha=0.85,
        )
        x1 = _x_at(bars, len(bars) - 1)
        mode_ru = "конс." if plan.mode == "conservative" else "агр."
        ax.text(
            x1,
            plan.entry_price,
            f" ВХОД {mode_ru} ",
            color=EW_STYLE["entry"],
            fontsize=9.0 if simple else 6.5,
            va="bottom",
            ha="left",
            alpha=0.98,
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.2",
                facecolor=EW_STYLE["label_bg"],
                edgecolor=EW_STYLE["entry"],
                alpha=0.85,
                linewidth=0.7,
            ),
        )
        if plan.stop_price:
            ax.axhline(
                plan.stop_price,
                color=EW_STYLE["stop"],
                linestyle=":",
                linewidth=1.1 if simple else 0.7,
                alpha=0.8,
            )
            ax.text(
                x1,
                plan.stop_price,
                " СТОП ",
                color=EW_STYLE["stop"],
                fontsize=8.5 if simple else 6.0,
                va="top",
                ha="left",
                fontweight="bold",
            )


def _draw_ew_layer(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    ew: ElliottWaveResult,
    pts: list[ElliottPoint],
    *,
    layer: str,
    is_htf: bool,
    emphasis: bool = False,
) -> None:
    if len(pts) < 2:
        return

    if emphasis and layer == "global":
        impulse_color = "#58a6ff"
        corr_color = "#ffa657"
        lw, ls, alpha, fs = 2.4, "-", 0.95, 11.5
        marker_size = 11.0
    elif layer == "global":
        impulse_color = "#388bfd"
        corr_color = "#d29922"
        lw, ls, alpha, fs = 1.55, "-", 0.75, 7.0
        marker_size = 5.5
    elif layer == "local":
        impulse_color = "#7ee787"
        corr_color = "#ffa657"
        lw, ls, alpha, fs = (1.8, "-", 0.95, 10.5) if emphasis else (1.05, "--", 0.9, 6.4)
        marker_size = 9.0 if emphasis else 4.2
    elif is_htf or layer == "htf":
        impulse_color = EW_STYLE["htf_impulse"]
        corr_color = EW_STYLE["htf_correction"]
        lw, ls, alpha, fs = 0.95, "--", 0.55, 6.0
        marker_size = 4.5
    else:
        impulse_color = EW_STYLE["impulse"]
        corr_color = EW_STYLE["correction"]
        lw, ls, alpha, fs = (2.2, "-", 0.95, 11.0) if emphasis else (1.2, "-", 0.85, 7.2)
        marker_size = 10.0 if emphasis else 5.5

    impulse_pts = [p for p in pts if p.label in _IMPULSE_LABELS]
    abc_pts = [p for p in pts if p.label in _ABC_LABELS]
    complex_pts = [p for p in pts if p.label in _COMPLEX_LABELS]
    by = _by_label([p for p in impulse_pts if p.label in {"0", "1", "2", "3", "4", "5"}])

    extension = getattr(ew, "extension", "") or ""
    truncated = bool(getattr(ew, "truncated", False))
    diagonal = getattr(ew, "diagonal", "") or ""
    corr_type = getattr(ew, "corr_type", "") or ""
    if layer == "global" and ew.impulse is not None:
        extension = extension or ew.impulse.extension
        truncated = truncated or ew.impulse.truncated
        diagonal = diagonal or ew.impulse.diagonal

    if layer == "global" and not is_htf and not emphasis:
        if extension:
            _draw_extension_highlight(ax, bars, by, extension, is_htf=False)
        if diagonal:
            _draw_diagonal_guides(ax, bars, by, diagonal, is_htf=False)
        if truncated:
            _draw_truncation_marker(ax, bars, by, is_htf=False)

    def _polyline(seq: list[ElliottPoint], color: str, line_w: float = lw) -> None:
        if len(seq) < 2:
            return
        xs = [_x_at(bars, p.index) for p in seq]
        ys = [p.price for p in seq]
        ax.plot(xs, ys, color=color, linewidth=line_w, alpha=alpha, linestyle=ls, zorder=5)
        for p in seq:
            x = _x_at(bars, p.index)
            is_abc = p.label in _ABC_LABELS or p.label in _COMPLEX_LABELS
            c = corr_color if is_abc else color
            # Читаемая метка: круг + номер/буква
            pretty = {
                "0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
                "A": "A", "B": "B", "C": "C", "D": "D", "E": "E",
                "i": "i", "ii": "ii", "iii": "iii", "iv": "iv", "v": "v",
                "a": "a", "b": "b", "c": "c",
            }.get(p.label, p.label)
            if is_htf and p.label in {"0", "1", "2", "3", "4", "5"}:
                pretty = f"H{pretty}"
            ax.plot(
                x,
                p.price,
                marker="o",
                color=c,
                markersize=marker_size,
                markeredgecolor="#0d1117",
                markeredgewidth=1.2 if emphasis else 0.6,
                alpha=0.98,
                zorder=8,
            )
            y_off = p.price * (
                1.004 if p.label in {"1", "3", "5", "B", "D", "i", "iii", "v", "b"} else 0.996
            )
            ax.text(
                x,
                y_off if not emphasis else p.price,
                pretty,
                color="#ffffff" if emphasis else c,
                fontsize=fs,
                fontweight="bold",
                va="center",
                ha="center",
                bbox=dict(
                    boxstyle="circle,pad=0.28" if emphasis else "round,pad=0.12",
                    facecolor=c if emphasis else EW_STYLE["label_bg"],
                    edgecolor="#0d1117" if emphasis else c,
                    alpha=0.95 if emphasis else 0.7,
                    linewidth=1.1 if emphasis else 0.55,
                ),
                zorder=9,
            )

    _polyline(impulse_pts, impulse_color, line_w=lw)
    if abc_pts:
        bridge: list[ElliottPoint] = []
        if impulse_pts:
            bridge.append(impulse_pts[-1])
        bridge.extend(abc_pts)
        _polyline(bridge if len(bridge) >= 2 else abc_pts, corr_color, line_w=lw * 0.9)
        if layer != "local" and not emphasis:
            _draw_corr_type_badge(ax, bars, abc_pts, corr_type, is_htf=is_htf)
    if complex_pts and not abc_pts:
        _polyline(complex_pts, corr_color, line_w=lw * 0.9)

    if layer == "global" and not is_htf and impulse_pts and emphasis:
        # Короткая подпись над первой точкой
        note = "импульс 1–5" if any(p.label == "5" for p in impulse_pts) else "волны"
        if abc_pts:
            note += " + ABC"
        p0 = impulse_pts[0]
        ax.text(
            _x_at(bars, p0.index),
            p0.price,
            f" {note} ",
            color=impulse_color,
            fontsize=8.5,
            fontweight="bold",
            va="bottom",
            ha="left",
            alpha=0.95,
            bbox=dict(
                boxstyle="round,pad=0.2",
                facecolor=EW_STYLE["label_bg"],
                edgecolor=impulse_color,
                alpha=0.8,
                linewidth=0.7,
            ),
            zorder=7,
        )
    elif layer == "global" and not is_htf:
        note = getattr(ew, "global_label_ru", "") or getattr(ew, "structure_note_ru", "") or ""
        if note and impulse_pts:
            p0 = impulse_pts[0]
            ax.text(
                _x_at(bars, p0.index),
                p0.price,
                f" G: {note[:48]} ",
                color=impulse_color,
                fontsize=6.0,
                fontweight="bold",
                va="bottom",
                ha="left",
                alpha=0.9,
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor=EW_STYLE["label_bg"],
                    edgecolor=impulse_color,
                    alpha=0.65,
                    linewidth=0.5,
                ),
                zorder=6,
            )
    if layer == "local" and impulse_pts and not emphasis:
        note = getattr(ew, "local_label_ru", "") or "локально"
        p0 = impulse_pts[0]
        ax.text(
            _x_at(bars, p0.index),
            p0.price,
            f" L: {note[:40]} ",
            color=impulse_color,
            fontsize=5.8,
            fontweight="bold",
            va="top",
            ha="left",
            alpha=0.9,
            bbox=dict(
                boxstyle="round,pad=0.12",
                facecolor=EW_STYLE["label_bg"],
                edgecolor=impulse_color,
                alpha=0.65,
                linewidth=0.45,
            ),
            zorder=6,
        )


def draw_wave_horizon_path(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    prices: list[float],
    labels: list[str] | None = None,
    *,
    bias: str = "neutral",
    horizon_hours: float = 2.0,
    reason: str = "",
    invalidation: float | None = None,
) -> None:
    """Жирный прогнозный зигзаг на 1–3ч — «как скорее всего пойдёт график» по волнам."""
    if not bars or len(prices) < 2:
        return
    labels = list(labels or [])
    from .pro_invariants import sanitize_path_prices

    current = float(bars[-1].close)
    if bias in {"long", "short"}:
        prices, labels = sanitize_path_prices(bias, current, prices, labels)
        if len(prices) < 2:
            return
    # отфильтровать invalidation из цен если вдруг попал
    path_p: list[float] = []
    path_l: list[str] = []
    for i, p in enumerate(prices):
        lab = labels[i] if i < len(labels) else ""
        if lab == "invalidation":
            if invalidation is None:
                invalidation = float(p)
            continue
        path_p.append(float(p))
        path_l.append(lab)
    if len(path_p) < 2:
        return

    hz = max(1.0, min(3.0, float(horizon_hours) or 2.0))
    start_x = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    span_days = hz / 24.0
    n = len(path_p)
    xs = [start_x + span_days * (i / max(1, n - 1)) for i in range(n)]

    color = "#3fb950" if bias == "long" else ("#f85149" if bias == "short" else EW_STYLE["forecast"])
    # тень + основная линия
    ax.plot(xs, path_p, color=color, linestyle="-", linewidth=3.4, alpha=0.22, zorder=5, solid_capstyle="round")
    ax.plot(xs, path_p, color=color, linestyle=(0, (5.5, 2.8)), linewidth=2.0, alpha=0.95, zorder=6, solid_capstyle="round")
    ax.annotate(
        "",
        xy=(xs[-1], path_p[-1]),
        xytext=(xs[-2], path_p[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.8, alpha=0.95),
        zorder=7,
    )
    for x, y in zip(xs, path_p):
        ax.plot(x, y, marker="o", color=color, markersize=5.5, alpha=0.95, zorder=7)

    # лейблы точек пути
    y_lim = ax.get_ylim()
    span_y = max(y_lim[1] - y_lim[0], abs(path_p[0]) * 0.02, 1e-9)
    min_gap = span_y * 0.018
    order = sorted(range(len(path_p)), key=lambda i: path_p[i])
    display_y = {i: path_p[i] for i in order}
    for k in range(1, len(order)):
        i_prev, i_cur = order[k - 1], order[k]
        if display_y[i_cur] - display_y[i_prev] < min_gap:
            display_y[i_cur] = display_y[i_prev] + min_gap

    for i, (x, y, lab) in enumerate(zip(xs, path_p, path_l)):
        if not lab or lab in {"сейчас", "entry", "path"}:
            continue
        ax.text(
            x,
            display_y.get(i, y),
            f" {lab}",
            color=color,
            fontsize=6.2,
            fontweight="bold",
            va="bottom" if path_p[-1] >= path_p[0] else "top",
            bbox=dict(
                boxstyle="round,pad=0.12",
                facecolor="#0d1117",
                edgecolor=color,
                alpha=0.78,
                linewidth=0.5,
            ),
            zorder=8,
        )

    # горизонт + сценарий
    hz_txt = f"{hz:.0f}ч" if hz >= 1.95 else f"{int(hz * 60)}м"
    title = reason[:42] if reason else f"путь ~{hz_txt}"
    if reason and "ч" not in reason and "м" not in reason:
        title = f"{reason[:36]} · ~{hz_txt}"
    ax.text(
        xs[-1],
        path_p[-1],
        f" ▶ {title} ",
        color=color,
        fontsize=6.8,
        fontweight="bold",
        va="bottom" if bias == "long" else "top",
        ha="left",
        bbox=dict(
            boxstyle="round,pad=0.18",
            facecolor="#0d1117",
            edgecolor=color,
            alpha=0.88,
            linewidth=0.7,
        ),
        zorder=9,
    )

    if invalidation is not None and invalidation > 0:
        ax.axhline(invalidation, color=EW_STYLE["stop"], linestyle=":", linewidth=0.75, alpha=0.55, zorder=3)
        ax.text(
            xs[-1],
            invalidation,
            " inv ",
            color=EW_STYLE["stop"],
            fontsize=5.5,
            va="center",
            ha="left",
            alpha=0.8,
            zorder=6,
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
    path_prices: list[float] = []
    path_labels: list[str] = []
    inv: float | None = None
    _lab_map = {
        "entry": "вход",
        "path": "",
        "tp1": "TP1",
        "tp2": "TP2",
        "tp3": "TP3",
        "invalidation": "inv",
        "retest": "ретест",
        "stop": "STOP",
    }
    for i, p in enumerate(prices):
        lab = labels[i] if i < len(labels) else ""
        if lab == "invalidation":
            inv = float(p)
            continue
        path_prices.append(float(p))
        path_labels.append(_lab_map.get(lab, lab if lab not in {"path", ""} else ""))

    if len(path_prices) < 2:
        return

    start_x = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    if len(bars) >= 2:
        step = mdates.date2num(_idx_to_date(bars, -1)) - mdates.date2num(
            _idx_to_date(bars, max(0, len(bars) - 6))
        )
        step = max(step / 5.0, 1e-6)
    else:
        step = 5.0 / (24 * 60)

    xs = [start_x + step * (i + 1.2) for i in range(len(path_prices))]
    color = EW_STYLE["forecast"]
    ax.plot(xs, path_prices, color=color, linestyle="--", linewidth=1.25, alpha=0.85, zorder=5)
    ax.annotate(
        "",
        xy=(xs[-1], path_prices[-1]),
        xytext=(xs[-2], path_prices[-2]),
        arrowprops=dict(arrowstyle="-|>", color=color, lw=1.25, linestyle="dashed", alpha=0.85),
    )
    # Разнести близкие path-лейблы по Y
    y_lim = ax.get_ylim()
    span = max(y_lim[1] - y_lim[0], abs(path_prices[0]) * 0.02, 1e-9)
    min_gap = span * 0.016
    order = sorted(range(len(path_prices)), key=lambda i: path_prices[i])
    display_y = {i: path_prices[i] for i in order}
    for k in range(1, len(order)):
        i_prev, i_cur = order[k - 1], order[k]
        if display_y[i_cur] - display_y[i_prev] < min_gap:
            display_y[i_cur] = display_y[i_prev] + min_gap
    for i, (x, y, lab) in enumerate(zip(xs, path_prices, path_labels)):
        if not lab:
            continue
        ax.text(
            x,
            display_y.get(i, y),
            f" {lab}",
            color=color,
            fontsize=6.0,
            fontweight="bold",
            va="bottom" if path_prices[-1] >= path_prices[0] else "top",
            bbox=dict(
                boxstyle="round,pad=0.1",
                facecolor="#0d1117",
                edgecolor=color,
                alpha=0.7,
                linewidth=0.4,
            ),
        )
    if inv is not None:
        ax.axhline(inv, color=EW_STYLE["stop"], linestyle=":", linewidth=0.7, alpha=0.5)
