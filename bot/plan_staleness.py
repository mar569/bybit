"""План уже отработал — не выдавать сигнал как «вход сейчас»."""
from __future__ import annotations

from html import escape

from .human_trade_brief import preferred_trade_side
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult


def plan_staleness_plain(ta: TAAnalysisResult) -> str:
    cur = float(getattr(ta, "current_price", 0) or 0)
    if cur <= 0:
        return ""
    side = preferred_trade_side(ta) or str(getattr(ta, "action_priority", "") or "").lower()
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") in {"long", "short"}:
        side = str(rbr["direction"])

    tps = [float(x) for x in (getattr(ta, "target_prices", None) or []) if x]
    if rbr:
        tps = tps or [float(x) for x in (rbr.get("targets") or []) if x]

    if side == "short" and tps:
        hit_tp = any(cur <= float(tp) * 1.012 for tp in tps if float(tp) > 0)
        if hit_tp:
            return "Цена уже у первой цели — вход опоздал, только наблюдение."
        el, eh = (rbr.get("entry_lo"), rbr.get("entry_hi")) if rbr else (None, None)
        if el and eh:
            entry_hi = float(eh)
            nearest_tp = min((float(t) for t in tps if float(t) > 0), default=0.0)
            if nearest_tp and cur < entry_hi * 0.992 and cur <= nearest_tp * 1.03:
                return "Движение к цели уже пошло без нашего входа — не догонять."

    if side == "long" and tps:
        tp1 = max(tps)
        if cur >= tp1 * 0.994:
            return "Цена уже у первой цели — вход опоздал, только наблюдение."

    inv = float(getattr(ta, "invalidation_price", 0) or 0)
    if side == "short" and inv > 0 and cur >= inv * 0.998:
        return "Стоп-зона уже близко — market-шорт не актуален."

    return ""


def plan_staleness_line_html(ta: TAAnalysisResult) -> str:
    msg = plan_staleness_plain(ta)
    if not msg:
        return ""
    return f"⚠️ <b>{escape(msg)}</b>"
