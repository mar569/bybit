"""Короткий «человеческий» разбор для Telegram / manual TA — без простыни метрик."""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scenario_engine import ScenarioPick
    from .ta_analysis import TAAnalysisResult

from .ta_analysis import fmt_price

_SCENARIO_RU = {
    "continuation": "продолжение по старшему ТФ",
    "reversal": "разворот после свипа и слома структуры",
    "range_fade": "отбой от границы диапазона",
    "breakout_compression": "пробой после сжатия у границы",
    "exhaustion_warning": "перегрев — импульс без участия",
}


def reconcile_verdict_with_scenario(
    *,
    verdict: str,
    confidence: int,
    reason: str,
    scenario: "ScenarioPick | None",
    methodology_grade: str = "",
    setup_grade: str = "",
    setup_score: int = 0,
) -> tuple[str, int, str, str]:
    """
    Один источник правды: сценарий C + методология режут локальный LONG/SHORT.
    Возвращает (verdict, confidence, reason, conflict_note_ru).
    """
    v = (verdict or "WAIT").upper()
    conf = int(confidence or 0)
    r = (reason or "").strip()
    note = ""

    grade = (methodology_grade or "").upper()
    if grade == "F" and v in {"LONG", "SHORT"}:
        note = "Методология F — directional-сигнал снят, только наблюдение."
        return "WAIT", min(conf, 6), _join_reason(r, note), note

    if scenario is None:
        return v, conf, r, note

    sc_action = (scenario.action or "WATCH").upper()
    sc_q = (scenario.quality or "C").upper()

    if sc_action in {"LONG", "SHORT"} and v in {"LONG", "SHORT"} and v != sc_action:
        note = (
            f"Локальный {v} не совпадает со сценарием ({sc_action.lower()}): "
            f"{scenario.title_ru}. Вход только после подтверждения на младшем ТФ."
        )
        return "WAIT", min(conf, 6), _join_reason(r, note), note

    if sc_action == "WATCH" and v in {"LONG", "SHORT"}:
        strong_setup = (setup_grade or "").upper() in {"A", "B"} and int(setup_score or 0) >= 7
        if sc_q in {"C", "F"} or not strong_setup:
            note = (
                f"Сценарий «{scenario.title_ru}» — режим наблюдения ({sc_q}). "
                f"Сигнал {v} не исполняем market-ом."
            )
            return "WAIT", min(conf, 6), _join_reason(r, note), note

    if sc_q == "F" and v in {"LONG", "SHORT"}:
        note = f"Качество сценария F — {scenario.reason_ru[:120]}."
        return "WAIT", min(conf, 5), _join_reason(r, note), note

    return v, conf, r, note


def _join_reason(base: str, extra: str) -> str:
    extra = extra.strip()
    if not extra:
        return base
    if extra in base:
        return base
    if base:
        return f"{base} · {extra}"
    return extra


def _clean_reading_narrative(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    for sep in (" · Сценарий C:", " Сценарий C:", " · tier", " · Метод"):
        if sep in text:
            text = text.split(sep)[0].strip()
    if len(text) > 320:
        text = text[:317] + "…"
    return text


def build_human_trade_brief(
    ta: "TAAnalysisResult",
    *,
    symbol: str = "",
    conflict_note: str = "",
) -> str:
    """4–6 предложений plain text (для тестов и логов)."""
    sym = (symbol or "").strip().upper()
    price = float(getattr(ta, "current_price", 0) or 0)
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    narrative = _clean_reading_narrative(getattr(ta, "reading_narrative", "") or "")

    sid = str(getattr(ta, "scenario_engine_id", "") or "").strip()
    sc_title = _SCENARIO_RU.get(sid, "")
    metrics = getattr(ta, "market_metrics", None) or {}
    if isinstance(metrics, dict):
        eng = metrics.get("scenario_engine")
        if isinstance(eng, dict) and eng.get("title"):
            sc_title = str(eng["title"])

    parts: list[str] = []
    replay = str(getattr(ta, "bar_replay_label", "") or "").strip()
    if replay:
        parts.append(f"⏪ {replay}.")

    head = sym or "Монета"
    if price > 0:
        parts.append(f"{head}, цена около {fmt_price(price)}.")
    else:
        parts.append(f"{head}.")

    if stack:
        parts.append(f"Старшие ТФ: {stack}.")
    elif narrative:
        parts.append(narrative)
    elif seek:
        parts.append(seek)
    else:
        parts.append("Структура смешанная — без явного тренда на всех ТФ.")

    if sc_title:
        sq = str(getattr(ta, "scenario_engine_quality", "") or "").strip()
        qbit = f" (оценка {sq})" if sq else ""
        parts.append(f"Рабочий сценарий: {sc_title}{qbit}.")
    elif sid == "breakout_compression":
        tc = {}
        if isinstance(metrics, dict):
            tc = metrics.get("touch_compression") or {}
        if isinstance(tc, dict) and tc.get("label"):
            parts.append(f"У границы: {str(tc['label'])[:120]}.")

    trigger = _trigger_sentence(ta)
    if trigger:
        parts.append(trigger)

    v = (ta.verdict or "WAIT").upper()
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    cn = (conflict_note or str(getattr(ta, "verdict_scenario_note", "") or "")).strip()
    if cn:
        parts.append(f"Итог: {v} {conf}/10 — {cn}")
    elif v == "WAIT":
        wait_why = seek or "нужен триггер на M5/M15 или реакция в зоне"
        parts.append(f"Итог: не входим сейчас — {wait_why}.")
    else:
        parts.append(
            f"Итог: приоритет {v} ({conf}/10), но только лимит/триггер по плану A/B, не погоня."
        )

    inv = getattr(ta, "invalidation_price", None)
    if inv:
        parts.append(f"Отмена идеи: закрепление за {fmt_price(float(inv))}.")

    absent = [str(x) for x in (getattr(ta, "reading_absent", None) or []) if str(x).strip()]
    if absent and v != "WAIT":
        parts.append(f"Пока не подтверждено: {absent[0][:100]}.")
    elif absent and v == "WAIT" and len(absent) > 0:
        parts.append(f"Чего не хватает: {absent[0][:100]}.")

    return " ".join(p for p in parts if p)


def _trigger_sentence(ta: "TAAnalysisResult") -> str:
    plan_html = str(getattr(ta, "htf_ltf_plan_html", "") or "")
    if "LTF триггер" in plan_html:
        import re

        m = re.search(r"LTF триггер:</b>\s*([^<\n]+)", plan_html)
        if m:
            return f"Триггер: {m.group(1).strip()[:140]}."
    if getattr(ta, "setup_trigger", ""):
        return f"Триггер: {str(ta.setup_trigger)[:120]}."
    lo = hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        lo, hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    if lo is not None and hi is not None:
        return f"Зона интереса: {fmt_price(lo)}–{fmt_price(hi)}."
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    if seek:
        return f"Ждём: {seek}."
    return ""


def scanner_side_blocked_by_scenario(ta: "TAAnalysisResult", side: str) -> str:
    """Причина WATCH/skip для сканера, если сценарий C или методология против стороны."""
    side = (side or "").lower()
    from .fib_entry_rules import fib_blocks_market_entry

    fib_block = fib_blocks_market_entry(ta, side)
    if fib_block:
        return fib_block

    metrics = getattr(ta, "market_metrics", None) or {}
    sym = str(getattr(ta, "symbol", "") or "").strip()
    if not sym and isinstance(metrics, dict):
        sym = str(metrics.get("symbol") or "")

    if isinstance(metrics, dict):
        from .btc_regime import btc_blocks_alt_side

        btc_note = btc_blocks_alt_side(
            side,
            str(metrics.get("btc_regime") or ""),
            symbol=sym,
        )
        if btc_note:
            return btc_note

    from .market_state_store import get_market_state_store
    cache_note = get_market_state_store().side_conflict_note(sym, side) if sym else ""
    if cache_note:
        return cache_note

    note = str(getattr(ta, "verdict_scenario_note", "") or "").strip()
    if note:
        return note[:180]

    if isinstance(metrics, dict):
        mw = metrics.get("methodology_weights")
        if isinstance(mw, dict) and str(mw.get("grade") or "").upper() == "F":
            return "Методология F — ENTRY запрещён, только наблюдение."

    sq = str(getattr(ta, "scenario_engine_quality", "") or "").upper()
    if sq == "F":
        return "Качество сценария F — без market-входа."

    sc_act = (getattr(ta, "scenario_engine_action", "") or "").upper()
    if sc_act in {"LONG", "SHORT"} and side in {"long", "short"}:
        want = "long" if sc_act == "LONG" else "short"
        if side != want:
            title = ""
            if isinstance(metrics, dict):
                se = metrics.get("scenario_engine")
                if isinstance(se, dict):
                    title = str(se.get("title") or "")
            return (
                f"Сканер {side.upper()} не совпадает со сценарием ({sc_act}): "
                f"{title[:100] or 'ждём подтверждение HTF'}."
            )
    if sc_act == "WATCH" and side in {"long", "short"}:
        grade = (getattr(ta, "setup_grade", "") or "").upper()
        score = int(getattr(ta, "setup_score", 0) or 0)
        if grade not in {"A", "B"} or score < 7:
            return "Сценарий в режиме наблюдения — ENTRY только после триггера A/B."
    return ""


def build_human_trade_brief_html(
    ta: "TAAnalysisResult",
    *,
    symbol: str = "",
    conflict_note: str = "",
) -> str:
    text = build_human_trade_brief(ta, symbol=symbol, conflict_note=conflict_note)
    if not text:
        return ""
    return f"💬 <b>Разбор</b>\n{escape(text)}"
