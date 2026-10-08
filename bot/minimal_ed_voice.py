"""Короткая человеческая подпись: монета + фаза + куда ждём — без цифр и метрик."""
from __future__ import annotations

import re
from html import escape

from .human_trade_brief import _clean_reading_narrative, _symbol_short, preferred_trade_side
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult

_NUM_RE = re.compile(r"\d[\d.,%]*")


def _strip_numbers(text: str) -> str:
    t = _NUM_RE.sub("", text or "")
    t = re.sub(r"\s{2,}", " ", t).strip(" ·—-")
    return t


def _situation_phrase(ta: TAAnalysisResult) -> str:
    rbr = get_rbr_from_ta(ta)
    if rbr:
        phase = str(rbr.get("phase") or "")
        if phase == "fade_top":
            return "после импульса в боковике, у потолка диапазона"
        if phase == "await_break":
            return "в боковике, пробоя пола ещё не было"
        if phase == "retest":
            return "ретest у пола после пробоя"
        story = _strip_numbers(str(rbr.get("story_ru") or rbr.get("label_ru") or ""))
        if story:
            return story[:120]

    phase = (getattr(ta, "phase_label", "") or getattr(ta, "phase", "") or "").lower()
    if any(k in phase for k in ("боков", "консол", "range", "флэт", "flat")):
        return "в зоне консолидации"
    if bool(getattr(ta, "post_pump", False)):
        return "после импульса, цена у локального экстремума"
    if bool(getattr(ta, "candle_compression", False)):
        return "сжатие волатильности перед выходом"

    narrative = _strip_numbers(_clean_reading_narrative(getattr(ta, "reading_narrative", "") or ""))
    if narrative:
        first = narrative.split(".")[0].strip()
        if len(first) > 8:
            return first[:140]

    seek = _strip_numbers(str(getattr(ta, "reading_seek_label", "") or ""))
    if seek:
        return seek[:120]

    struct = str(getattr(ta, "structure_label", "") or "").strip()
    if struct and struct.lower() not in {"n/a", "—"}:
        return f"структура: {_strip_numbers(struct)[:80]}"

    return "наблюдаем реакцию на уровнях с графика"


def _expect_phrase(ta: TAAnalysisResult) -> str:
    v = (getattr(ta, "verdict", "") or "WAIT").upper()
    side = preferred_trade_side(ta)
    bias = str(getattr(ta, "pattern_foresight_bias", "") or "").lower()
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") == "short":
        if str(rbr.get("phase") or "") == "fade_top":
            return "ждём отказ сверху и движение вниз"
        return "ждём движение вниз по сценарию"

    if side == "short" or bias == "bearish":
        return "ждём движение вниз"
    if side == "long" or bias == "bullish":
        return "ждём движение вверх"
    if v in {"LONG", "SHORT"}:
        return "вход только по триггеру на графике, без догонялки"
    live = str(getattr(ta, "reading_live_scenario", "") or "").lower()
    if "шорт" in live or "short" in live:
        return "ждём движение вниз"
    if "лонг" in live or "long" in live:
        return "ждём движение вверх"
    return "пока без market — смотрим график"


def build_minimal_ed_voice_plain(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> str:
    from .plan_staleness import plan_staleness_plain

    sym = _symbol_short(symbol or getattr(ta, "symbol", "") or "") or "Монета"
    stale = plan_staleness_plain(ta)
    sit = _situation_phrase(ta).rstrip(".")
    exp = _expect_phrase(ta).rstrip(".")
    if stale:
        exp = stale
    elif exp and "опоздал" not in exp.lower():
        pass
    if sit and exp:
        return f"{sym} — {sit}. {exp.capitalize()}."
    if sit:
        return f"{sym} — {sit}."
    return f"{sym} — {exp.capitalize()}."


def build_minimal_ed_voice_html(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> str:
    from .plan_staleness import plan_staleness_line_html

    stale = plan_staleness_line_html(ta)
    plain = build_minimal_ed_voice_plain(ta, symbol=symbol)
    body = ""
    if " — " in plain:
        sym, rest = plain.split(" — ", 1)
        body = f"<b>{escape(sym)}</b> — {escape(rest)}"
    else:
        body = escape(plain)
    if stale:
        return f"{stale}\n\n{body}"
    return body
