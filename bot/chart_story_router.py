"""Роутер story-chart: ed_story / range_wait / observation / full manual."""
from __future__ import annotations

from typing import Any

from .bybit_klines import KlineBar
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price

_ED_STORY_PHASES = frozenset({"fade_top", "await_break", "retest"})


def build_synthetic_rbr_dict(
    ta: TAAnalysisResult,
    bars: list[KlineBar] | None = None,
) -> dict[str, Any] | None:
    """Если evaluate RBR не прошёл RR, но картина «как у Ed» — достраиваем план для story-chart."""
    if get_rbr_from_ta(ta):
        return None
    if not bool(getattr(ta, "post_pump", False)):
        return None
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return None
    top = float(getattr(cons, "top", 0) or 0)
    bot = float(getattr(cons, "bottom", 0) or 0)
    cur = float(getattr(ta, "current_price", 0) or 0)
    if top <= bot or cur <= 0:
        return None
    if cur > top * 1.004:
        return None
    pos = (cur - bot) / (top - bot)
    if pos < 0.55:
        return None

    floor = float(getattr(ta, "breakdown_level", 0) or bot)
    ceil = float(getattr(ta, "breakout_level", 0) or top)
    if floor <= 0:
        floor = bot
    if ceil <= top * 0.998:
        ceil = top

    swing_tp = floor
    if bars:
        from .range_breakdown_retest import _swing_low_target

        swing_tp = _swing_low_target(bars, floor)

    tps = [float(t) for t in (getattr(ta, "target_prices", None) or []) if t]
    targets = [t for t in tps if t < cur][:2]
    if not targets:
        targets = [floor, swing_tp] if swing_tp < floor * 0.995 else [floor]

    entry_lo, entry_hi = ceil * 0.982, ceil * 1.006
    seg = bars[-min(24, len(bars)) :] if bars else []
    local_hi = max((float(b.high) for b in seg), default=cur)
    stop = max(ceil * 1.012, local_hi * 1.0015, cur * 1.004)
    inv = getattr(ta, "invalidation_price", None)
    if inv and float(inv) > cur:
        stop = max(stop, float(inv))

    story = (
        f"После импульса — боковик {fmt_price(floor)}–{fmt_price(ceil)}; "
        f"цена у потолка. Шорт только после отказа у {fmt_price(ceil)}, "
        f"не в зелёный вынос. Цели: {fmt_price(targets[0])}"
        + (f" → {fmt_price(targets[1])}" if len(targets) > 1 else "")
        + f", если уйдём под {fmt_price(floor)}."
    )

    return {
        "direction": "short",
        "range_top": ceil,
        "range_bottom": floor,
        "phase": "fade_top",
        "entry_lo": entry_lo,
        "entry_hi": entry_hi,
        "stop": stop,
        "targets": targets[:3],
        "swing_low_target": swing_tp,
        "label_ru": "шорт от верха после пампа",
        "story_ru": story,
        "synthetic": True,
    }


def enrich_ta_for_chart_story(
    ta: TAAnalysisResult,
    bars: list[KlineBar] | None = None,
) -> TAAnalysisResult:
    if bars:
        from .chart_rbr_refresh import refresh_rbr_for_chart

        ta = refresh_rbr_for_chart(ta, bars)
        try:
            from .chart_pdf_style import sanitize_rbr_for_pdf

            ta = sanitize_rbr_for_pdf(ta, bars)
        except Exception:
            pass
    if bars and not str(getattr(ta, "recent_price_action_ru", "") or "").strip():
        try:
            from .recent_bars_narrative import describe_recent_bars

            ta.recent_price_action_ru = describe_recent_bars(bars, count=4)
        except Exception:
            pass
    if get_rbr_from_ta(ta):
        return ta
    try:
        from .chart_display_policy import ed_pdf_chart_style_enabled

        if ed_pdf_chart_style_enabled():
            return ta
    except Exception:
        pass
    synth = build_synthetic_rbr_dict(ta, bars)
    if not synth:
        return ta
    mm = dict(getattr(ta, "market_metrics", None) or {})
    mm["range_breakdown_retest"] = synth
    ta.market_metrics = mm
    if (getattr(ta, "verdict", "") or "").upper() == "WAIT":
        ta.action_priority = "short"
    return ta


def resolve_chart_story_kind(ta: TAAnalysisResult) -> str:
    """ed_story | range_wait | observation | full_manual"""
    from .chart_range_wait import use_range_wait_chart

    if use_story_chart(ta):
        return "ed_story"
    if use_range_wait_chart(ta):
        return "range_wait"
    v = (getattr(ta, "verdict", "") or "").upper()
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    if v == "WAIT":
        return "observation"
    if v in {"LONG", "SHORT"} and grade in {"A", "B"}:
        return "full_manual"
    if int(getattr(ta, "setup_clarity", 0) or 0) >= 8 and grade in {"A", "B"}:
        return "full_manual"
    return "observation"


def use_minimal_story_chart(ta: TAAnalysisResult) -> bool:
    return resolve_chart_story_kind(ta) != "full_manual"


def effective_rbr(ta: TAAnalysisResult) -> dict[str, Any] | None:
    return get_rbr_from_ta(ta)


def use_story_chart(ta: TAAnalysisResult) -> bool:
    rbr = effective_rbr(ta)
    if not rbr:
        return False
    return str(rbr.get("phase") or "") in _ED_STORY_PHASES


def rbr_short_story_lock(ta: TAAnalysisResult) -> bool:
    """Приоритет шорта от верха — не перебивать flow/long у потолка."""
    rbr = get_rbr_from_ta(ta)
    if not rbr or str(rbr.get("direction") or "") != "short":
        return False
    phase = str(rbr.get("phase") or "")
    if phase not in {"fade_top", "retest"}:
        return False
    if (getattr(ta, "verdict", "") or "").upper() not in {"WAIT", "SHORT"}:
        return False
    cur = float(getattr(ta, "current_price", 0) or 0)
    ceil = float(rbr.get("range_top") or 0)
    if cur <= 0 or ceil <= 0:
        return True
    return cur >= ceil * 0.94
