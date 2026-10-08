"""Живой разбор: одна история из фактов TA + последние свечи + контекст."""
from __future__ import annotations

from html import escape

from .analysis_context_plugins import domain_context_line
from .chart_story_router import effective_rbr, rbr_short_story_lock
from .human_trade_brief import (
    _clean_reading_narrative,
    _ed_wait_flow_one_liner,
    _symbol_short,
    format_ed_range_wait_html,
    preferred_trade_side,
    use_range_wait_caption,
)
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult, fmt_price


def _candles_block(ta: TAAnalysisResult) -> str:
    replay = str(getattr(ta, "bar_replay_label", "") or "").strip()
    recent = str(getattr(ta, "recent_price_action_ru", "") or "").strip()
    if replay:
        return f"⏪ {escape(replay)}"
    if recent:
        # describe_recent_bars may include <b> for last bar
        if "<b>" in recent:
            return f"🕯 {recent}"
        return f"🕯 {escape(recent)}"
    return ""


def _living_extras(ta: TAAnalysisResult, *, symbol: str) -> list[str]:
    parts: list[str] = []
    candles = _candles_block(ta)
    if candles:
        parts.append(candles)
    domain = domain_context_line(symbol, ta)
    if domain:
        parts.append(f"🌐 {domain}")
    flow = _ed_wait_flow_one_liner(ta)
    if flow:
        parts.append(f"<i>{escape(flow)}</i>")
    return parts


def _price_action_sentence(ta: TAAnalysisResult) -> str:
    dd = float(getattr(ta, "drawdown_from_high_pct", 0) or 0)
    mom = str(getattr(ta, "momentum_label", "") or getattr(ta, "momentum", "") or "").lower()
    if dd >= 2.5 and mom == "down":
        return "Уже виден откат от локального хая — смотрим, держится ли отказ."
    if dd <= 0.8 and bool(getattr(ta, "post_pump", False)):
        return "Импульс вверх только что отработал — цена вплотную у потолка, без «запаса» для догоняющего лонга."
    if mom == "up":
        return "Краткосрочный импульс ещё тянет цену к сопротивлению — не путать с готовым лонгом."
    return "Движение на младшем ТФ замедлилось — нужна реакция на уровне, а не market."


def _flow_at_top_note(ta: TAAnalysisResult) -> str:
    raw = " ".join(str(x) for x in (getattr(ta, "market_participation_lines", None) or [])).lower()
    if "cvd buy" in raw or "агрессивные покуп" in raw:
        return (
            "Покупатели в потоке ещё сильные — часто добивают хай и выбивают шортов "
            "<i>до</i> нормального разворота. Шорт только после отказа, не в зелёную свечу."
        )
    if "перекос в лонг" in raw or "l/s" in raw:
        return "В стакане перекос в лонг — вынос вверх возможен, но это не повод покупать у потолка."
    return ""


def build_living_rbr_html(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> str:
    rbr = effective_rbr(ta) or get_rbr_from_ta(ta)
    if not rbr:
        return ""
    phase = str(rbr.get("phase") or "")
    sym = _symbol_short(symbol or getattr(ta, "symbol", "") or "")
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    floor = fmt_price(float(rbr.get("range_bottom") or 0))
    ceil = fmt_price(float(rbr.get("range_top") or 0))
    story = str(rbr.get("story_ru") or "").strip()
    label = escape(str(rbr.get("label_ru") or "")[:100])
    el = float(rbr.get("entry_lo") or 0)
    eh = float(rbr.get("entry_hi") or 0)
    stop = fmt_price(float(rbr["stop"])) if rbr.get("stop") else "—"
    entry = f"{fmt_price(el)}–{fmt_price(eh)}" if el and eh and eh > el else "—"
    tgs = " → ".join(fmt_price(float(t)) for t in (rbr.get("targets") or [])[:2] if t)

    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    stack_line = f"🧭 {escape(stack)}" if stack else ""
    extras = _living_extras(ta, symbol=symbol)

    if phase == "fade_top":
        head = (
            f"📌 <b>{escape(sym)}</b> · не market ({conf}/10) · "
            f"после выноса, потолок <b>{floor}–{ceil}</b>"
        )
        pa = _price_action_sentence(ta)
        flow_top = _flow_at_top_note(ta)
        core = escape(story[:420]) if story else (
            f"Цена в верхней части боковика <b>{floor}–{ceil}</b>. "
            f"{pa} Лонг здесь — догонялка; рабочая идея — <b>шорт после отказа</b> у {ceil}."
        )
        body_parts = [f"🧠 <b>Разбор</b>", core]
        if flow_top:
            body_parts.append(flow_top)
        plan = (
            f"🎯 Черновик на графике: вход <b>{escape(entry)}</b> · стоп <b>{stop}</b>"
            + (f" · цели <b>{escape(tgs)}</b>" if tgs else "")
        )
        rules = (
            f"🟢 Не шортим в импульс вверх — ждём красную реакцию / pin у {ceil}.\n"
            f"🔴 Закреп <b>под {floor}</b> — усиливает сценарий вниз к целям.\n"
            f"🟡 Пока между {floor} и {ceil} — только наблюдение."
        )
        note = "<i>Коридор, ghost-путь и short-tool — на PNG.</i>"
        parts = [head, stack_line, "\n".join(body_parts), *extras, plan, rules, note]
        return "\n\n".join(p for p in parts if p)

    if phase == "await_break":
        head = f"📌 <b>{escape(sym)}</b> · не market ({conf}/10) · боковик <b>{floor}–{ceil}</b>"
        body = (
            f"🧠 <b>Разбор</b>\n"
            f"Цена ещё <b>не закрепилась под {floor}</b>. {label or 'Шорт активируется после пробоя пола'}, "
            f"не в случайный импульс вниз без close."
        )
        rules = (
            f"🔴 Триггер шорта: close <b>≤ {floor}</b> → retest → вход.\n"
            f"🟡 До пробоя — без market."
        )
        parts = [head, stack_line, body, *extras, rules, "<i>Уровни на графике.</i>"]
        return "\n\n".join(p for p in parts if p)

    if phase == "retest":
        head = f"📌 <b>{escape(sym)}</b> · retest пола <b>{floor}</b> ({conf}/10)"
        body = f"🧠 <b>Разбор</b>\n{escape(story[:400]) if story else label}"
        parts = [head, stack_line, body, *extras, "<i>Стоп/цели на PNG.</i>"]
        return "\n\n".join(p for p in parts if p)

    return ""


def build_living_general_html(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> str:
    """Любой тикер без RBR/range_wait: проза + триггеры, без простыни."""
    sym = _symbol_short(symbol or getattr(ta, "symbol", "") or "")
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    v = (getattr(ta, "verdict", "") or "WAIT").upper()
    side = preferred_trade_side(ta)
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    stack_line = f"🧭 {escape(stack)}" if stack else ""

    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    narrative = _clean_reading_narrative(getattr(ta, "reading_narrative", "") or "")
    if not narrative and seek:
        narrative = seek
    if not narrative:
        narrative = _price_action_sentence(ta)

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)

    if v == "WAIT":
        head = f"📌 <b>{escape(sym)}</b> · подождал бы ({conf}/10) — без market, только триггер."
    else:
        head = f"📌 <b>{escape(sym)}</b> · <b>{v}</b> ({conf}/10) — вход лимитом/по close, не догонять."

    body = f"🧠 <b>Разбор</b>\n{escape(narrative[:420])}"

    rules_parts: list[str] = []
    if brk > 0 and brdn > 0 and brk > brdn:
        brk_s, brdn_s = fmt_price(brk), fmt_price(brdn)
        if side == "long":
            rules_parts.append(f"🟢 Выше <b>{brk_s}</b> — сценарий лонга после закрепа.")
            rules_parts.append(f"🔴 Ниже <b>{brdn_s}</b> — идея лонга off.")
        elif side == "short":
            rules_parts.append(f"🔴 Ниже <b>{brdn_s}</b> — шорт после пробоя.")
            rules_parts.append(f"🟢 Выше <b>{brk_s}</b> — шорт отменяется.")
        else:
            rules_parts.append(f"🟢 Выше <b>{brk_s}</b> · 🔴 Ниже <b>{brdn_s}</b>.")
        rules_parts.append(f"🟡 Между {brdn_s} и {brk_s} — наблюдаем.")
    elif brk > 0:
        rules_parts.append(f"🟢 Триггер вверх: <b>{fmt_price(brk)}</b> (close/retest).")
    elif brdn > 0:
        rules_parts.append(f"🔴 Триггер вниз: <b>{fmt_price(brdn)}</b> (close/retest).")

    absent = list(getattr(ta, "reading_absent", None) or [])[:2]
    if absent:
        miss = " · ".join(escape(str(x)[:70]) for x in absent)
        body += f"\n<i>Пока нет:</i> {miss}"

    extras = _living_extras(ta, symbol=symbol)
    note = "<i>Ключевые уровни и поток — на PNG.</i>"
    parts = [head, stack_line, body, *extras]
    if rules_parts:
        parts.append("\n".join(rules_parts))
    parts.append(note)
    return "\n\n".join(p for p in parts if p)


def build_living_analysis_html(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> str:
    from .chart_display_policy import ed_minimal_voice_enabled
    from .minimal_ed_voice import build_minimal_ed_voice_html

    if ed_minimal_voice_enabled():
        return build_minimal_ed_voice_html(ta, symbol=symbol)

    if get_rbr_from_ta(ta) or effective_rbr(ta):
        html = build_living_rbr_html(ta, symbol=symbol)
        if html:
            return html
    if use_range_wait_caption(ta):
        html = format_ed_range_wait_html(ta, symbol=symbol)
        if html:
            extras = _living_extras(ta, symbol=symbol)
            if extras:
                return html + "\n\n" + "\n".join(extras)
            return html
    return build_living_general_html(ta, symbol=symbol)


def apply_rbr_priority_lock(ta: TAAnalysisResult) -> None:
    if not rbr_short_story_lock(ta):
        return
    ta.action_priority = "short"
