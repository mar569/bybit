"""Отрисовка волн Эллиотта (1–5 + ABC/ABCDE) на matplotlib-графике.

Включает PPT-структуры: растяжения 1/3/5, усечение 5, диагонали, тип ABC.
"""
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
    "extension": "#e3b341",
    "truncation": "#f85149",
    "diagonal": "#a371f7",
    "corr_type": "#ffa657",
}

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
    """Границы клина 0-2-4 и 1-3-5 + подпись leading/ending."""
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
            linestyle="-.",
            linewidth=0.95 if is_htf else 1.15,
            alpha=0.7,
            zorder=3,
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
    names = {"zigzag": "зигзаг", "flat": "плоская", "triangle": "треугольник"}
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
    """Красные границы треугольника A–C и B–D (как на слайде PPT)."""
    color = "#ff7b72"
    la = getattr(tri, "lower_a", None)
    lc = getattr(tri, "lower_c", None)
    ub = getattr(tri, "upper_b", None)
    ud = getattr(tri, "upper_d", None)
    for a, b in ((la, lc), (ub, ud)):
        if a is None or b is None:
            continue
        if not (0 <= a.index < len(bars) and 0 <= b.index < len(bars)):
            continue
        ax.plot(
            [_x_at(bars, a.index), _x_at(bars, b.index)],
            [a.price, b.price],
            color=color,
            linewidth=1.35 if not is_htf else 1.0,
            alpha=0.85,
            zorder=4,
        )
        # продлить чуть вперёд к E
        last = getattr(tri, "points", None)
        if last and len(last) >= 5:
            e = last[4]
            if 0 <= e.index < len(bars) and b.index != a.index:
                # экстраполяция линии до индекса E
                t = (e.index - a.index) / max(1, b.index - a.index)
                y = a.price + (b.price - a.price) * t
                ax.plot(
                    [_x_at(bars, b.index), _x_at(bars, e.index)],
                    [b.price, y],
                    color=color,
                    linewidth=1.0,
                    alpha=0.55,
                    linestyle="--",
                    zorder=3,
                )
    kind = getattr(tri, "kind", "") or ""
    title = "сходящ. △" if kind == "contracting" else ("расход. △" if kind == "expanding" else "△ ABCDE")
    pts = getattr(tri, "points", None) or []
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
    for i, (price, lab) in enumerate(zip(prices[:3], (labels + [""] * 3)[:3])):
        ax.axhline(price, color=color, linestyle="--", linewidth=0.75, alpha=0.55 - i * 0.08)
        ax.text(
            x1,
            price,
            f" {lab or f'Fib5 #{i+1}'} ",
            color=color,
            fontsize=5.8,
            va="bottom",
            ha="left",
            alpha=0.9,
            fontweight="bold",
        )


def draw_elliott_waves(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    ew: ElliottWaveResult | None,
    *,
    max_points: int = 24,
    style: str = "ltf",
) -> None:
    """Линии 0-1-2-3-4-5 + A-B-C(-D-E)/WXY; глобальный + локальный слой.

    style: ltf | htf | global | local
    """
    if ew is None or not bars:
        return

    is_htf = style == "htf"
    # Явные слои, если переданы
    g_pts = list(getattr(ew, "global_draw_points", None) or [])
    l_pts = list(getattr(ew, "local_draw_points", None) or [])
    if style == "global" and g_pts:
        pts = [p for p in g_pts if 0 <= p.index < len(bars)][:max_points]
        _draw_ew_layer(ax, bars, ew, pts, layer="global", is_htf=False)
        return
    if style == "local" and l_pts:
        pts = [p for p in l_pts if 0 <= p.index < len(bars)][:max_points]
        _draw_ew_layer(ax, bars, ew, pts, layer="local", is_htf=False)
        return

    # Авто: если есть оба слоя — рисуем оба
    if g_pts or l_pts:
        if g_pts:
            pts_g = [p for p in g_pts if 0 <= p.index < len(bars)][:max_points]
            _draw_ew_layer(ax, bars, ew, pts_g, layer="global", is_htf=is_htf)
        if l_pts and not is_htf:
            pts_l = [p for p in l_pts if 0 <= p.index < len(bars)][:max_points]
            _draw_ew_layer(ax, bars, ew, pts_l, layer="local", is_htf=False)
        if not is_htf:
            _draw_ew_overlays(ax, bars, ew)
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
        _draw_ew_layer(ax, bars, ew, global_pts, layer="global" if not is_htf else "htf", is_htf=is_htf)
    if local_pts and not is_htf:
        _draw_ew_layer(ax, bars, ew, local_pts, layer="local", is_htf=False)
    if not global_pts and not local_pts:
        _draw_ew_layer(ax, bars, ew, pts, layer="ltf", is_htf=is_htf)
    if not is_htf:
        _draw_ew_overlays(ax, bars, ew)


def _draw_ew_overlays(ax: "maxes.Axes", bars: list[KlineBar], ew: ElliottWaveResult) -> None:
    tri = getattr(ew, "triangle_obj", None)
    if tri and getattr(tri, "valid", False):
        _draw_triangle_boundaries(ax, bars, tri, is_htf=False)
    fib_p = list(getattr(ew, "fib_target_prices", None) or [])
    fib_l = list(getattr(ew, "fib_target_labels", None) or [])
    if fib_p:
        _draw_fib_targets(ax, bars, fib_p, fib_l)
    path_p = list(getattr(ew, "path_prices", None) or [])
    path_l = list(getattr(ew, "path_labels", None) or [])
    if len(path_p) >= 2 and getattr(ew, "path_bias", "") in {"long", "short"}:
        draw_setup_forecast_path(ax, bars, path_p, path_l)
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


def _draw_ew_layer(
    ax: "maxes.Axes",
    bars: list[KlineBar],
    ew: ElliottWaveResult,
    pts: list[ElliottPoint],
    *,
    layer: str,
    is_htf: bool,
) -> None:
    if len(pts) < 2:
        return

    if layer == "global":
        impulse_color = "#388bfd"
        corr_color = "#d29922"
        lw, ls, alpha, fs = 1.55, "-", 0.75, 7.0
    elif layer == "local":
        impulse_color = "#7ee787"
        corr_color = "#ffa657"
        lw, ls, alpha, fs = 1.05, "--", 0.9, 6.4
    elif is_htf or layer == "htf":
        impulse_color = EW_STYLE["htf_impulse"]
        corr_color = EW_STYLE["htf_correction"]
        lw, ls, alpha, fs = 0.95, "--", 0.55, 6.0
    else:
        impulse_color = EW_STYLE["impulse"]
        corr_color = EW_STYLE["correction"]
        lw, ls, alpha, fs = 1.2, "-", 0.85, 7.2

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

    if layer == "global" and not is_htf:
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
            ax.plot(
                x,
                p.price,
                marker="o",
                color=c,
                markersize=4.2 if layer == "local" else (4.5 if is_htf else 5.5),
                alpha=0.95,
                zorder=5,
            )
            prefix = ""
            if is_htf and p.label in {"0", "1", "2", "3", "4", "5"}:
                prefix = "H"
            elif layer == "global" and p.label in {"0", "1", "2", "3", "4", "5", "A", "B", "C", "D", "E"}:
                prefix = ""  # чистые 1–5 / A–E
            label = f"{prefix}{p.label}" if prefix else p.label
            y_off = p.price * (
                1.0025
                if p.label in {"1", "3", "5", "B", "D", "X", "X2", "i", "iii", "v", "b", "d", "x"}
                else 0.9975
            )
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
                    boxstyle="round,pad=0.12",
                    facecolor=EW_STYLE["label_bg"],
                    edgecolor=c,
                    alpha=0.7,
                    linewidth=0.55,
                ),
                zorder=6,
            )

    _polyline(impulse_pts, impulse_color, line_w=lw)
    if abc_pts:
        bridge: list[ElliottPoint] = []
        if impulse_pts:
            bridge.append(impulse_pts[-1])
        bridge.extend(abc_pts)
        _polyline(bridge if len(bridge) >= 2 else abc_pts, corr_color, line_w=lw * 0.9)
        if layer != "local":
            _draw_corr_type_badge(ax, bars, abc_pts, corr_type, is_htf=is_htf)
    if complex_pts and not abc_pts:
        _polyline(complex_pts, corr_color, line_w=lw * 0.9)

    if layer == "global" and not is_htf:
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
    if layer == "local" and impulse_pts:
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
                linewidth=0.5,
            ),
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
