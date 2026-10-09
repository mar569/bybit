"""План уже отработал — не выдавать сигнал как «вход сейчас»."""
from __future__ import annotations

from html import escape

from .human_trade_brief import preferred_trade_side
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult


def _side_and_tps(ta: TAAnalysisResult) -> tuple[str, list[float], dict | None]:
    side = preferred_trade_side(ta) or str(getattr(ta, "action_priority", "") or "").lower()
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") in {"long", "short"}:
        side = str(rbr["direction"])
    tps = [float(x) for x in (getattr(ta, "target_prices", None) or []) if x]
    if rbr:
        tps = tps or [float(x) for x in (rbr.get("targets") or []) if x]
    return side, tps, rbr


def plan_staleness_plain(ta: TAAnalysisResult) -> str:
    cur = float(getattr(ta, "current_price", 0) or 0)
    if cur <= 0:
        return ""
    side, tps, rbr = _side_and_tps(ta)

    if side == "short" and tps:
        for raw_tp in tps:
            tp = float(raw_tp)
            if tp <= 0:
                continue
            if tp <= cur * 1.015 and cur <= tp * 1.012:
                return "Цена уже у первой цели — вход опоздал, только наблюдение."
        below = [float(tp) for tp in tps if float(tp) > 0 and float(tp) < cur * 0.999]
        el, eh = (rbr.get("entry_lo"), rbr.get("entry_hi")) if rbr else (None, None)
        if el and eh and below:
            entry_hi = float(eh)
            nearest_tp = max(below)
            if cur < entry_hi * 0.992 and cur <= nearest_tp * 1.03:
                return "Движение к цели уже пошло без нашего входа — не догонять."

    if side == "long" and tps:
        for raw_tp in tps:
            tp = float(raw_tp)
            if tp <= 0:
                continue
            if tp >= cur * 0.985 and cur >= tp * 0.994:
                return "Цена уже у первой цели — вход опоздал, только наблюдение."

    inv = float(getattr(ta, "invalidation_price", 0) or 0)
    stop_px = inv
    if rbr and rbr.get("stop"):
        stop_px = float(rbr["stop"])
    if side == "short" and stop_px > 0 and cur >= stop_px * 0.997:
        return "Стоп-зона уже пробита — сценарий не актуален, только наблюдение."
    if side == "short" and inv > cur * 1.001 and cur >= inv * 0.998:
        return "Стоп-зона уже близко — market-шорт не актуален."

    if side == "long" and inv > 0 and inv < cur * 0.999 and cur <= inv * 1.002:
        return "Стоп-зона уже близко — market-лонг не актуален."

    if rbr and side == "short":
        phase = str(rbr.get("phase") or "")
        floor = float(rbr.get("range_bottom") or 0)
        el = float(rbr.get("entry_lo") or 0)
        eh = float(rbr.get("entry_hi") or 0)
        if phase == "await_break" and floor > 0 and cur < floor * 0.996:
            return "Пробой пола уже был — ждём retest, старый вход у потолка не актуален."
        if phase in {"retest", "broken"} and eh > el > 0 and cur < el * 0.985:
            return "Цена ушла от зоны входа — план устарел, не догонять."
        if phase == "fade_top" and eh > 0 and cur > eh * 1.018:
            return "Импульс выше зоны шорта — вход опоздал."
        if phase == "fade_top" and floor > 0 and cur < floor * 0.992:
            return "Пробой пола до входа в шорт — сценарий от верха не актуален."

    if rbr and side == "long":
        el = float(rbr.get("entry_lo") or 0)
        eh = float(rbr.get("entry_hi") or 0)
        if eh > el > 0 and cur > eh * 1.015:
            tp_hit = any(cur >= float(tp) * 0.994 for tp in tps if tp)
            if tp_hit:
                return "Цена уже у первой цели — вход опоздал, только наблюдение."

    return ""


def plan_is_stale(ta: TAAnalysisResult) -> bool:
    return bool(plan_staleness_plain(ta))


def plan_staleness_line_html(ta: TAAnalysisResult) -> str:
    msg = plan_staleness_plain(ta)
    if not msg:
        return ""
    return f"⚠️ <b>{escape(msg)}</b>"
