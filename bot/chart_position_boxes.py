"""Блоки «позиция» как в TradingView: зелёный тейк / красный стоп от линии входа."""
from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .bybit_klines import KlineBar
from .human_trade_brief import preferred_trade_side
from .pro_invariants import bias_side, ensure_minimum_targets, resolve_wait_plan_levels
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price


def chart_plan_targets(
    ta: TAAnalysisResult,
    *,
    entry_lo: float | None,
    entry_hi: float | None,
) -> list[float]:
    current = float(getattr(ta, "current_price", 0) or 0)
    rbr_side = get_rbr_from_ta(ta)
    side = bias_side(getattr(ta, "verdict", ""), getattr(ta, "action_priority", ""))
    if rbr_side and str(rbr_side.get("direction") or "") in {"long", "short"}:
        side = str(rbr_side["direction"])
    if side not in {"long", "short"}:
        side = preferred_trade_side(ta) or "long"
    entry = None
    if entry_lo is not None and entry_hi is not None and entry_hi > entry_lo:
        entry = (entry_lo, entry_hi)
    raw = list(getattr(ta, "target_prices", None) or []) or list(getattr(ta, "setup_tps", None) or [])
    _, _, trigger = resolve_wait_plan_levels(ta)
    return ensure_minimum_targets(side, current, raw, entry_zone=entry, trigger=trigger)

_BOX_GREEN = "#3fb950"
_BOX_RED = "#f85149"


def forward_box_slot(
    ax: plt.Axes,
    bars: list[KlineBar],
    *,
    use_xlim: bool = True,
) -> tuple[float, float]:
    """Правая колонка под TP/SL — не на свечах и не на правых подписях."""
    x_last = mdates.date2num(_idx_to_date(bars, len(bars) - 1))
    if use_xlim:
        x0, x1 = ax.get_xlim()
        span = max(x1 - x0, 0.001)
        width = span * 0.125
        x_box = x1 - width * 1.06
        if x_box < x_last + span * 0.04:
            x_box = x_last + span * 0.04
        return x_box, width
    i0 = max(0, len(bars) - min(len(bars), 72))
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    span = max(x_last - x0, 0.001)
    return x_last + span * 0.06, span * 0.22


def _glyph_from_label(text: str) -> str:
    low = (text or "").lower()
    if "тейк" in low or low.startswith("tp"):
        return "TP"
    if "стоп" in low or low.startswith("sl"):
        return "SL"
    if "вход" in low or "entry" in low:
        return "IN"
    return ""


def _label_inside_box(
    ax: plt.Axes,
    x_box: float,
    width: float,
    y: float,
    text: str,
    color: str,
) -> None:
    from .chart_display_policy import chart_box_labels_enabled, chart_plan_glyphs_enabled

    if chart_box_labels_enabled():
        ax.text(
            x_box + width * 0.06,
            y,
            text,
            color=color,
            fontsize=7.2,
            fontweight="bold",
            va="center",
            ha="left",
            zorder=10,
            clip_on=True,
        )
        return
    if not chart_plan_glyphs_enabled():
        return
    glyph = _glyph_from_label(text)
    if not glyph:
        return
    ax.text(
        x_box + width * 0.52,
        y,
        glyph,
        color=color,
        fontsize=8.5,
        fontweight="bold",
        va="center",
        ha="center",
        zorder=10,
        clip_on=True,
        bbox=dict(boxstyle="circle,pad=0.28", facecolor="#0d1117cc", edgecolor=color, linewidth=0.8),
    )


def _idx_to_date(bars: list[KlineBar], idx: int):
    from datetime import datetime, timezone

    idx = max(0, min(idx, len(bars) - 1))
    return datetime.fromtimestamp(bars[idx].open_time, tz=timezone.utc)


def plan_for_display(
    ta: TAAnalysisResult,
) -> tuple[str, float, float, float, float, float] | None:
    """side, entry, entry_lo, entry_hi, stop, tp (real)."""
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") == "short":
        el, eh = rbr.get("entry_lo"), rbr.get("entry_hi")
        stop = rbr.get("stop")
        if el and eh and stop:
            entry_lo, entry_hi = float(el), float(eh)
            if entry_hi > entry_lo and float(stop) > 0:
                tps = [float(t) for t in (rbr.get("targets") or []) if t]
                if tps:
                    entry = (entry_lo + entry_hi) / 2.0
                    stop_f = float(stop)
                    tp = float(tps[0])
                    if stop_f > entry and tp < entry:
                        return "short", entry, entry_lo, entry_hi, stop_f, tp
    raw = _entry_stop_tp(ta)
    if raw is None:
        return None
    side, entry, stop, tp = raw
    entry_lo = entry_hi = entry
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    return side, entry, entry_lo, entry_hi, stop, tp


def _entry_stop_tp(ta: TAAnalysisResult) -> tuple[str, float, float, float] | None:
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") == "short":
        el, eh = rbr.get("entry_lo"), rbr.get("entry_hi")
        stop = rbr.get("stop")
        if el and eh and stop:
            entry_lo, entry_hi = float(el), float(eh)
            if entry_hi > entry_lo and float(stop) > 0:
                tps = [float(t) for t in (rbr.get("targets") or []) if t]
                if tps:
                    entry = (entry_lo + entry_hi) / 2.0
                    stop_f = float(stop)
                    tp = float(tps[0])
                    if stop_f > entry and tp < entry:
                        return "short", entry, stop_f, tp

    side = preferred_trade_side(ta)
    if rbr and str(rbr.get("direction") or "") == "short":
        side = "short"
    if side not in {"long", "short"}:
        v = (getattr(ta, "verdict", "") or "").upper()
        if v == "LONG":
            side = "long"
        elif v == "SHORT":
            side = "short"
        else:
            side = "long"

    entry_lo = entry_hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    elif getattr(ta, "setup_entry", None):
        e = float(ta.setup_entry)
        entry_lo, entry_hi = e * 0.9985, e * 1.0015
    if entry_lo is None or entry_hi is None:
        cur = float(getattr(ta, "current_price", 0) or 0)
        if cur <= 0:
            return None
        entry_lo, entry_hi = cur * 0.999, cur * 1.001

    entry = (entry_lo + entry_hi) / 2.0
    stop = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
    if not stop or float(stop) <= 0:
        return None
    stop = float(stop)

    tps = chart_plan_targets(ta, entry_lo=entry_lo, entry_hi=entry_hi)
    if not tps:
        return None
    tp = float(tps[0])

    if side == "long":
        if stop >= entry or tp <= entry:
            return None
    else:
        if stop <= entry or tp >= entry:
            return None
    return side, entry, stop, tp


def _compact_wait_plan(ta: TAAnalysisResult, *, entry: float, tp: float) -> bool:
    verdict = (getattr(ta, "verdict", "") or "").upper()
    rbr = get_rbr_from_ta(ta)
    phase = str(rbr.get("phase") or "") if rbr else ""
    if phase in {"fade_top", "await_break"}:
        return True
    if verdict == "WAIT" and entry > 0 and tp > 0:
        return abs(entry - tp) / entry > 0.09
    return False


def draw_forward_short_projection(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    use_xlim: bool = False,
) -> bool:
    """TV-стиль: блок SL/вход/TP вправо «вперёд» (ожидаемый шорт от зоны)."""
    from .chart_display_policy import chart_trade_plan_on_chart_enabled

    if not chart_trade_plan_on_chart_enabled():
        return False
    rbr = get_rbr_from_ta(ta)
    phase = str(rbr.get("phase") or "") if rbr else ""
    if not rbr or phase not in {"fade_top", "await_break", "retest"}:
        return False
    raw = plan_for_display(ta)
    if raw is None:
        return False
    side, entry, entry_lo, entry_hi, stop, tp = raw
    if side != "short" or stop <= entry or tp >= entry:
        return False
    from .chart_plan_display import build_display_plan

    disp = build_display_plan(
        side=side,
        entry=entry,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        tp=tp,
    )
    stop, tp = disp.stop, disp.tp
    tp_label = disp.tp_label

    x_box, width = forward_box_slot(ax, bars, use_xlim=use_xlim)

    entry_lo = float(rbr.get("entry_lo") or entry_lo)
    entry_hi = float(rbr.get("entry_hi") or entry_hi)
    ax.add_patch(
        Rectangle(
            (x_box, entry_hi), width, max(stop - entry_hi, (entry_hi - entry_lo) * 0.5),
            facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.32, linewidth=1.1, zorder=9,
        )
    )
    ax.add_patch(
        Rectangle(
            (x_box, entry_lo), width, entry_hi - entry_lo,
            facecolor="#e3b341", edgecolor="#e3b341", alpha=0.22, linewidth=1.0, zorder=9,
        )
    )
    ax.add_patch(
        Rectangle(
            (x_box, tp), width, max(entry_lo - tp, entry * 0.003),
            facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.30, linewidth=1.0, zorder=9,
        )
    )
    _label_inside_box(ax, x_box, width, (entry_lo + tp) / 2.0, f"тейк {tp_label}", "#3fb950")
    _label_inside_box(ax, x_box, width, (entry_lo + entry_hi) / 2, "вход", "#e6edf3")
    _label_inside_box(ax, x_box, width, (entry_hi + stop) / 2.0, f"стоп {disp.stop_label}", _BOX_RED)
    return True


def draw_forward_long_projection(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    use_xlim: bool = False,
) -> bool:
    """TV-стиль: блок SL/вход/TP вправо «вперёд» (ожидаемый лонг от зоны)."""
    from .chart_display_policy import chart_trade_plan_on_chart_enabled

    if not chart_trade_plan_on_chart_enabled():
        return False
    plan = _entry_stop_tp(ta)
    if plan is None:
        return False
    side, entry, stop, tp = plan
    if side != "long" or stop >= entry or tp <= entry:
        return False

    entry_lo = entry_hi = entry
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    elif entry_hi <= entry_lo:
        entry_lo, entry_hi = entry * 0.9992, entry * 1.0008

    from .chart_plan_display import build_display_plan

    disp = build_display_plan(
        side=side,
        entry=entry,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        tp=tp,
    )
    stop, tp = disp.stop, disp.tp
    x_box, width = forward_box_slot(ax, bars, use_xlim=use_xlim)

    ax.add_patch(
        Rectangle(
            (x_box, stop), width, max(entry_lo - stop, entry * 0.003),
            facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.32, linewidth=1.1, zorder=9,
        )
    )
    ax.add_patch(
        Rectangle(
            (x_box, entry_lo), width, max(entry_hi - entry_lo, entry * 0.0008),
            facecolor="#e3b341", edgecolor="#e3b341", alpha=0.22, linewidth=1.0, zorder=9,
        )
    )
    ax.add_patch(
        Rectangle(
            (x_box, entry_hi), width, max(tp - entry_hi, entry * 0.003),
            facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.30, linewidth=1.0, zorder=9,
        )
    )
    _label_inside_box(ax, x_box, width, (entry_hi + tp) / 2.0, f"тейк {disp.tp_label}", _BOX_GREEN)
    _label_inside_box(ax, x_box, width, (entry_lo + entry_hi) / 2, "вход", "#e6edf3")
    _label_inside_box(ax, x_box, width, (entry_lo + stop) / 2.0, f"стоп {disp.stop_label}", _BOX_RED)
    return True


def draw_forward_plan_boxes(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    use_xlim: bool = True,
) -> bool:
    """TP/SL/вход справа для любого плана — short или long."""
    from .chart_display_policy import chart_trade_plan_on_chart_enabled
    from .plan_staleness import plan_is_stale

    if not chart_trade_plan_on_chart_enabled() or plan_is_stale(ta):
        return False
    if draw_forward_short_projection(ax, bars, ta, use_xlim=use_xlim):
        return True
    if draw_forward_long_projection(ax, bars, ta, use_xlim=use_xlim):
        return True
    raw = plan_for_display(ta)
    if raw is None:
        return False
    side = raw[0]
    if side == "short":
        # Нет RBR-фазы — всё равно рисуем блок по плану.
        from .chart_plan_display import build_display_plan

        _, entry, entry_lo, entry_hi, stop, tp = raw
        if stop <= entry or tp >= entry:
            return False
        disp = build_display_plan(
            side="short", entry=entry, entry_lo=entry_lo, entry_hi=entry_hi, stop=stop, tp=tp,
        )
        stop, tp = disp.stop, disp.tp
        x_box, width = forward_box_slot(ax, bars, use_xlim=use_xlim)
        ax.add_patch(
            Rectangle(
                (x_box, entry_hi), width, max(stop - entry_hi, (entry_hi - entry_lo) * 0.5),
                facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.32, linewidth=1.1, zorder=9,
            )
        )
        ax.add_patch(
            Rectangle(
                (x_box, entry_lo), width, max(entry_hi - entry_lo, entry * 0.0008),
                facecolor="#e3b341", edgecolor="#e3b341", alpha=0.22, linewidth=1.0, zorder=9,
            )
        )
        ax.add_patch(
            Rectangle(
                (x_box, tp), width, max(entry_lo - tp, entry * 0.003),
                facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.30, linewidth=1.0, zorder=9,
            )
        )
        _label_inside_box(ax, x_box, width, (entry_lo + tp) / 2.0, f"тейк {disp.tp_label}", _BOX_GREEN)
        _label_inside_box(ax, x_box, width, (entry_lo + entry_hi) / 2, "вход", "#e6edf3")
        _label_inside_box(ax, x_box, width, (entry_hi + stop) / 2.0, f"стоп {disp.stop_label}", _BOX_RED)
        return True
    return False


def draw_position_risk_boxes(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    from .chart_display_policy import chart_trade_plan_on_chart_enabled

    if not chart_trade_plan_on_chart_enabled():
        return
    if draw_forward_plan_boxes(ax, bars, ta, use_xlim=True):
        return
    plan = _entry_stop_tp(ta)
    if plan is None or not bars:
        return
    side, entry, stop, tp = plan

    i0 = max(0, len(bars) - min(len(bars), 72))
    i1 = len(bars) - 1
    x0 = mdates.date2num(_idx_to_date(bars, i0))
    x1 = mdates.date2num(_idx_to_date(bars, i1))
    width = max((x1 - x0) * 0.22, 0.0008)
    x_box = x1 - width * 0.95

    compact = _compact_wait_plan(ta, entry=entry, tp=tp)

    if side == "long":
        sl_rect = Rectangle((x_box, stop), width, entry - stop, facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.28, zorder=2)
        tp_lbl_y, sl_lbl_y = (entry + tp) / 2, (entry + stop) / 2
        if compact:
            tp_rect = None
        else:
            tp_rect = Rectangle((x_box, entry), width, tp - entry, facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.22, zorder=2)
    else:
        sl_rect = Rectangle((x_box, entry), width, stop - entry, facecolor=_BOX_RED, edgecolor=_BOX_RED, alpha=0.28, zorder=2)
        tp_lbl_y, sl_lbl_y = (entry + tp) / 2, (entry + stop) / 2
        if compact:
            tp_rect = None
        else:
            tp_rect = Rectangle((x_box, tp), width, entry - tp, facecolor=_BOX_GREEN, edgecolor=_BOX_GREEN, alpha=0.22, zorder=2)

    if tp_rect is not None:
        ax.add_patch(tp_rect)
    ax.add_patch(sl_rect)
    ax.axhline(entry, xmin=0.72, xmax=0.98, color="#e6edf3", linewidth=1.0, linestyle="-", alpha=0.85, zorder=3)

    if compact:
        ax.hlines(tp, xmin=x0, xmax=x1, colors=_BOX_GREEN, linewidth=1.0, linestyle=":", alpha=0.65, zorder=2)
        ax.text(
            x0 + (x1 - x0) * 0.55,
            tp,
            f"  цель (после сценария) {fmt_price(tp)}  ",
            color=_BOX_GREEN,
            fontsize=6.8,
            fontweight="bold",
            va="top",
            ha="left",
            zorder=6,
        )
    else:
        ax.text(
            x_box + width * 0.02, tp_lbl_y,
            f"тейк {fmt_price(tp)}",
            color=_BOX_GREEN, fontsize=7.5, fontweight="bold", va="center", ha="left", zorder=6,
        )
    ax.text(
        x_box + width * 0.02, sl_lbl_y,
        f"стоп {fmt_price(stop)}",
        color=_BOX_RED, fontsize=7.5, fontweight="bold", va="center", ha="left", zorder=6,
    )
    ax.text(
        x_box + width * 0.02, entry,
        f"вход {fmt_price(entry)}",
        color="#e6edf3", fontsize=7.5, fontweight="bold", va="bottom", ha="left", zorder=6,
        bbox=dict(boxstyle="round,pad=0.15", facecolor="#0d1117", edgecolor="#484f58", alpha=0.9),
    )
