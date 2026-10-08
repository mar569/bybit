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

    from .mtf_trader_context import build_trader_macro_paragraph

    macro = build_trader_macro_paragraph(ta, chart_interval=int(getattr(ta, "analysis_interval_minutes", 5) or 5))
    if macro:
        parts.append(macro)

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
    if "Триггер младшего" in plan_html or "LTF триггер" in plan_html:
        import re

        m = re.search(
            r"(?:Триггер младшего ТФ|LTF триггер):</b>\s*([^<\n]+)", plan_html
        )
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
            return "Слабая методология — вход запрещён, только наблюдение."

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
                f"{title[:100] or 'ждём подтверждение на старшем ТФ'}."
            )
    if sc_act == "WATCH" and side in {"long", "short"}:
        grade = (getattr(ta, "setup_grade", "") or "").upper()
        score = int(getattr(ta, "setup_score", 0) or 0)
        if grade not in {"A", "B"} or score < 7:
            return "Пока только наблюдение — вход после подтверждения на графике."
    return ""


def build_trade_analysis_html(ta: "TAAnalysisResult") -> str:
    """Причина + подтверждения + качество плана (как на учебном PNG)."""
    from .chart_analysis_text import collect_reason_and_confirmations
    from .mtf_trader_context import build_trader_macro_lines
    from .trade_plan_quality import validate_trade_plan

    iv = int(getattr(ta, "analysis_interval_minutes", 5) or 5)
    macro = build_trader_macro_lines(ta, chart_interval=iv)
    reason, confirms = collect_reason_and_confirmations(ta)
    absent = _dedupe_reading_lines(list(getattr(ta, "reading_absent", None) or []), max_items=2)
    lines: list[str] = []
    if macro:
        lines.append(f"<b>Контекст:</b> {escape(' '.join(macro[:3]))}")
    if reason:
        lines.append(f"<b>Причина:</b> {escape(reason)}")
    if confirms:
        lines.append("✓ " + " · ".join(escape(c) for c in confirms[:4]))
    if absent:
        lines.append("<i>Не хватает:</i> " + " · ".join(escape(x[:90]) for x in absent))
    pq = validate_trade_plan(ta)
    if pq.ok and pq.rr > 0:
        lines.append(
            f"План: риск ~{pq.risk_pct:.1f}% · цель ~{pq.reward_pct:.1f}% · ≈1:{pq.rr:.1f}"
        )
    elif not pq.ok and pq.reason_ru:
        lines.append(f"⚠️ План: {escape(pq.reason_ru)}")
    return "\n".join(lines)


def build_human_trade_brief_html(
    ta: "TAAnalysisResult",
    *,
    symbol: str = "",
    conflict_note: str = "",
) -> str:
    parts: list[str] = []
    analysis = build_trade_analysis_html(ta)
    if analysis:
        parts.append(f"📋 {analysis}")
    text = build_human_trade_brief(ta, symbol=symbol, conflict_note=conflict_note)
    if text:
        parts.append(f"💬 <b>Разбор</b>\n{escape(text)}")
    return "\n\n".join(parts)


def _dedupe_reading_lines(items: list[str], *, max_items: int = 4) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        line = str(raw or "").strip()
        if not line:
            continue
        key = line.split(":", 1)[0].strip().lower() if ":" in line else line[:24].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= max_items:
            break
    return out


def preferred_trade_side(ta: "TAAnalysisResult") -> str:
    sc = str(getattr(ta, "scenario_engine_action", "") or "").upper()
    if sc in {"LONG", "SHORT"}:
        return sc.lower()
    ap = (getattr(ta, "action_priority", "") or "").lower()
    if ap in {"long", "short"}:
        return ap
    v = (ta.verdict or "WAIT").upper()
    if v in {"LONG", "SHORT"}:
        return v.lower()
    return ""


def _manual_levels_html(ta: "TAAnalysisResult", *, side: str) -> str:
    from .manual_plan_display import manual_display_stop_and_targets

    cur = float(getattr(ta, "current_price", 0) or 0)
    stop, targets = manual_display_stop_and_targets(ta)
    targets = [float(x) for x in targets if x]
    zone = getattr(ta, "entry_zone", None)
    brk = getattr(ta, "breakout_level", None)
    brdn = getattr(ta, "breakdown_level", None)
    ns = getattr(ta, "nearest_support", None)
    nr = getattr(ta, "nearest_resistance", None)
    setup_e = getattr(ta, "setup_entry", None)
    if not zone and setup_e and float(setup_e) > 0:
        e = float(setup_e)
        zone = (e * 0.9985, e * 1.0015)
    if not zone and not stop and not targets and not brk and not brdn and not ns and not nr:
        return ""

    if side == "long" and targets and cur > 0 and all(t < cur * 0.998 for t in targets):
        return (
            "⚠️ <i>Черновые TP ниже цены при уклоне в лонг — "
            "уровни пересчитаются после триггера; сейчас ориентир только текст «Ждём».</i>"
        )
    if side == "short" and targets and cur > 0 and all(t > cur * 1.002 for t in targets):
        return (
            "⚠️ <i>Черновые TP выше цены при уклоне в шорт — "
            "ждём подтверждения структуры.</i>"
        )

    bits: list[str] = []
    side_ru = {"long": "лонг", "short": "шорт"}.get(side, "сделка")
    if brk and cur > 0 and abs(float(brk) - cur) / cur <= 0.15:
        bits.append(f"пробой лонг ≥ <b>{fmt_price(float(brk))}</b>")
    if brdn and cur > 0 and abs(float(brdn) - cur) / cur <= 0.15:
        bits.append(f"пробой шорт ≤ <b>{fmt_price(float(brdn))}</b>")
    if ns and cur > 0 and abs(float(ns) - cur) / cur <= 0.12:
        bits.append(f"поддерж. <b>{fmt_price(float(ns))}</b>")
    if nr and cur > 0 and abs(float(nr) - cur) / cur <= 0.12:
        bits.append(f"сопр. <b>{fmt_price(float(nr))}</b>")
    if zone and len(zone) == 2:
        lo, hi = float(zone[0]), float(zone[1])
        bits.append(f"вход ({side_ru}): <b>{fmt_price(lo)}–{fmt_price(hi)}</b>")
    if stop:
        bits.append(f"стоп: <b>{fmt_price(float(stop))}</b>")
    if targets:
        tps = " → ".join(fmt_price(t) for t in targets)
        bits.append(f"цели: {tps}")
    if not bits:
        return ""
    return "📍 <b>План</b> (не market, только после триггера): " + " · ".join(bits)


def flow_strip_lines_for_chart(ta: object, *, max_lines: int = 2) -> list[str]:
    """Коротко для PNG — без дубля в Telegram."""
    raw = [str(x).strip() for x in (getattr(ta, "market_participation_lines", None) or []) if str(x).strip()]
    picked: list[str] = []
    for line in raw[1:] if len(raw) > 1 else raw:
        low = line.lower()
        if any(k in low for k in ("funding", "cvd", "l/s", "oi ", "поток", "taker")):
            picked.append(line[:95])
        if len(picked) >= max_lines:
            break
    if len(picked) < max_lines:
        btc = (getattr(ta, "btc_context", "") or "").strip()
        if btc:
            picked.append(btc[:90])
    return picked[:max_lines]


def format_rbr_alert_caption_html(
    ta: "TAAnalysisResult",
    *,
    symbol: str = "",
) -> str:
    """WATCH/alert при RBR: текст минимальный — уровни и поток на PNG."""
    from .range_breakdown_retest import get_rbr_from_ta

    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return ""
    sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    phase = str(rbr.get("phase") or "")
    floor = fmt_price(float(rbr.get("range_bottom") or 0))
    ceil = fmt_price(float(rbr.get("range_top") or 0))
    label = escape(str(rbr.get("label_ru") or "сценарий")[:120])
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    stack_short = ""
    if stack:
        parts = [p.strip() for p in stack.replace("→", "·").split("·") if p.strip()]
        if len(parts) > 3:
            stack_short = f"🧭 {escape(parts[0])} … {escape(parts[-1])}"
        else:
            stack_short = f"🧭 {escape(stack[:100])}"

    if phase in {"fade_top", "await_break"}:
        head = (
            f"📌 <b>{escape(sym)}</b> · не market ({conf}/10) · "
            f"боковик <b>{floor}–{ceil}</b>"
        )
        if phase == "await_break":
            action = f"⏳ <b>{label}</b> — триггер: закреп <b>под {floor}</b>, не шорт в импульс."
        else:
            action = f"⏳ <b>{label}</b> — реакция у <b>{ceil}</b>, не в зелёный импульс."
    elif phase == "retest":
        head = f"📌 <b>{escape(sym)}</b> · не market ({conf}/10) · retest пола <b>{floor}</b>"
        action = f"⏳ <b>{label}</b> — вход только после подтверждения."
    else:
        head = f"📌 <b>{escape(sym)}</b> · наблюдение ({conf}/10)"
        action = f"⏳ {label}"

    note = "<i>Уровни, SL/TP и поток — на графике.</i>"
    parts = [head, stack_short, action, note]
    return "\n".join(p for p in parts if p)


def format_manual_ta_human_html(
    ta: "TAAnalysisResult",
    *,
    symbol: str = "",
) -> str:
    """Подпись manual TA: один вердикт, ждём, план уровней, поток — без A/B/C и setup D."""
    from .range_breakdown_retest import get_rbr_from_ta

    if get_rbr_from_ta(ta):
        compact = format_rbr_alert_caption_html(ta, symbol=symbol)
        if compact:
            return compact

    sym = (symbol or "").strip().upper()
    v = (ta.verdict or "WAIT").upper()
    side = preferred_trade_side(ta)
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)

    if v == "WAIT":
        if side == "long":
            head = (
                f"📌 <b>Сейчас не входим</b> ({conf}/10). "
                "Уклон в <b>лонг</b> — только после подтверждения (close/ретest), не догонять."
            )
        elif side == "short":
            head = (
                f"📌 <b>Сейчас не входим</b> ({conf}/10). "
                "Уклон в <b>шорт</b> — только после подтверждения, не шортить в импульс."
            )
        else:
            head = (
                f"📌 <b>Сейчас не входим</b> ({conf}/10). "
                "Старшие ТФ спорят или цена в середине — нет смысла в market."
            )
    else:
        head = f"📌 Вердикт <b>{v}</b> ({conf}/10) — вход только лимит/триггер по плану."

    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    stack_line = f"🧭 {escape(stack)}" if stack else ""

    from .range_breakdown_retest import get_rbr_from_ta

    rbr = get_rbr_from_ta(ta)
    rbr_story = str(rbr.get("story_ru", "") if rbr else "").strip()

    sit_html = str(getattr(ta, "situational_brief_html", "") or "").strip()
    if rbr_story:
        story = escape(rbr_story[:480])
    elif sit_html:
        story = sit_html
    else:
        narrative = _clean_reading_narrative(getattr(ta, "reading_narrative", "") or "")
        if not narrative:
            from .situational_brief import build_situational_brief_plain

            narrative = build_situational_brief_plain(ta, symbol=sym) or build_human_trade_brief(
                ta, symbol=sym
            )
            for cut in ("Итог:", "Отмена идеи:", "Чего не хватает:"):
                if cut in narrative:
                    narrative = narrative.split(cut)[0].strip()
        story = escape(narrative[:480]) if narrative else ""

    trigger = str(getattr(ta, "setup_trigger", "") or "").strip()
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    if rbr and rbr.get("label_ru"):
        wait_line = f"⏳ <b>Ждём:</b> {escape(str(rbr['label_ru'])[:220])}"
    elif trigger:
        wait_line = f"⏳ <b>Ждём:</b> {escape(trigger[:220])}"
    elif seek:
        wait_line = f"⏳ <b>Ждём:</b> {escape(seek[:220])}"
    else:
        wait_line = ""

    absent = _dedupe_reading_lines(list(getattr(ta, "reading_absent", None) or []), max_items=2)
    if absent:
        miss = " · ".join(escape(x[:80]) for x in absent)
        wait_line = (wait_line + f"\n<i>Пока нет:</i> {miss}").strip()

    levels = _manual_levels_html(ta, side=side)

    flow_raw = [str(x).strip() for x in (getattr(ta, "market_participation_lines", None) or []) if str(x).strip()]
    flow_body = flow_raw[1:4] if len(flow_raw) > 1 else flow_raw[:3]
    flow_block = ""
    if flow_body:
        flow_block = "📊 <b>Поток</b>\n" + "\n".join(escape(line[:140]) for line in flow_body)

    btc = (getattr(ta, "btc_context", "") or "").strip()
    if not btc and getattr(ta, "btc_alt_spread", None) is not None:
        btc = f"альт vs BTC {ta.btc_alt_spread:+.1f}%"
    btc_line = f"₿ {escape(btc[:100])}" if btc else ""

    iv = int(getattr(ta, "analysis_interval_minutes", 0) or 0)
    tf_note = f"<i>Уровни и PNG: {iv}m · план совпадает с видимым боковиком на скрине.</i>" if iv >= 5 else ""
    parts = [head, stack_line, story, wait_line, levels, flow_block, btc_line, tf_note]
    return "\n\n".join(p for p in parts if p)
