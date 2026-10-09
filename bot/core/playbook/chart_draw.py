"""Draw ChartSpec levels on TV overlay (structure-only)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .chart_spec import ChartLevel, ChartSpec
from ...chart_display_policy import chart_teaching_tags_enabled, ed_playbook_v3_enabled
from ...range_breakdown_retest import get_rbr_from_ta
from ...ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)

_KIND_STYLE = {
    "range_top": ("#f0c040", "ПОТОЛОК"),
    "range_bottom": ("#3fb950", "ПОЛ"),
    "break_up": ("#58a6ff", "↑"),
    "break_down": ("#ff7b72", "↓"),
}


def _levels_from_ta(ta: TAAnalysisResult) -> list[ChartLevel]:
    mm = dict(getattr(ta, "market_metrics", None) or {})
    raw = mm.get("playbook_v3")
    if not isinstance(raw, dict):
        return []
    spec = raw.get("chart_spec")
    if not isinstance(spec, dict):
        return []
    out: list[ChartLevel] = []
    for lv in spec.get("levels") or []:
        if not isinstance(lv, dict):
            continue
        price = float(lv.get("price") or 0)
        if price <= 0:
            continue
        out.append(
            ChartLevel(
                price,
                str(lv.get("label") or ""),
                str(lv.get("kind") or "structure"),
            )
        )
    return out


def draw_playbook_spec_tv(ax: plt.Axes, mapper: object, ta: TAAnalysisResult) -> int:
    """Returns layer count added."""
    if not ed_playbook_v3_enabled():
        return 0
    levels = _levels_from_ta(ta)
    if not levels:
        try:
            from .cache import get_or_run_playbook

            levels = get_or_run_playbook(ta).chart_spec.levels
        except Exception:
            logger.debug("playbook spec for TV draw failed", exc_info=True)
            return 0
    drawn = 0
    show_tags = chart_teaching_tags_enabled()
    for lv in levels:
        if not mapper.price_visible(lv.price):  # type: ignore[attr-defined]
            continue
        color, default_tag = _KIND_STYLE.get(lv.kind, ("#8b949e", lv.label))
        mapper.hline(ax, lv.price, color=color, lw=1.35, alpha=0.88)  # type: ignore[attr-defined]
        drawn += 1
        if show_tags and lv.label:
            tag = default_tag if lv.kind in _KIND_STYLE else lv.label[:12]
            yy = mapper.y(lv.price)  # type: ignore[attr-defined]
            ax.text(
                mapper.x_start + 0.006,  # type: ignore[attr-defined]
                yy,
                f" {tag}",
                color=color,
                fontsize=6.5,
                va="center",
                zorder=8,
            )
    mm = dict(getattr(ta, "market_metrics", None) or {})
    spec = (mm.get("playbook_v3") or {}).get("chart_spec") if isinstance(mm.get("playbook_v3"), dict) else {}
    if isinstance(spec, dict) and spec.get("show_range") and len(levels) >= 2:
        tops = [lv.price for lv in levels if lv.kind == "range_top"]
        bots = [lv.price for lv in levels if lv.kind == "range_bottom"]
        if tops and bots:
            top, bot = max(tops), min(bots)
            i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 56))  # type: ignore[attr-defined]
            mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color="#8b949e", alpha=0.10)  # type: ignore[attr-defined]
            drawn += 1
    return drawn


def draw_playbook_rbr_phase_tv(
    ax: plt.Axes,
    mapper: object,
    ta: TAAnalysisResult,
    spec: ChartSpec,
) -> int:
    """Phase shading (fade_top / retest) — без повторного ПОЛ/ПОТОЛОК (уже в spec levels)."""
    if not spec.show_range or not spec.rbr_phase:
        return 0
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return 0
    from ...chart_display_policy import chart_entry_zone_tags_enabled, chart_teaching_tags_enabled

    phase = spec.rbr_phase
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    drawn = 0
    vis_start = getattr(mapper, "vis_start", 0)
    i0 = vis_start

    if phase == "await_break" and floor > 0 and ceil > floor:
        cur = float(getattr(ta, "current_price", 0) or mapper.bars[-1].close)  # type: ignore[attr-defined]
        if cur >= floor * 0.996:
            mapper.rect(ax, i0, len(mapper.bars) - 1, floor, ceil, color="#8b949e", alpha=0.06)  # type: ignore[attr-defined]
            drawn += 1
            if chart_teaching_tags_enabled():
                ax.text(
                    0.5,
                    0.97,
                    " БОКОВИК · ждём пробой пола",
                    transform=ax.transAxes,
                    ha="center",
                    va="top",
                    color="#8b949e",
                    fontsize=7,
                    zorder=9,
                )
                drawn += 1
    elif phase == "fade_top":
        resistance = ceil if ceil > 0 else float(rbr.get("entry_hi") or 0)
        if resistance > 0:
            el = float(rbr.get("entry_lo") or resistance * 0.985)
            eh = float(rbr.get("entry_hi") or resistance * 1.006)
            z_lo, z_hi = min(el, resistance * 0.998), max(eh, resistance * 1.002)
            mapper.rect(ax, i0, len(mapper.bars) - 1, z_lo, z_hi, color="#f0c040", alpha=0.11)  # type: ignore[attr-defined]
            drawn += 1
            if chart_teaching_tags_enabled() and chart_entry_zone_tags_enabled():
                ax.text(
                    mapper.x_start + 0.008,  # type: ignore[attr-defined]
                    mapper.y(z_hi),  # type: ignore[attr-defined]
                    " ЗОНА ОТКАЗА",
                    color="#f0c040",
                    fontsize=6.5,
                    va="bottom",
                    zorder=8,
                )
            if chart_teaching_tags_enabled():
                ax.text(
                    0.5,
                    0.97,
                    " FADE · у потолка · без входа",
                    transform=ax.transAxes,
                    ha="center",
                    va="top",
                    color="#f0c040",
                    fontsize=7,
                    zorder=9,
                )
                drawn += 1
    elif phase == "retest" and floor > 0:
        el = float(rbr.get("entry_lo") or floor * 0.996)
        eh = float(rbr.get("entry_hi") or floor * 1.01)
        if mapper.price_visible(eh) and mapper.price_visible(el):  # type: ignore[attr-defined]
            mapper.hline(ax, eh, color="#58a6ff", lw=1.5, alpha=0.9)  # type: ignore[attr-defined]
            mapper.hline(ax, el, color="#58a6ff", lw=1.5, alpha=0.9)  # type: ignore[attr-defined]
            if chart_teaching_tags_enabled():
                ax.text(
                    mapper.x_start + 0.008,  # type: ignore[attr-defined]
                    mapper.y(eh),  # type: ignore[attr-defined]
                    " RETEST",
                    color="#58a6ff",
                    fontsize=6.5,
                    va="bottom",
                    zorder=8,
                )
            drawn += 1
    return drawn
