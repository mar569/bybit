"""Профессиональный аналитический слой: тезис сделки с Elliott ABC + Fib + уровнями.

Не «цена пошла вверх» — а аргументированный план:
структура → волна → Fib/confluence → вход / стоп / цели → когда НЕ входить.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .ta_analysis import TAAnalysisResult, fmt_price, ta_display_score

if TYPE_CHECKING:
    from .models import Signal
    from .trade_decision_gate import TradeDecision

_PHASE_RU = {
    "shallow_pullback": "мелкий откат",
    "wave_2_4_zone": "зона Fib 0.5–0.618",
    "deep_pullback": "глубокий откат",
    "mid_correction": "коррекция",
    "late_impulse": "финал импульса",
}


@dataclass
class TradeThesis:
    side: str
    action: str  # entry | watch | skip
    confidence: int
    headline: str
    thesis: str
    structure_line: str
    wave_line: str
    fib_line: str
    entry_price: float | None = None
    entry_zone: tuple[float, float] | None = None
    stop_price: float | None = None
    target_prices: list[float] = field(default_factory=list)
    invalidation: str = ""
    wait_for: str = ""
    arguments: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    fib_action: str = ""  # короткая инструкция: входить / ждать / Fib не строим


def _pick_side(signal: Signal, ta: TAAnalysisResult) -> str:
    """Сторона Hot-плана = сторона сканера для pump/dump (без LONG Fib на дамп-заголовке)."""
    sig = (signal.side or "long").lower()
    st = (signal.signal_type or "").lower()
    scanner_locked = (
        st.endswith("_dump")
        or st.endswith("_pump")
        or st in {"pulse_dump", "pulse_pump", "pump", "dump"}
    )
    if scanner_locked and sig in {"long", "short"}:
        return sig
    if ta.verdict == "LONG":
        return "long"
    if ta.verdict == "SHORT":
        return "short"
    if ta.action_priority in {"long", "short"}:
        return ta.action_priority
    return sig


def _fib_price_at(ta: TAAnalysisResult, ratio: float) -> float | None:
    for lv in ta.fib_levels or []:
        if lv.kind == "retracement" and abs(lv.ratio - ratio) < 1e-9:
            return lv.price
    for lv in ta.fib_levels or []:
        if abs(lv.ratio - ratio) < 1e-9:
            return lv.price
    return None


def _fib_zone_text(ta: TAAnalysisResult) -> str:
    if not ta.fib_levels:
        return ""
    ratios = sorted({lv.ratio for lv in ta.fib_levels if lv.kind == "retracement"})
    if not ratios:
        return ""
    key = [r for r in ratios if r in {0.382, 0.5, 0.618, 0.786}]
    if not key:
        key = ratios[:3]
    parts = [f"{r:.3f}" for r in key]
    phase = _PHASE_RU.get(ta.wave_phase or "", "")
    zone = f"Fib {', '.join(parts)}"
    if phase:
        return f"{zone} · {phase}"
    return zone


def _confluence_text(ta: TAAnalysisResult) -> str:
    if not ta.wave_has_confluence:
        return "Fib без confluence с П/С — только подсказка, не вход"
    bits: list[str] = []
    if getattr(ta, "wave_confluence_sr", False):
        bits.append("П/С")
    if getattr(ta, "wave_confluence_round", False):
        bits.append("круглый")
    if getattr(ta, "wave_confluence_retest", False):
        bits.append("ретест пробоя")
    if not bits:
        bits.append(f"confluence ×{ta.wave_confluence_count}")
    return "Fib + " + " + ".join(bits)


def build_fib_action_text(
    ta: TAAnalysisResult,
    *,
    action: str = "watch",
    location: str = "",
    side: str = "long",
    entry_zone: tuple[float, float] | None = None,
    stop_price: float | None = None,
    target_prices: list[float] | None = None,
) -> str:
    """Понятная инструкция: входить сейчас / ждать Fib / Fib не строим.

    Используется в Hot/Pro и ручном TA.
    """
    _ = side
    status = (getattr(ta, "fib_status", "") or "").lower()
    reject = (getattr(ta, "fib_reject_reason", "") or "").strip()
    location = (location or "").lower()
    action = (action or "watch").lower()
    zone = entry_zone or ta.entry_zone
    stop = stop_price if stop_price is not None else ta.invalidation_price
    targets = list(target_prices or ta.target_prices or [])[:3]

    f618 = _fib_price_at(ta, 0.618)
    f500 = _fib_price_at(ta, 0.5)
    wait_px = f618 or f500

    if action == "entry" and location == "fib":
        parts = ["Вход от Fib 0.5–0.618"]
        if zone:
            parts.append(f"зона {fmt_price(zone[0])}–{fmt_price(zone[1])}")
        elif wait_px:
            parts.append(f"≈ {fmt_price(wait_px)}")
        if stop:
            parts.append(f"стоп {fmt_price(stop)}")
        if targets:
            parts.append("TP " + "/".join(fmt_price(t) for t in targets))
        return " · ".join(parts)

    if action == "entry" and ta.wave_has_confluence and ta.fib_levels:
        parts = ["Вход у Fib-зоны"]
        if zone:
            parts.append(f"{fmt_price(zone[0])}–{fmt_price(zone[1])}")
        if stop:
            parts.append(f"стоп {fmt_price(stop)}")
        return " · ".join(parts)

    if action == "watch":
        if wait_px:
            ratio_lbl = "0.618" if f618 else "0.5"
            return (
                f"Не входить сейчас · ждать откат к Fib ~{fmt_price(wait_px)} "
                f"({ratio_lbl}) или ретест"
            )
        if status in {"late_impulse", "late"} or ta.wave_phase == "late_impulse":
            return "Не входить сейчас · финал импульса — ждать откат к Fib 0.5–0.618"
        if status == "chart_only" or (ta.fib_levels and not ta.wave_has_confluence):
            return (
                "Не входить по Fib · нет confluence с П/С — "
                "ждать совпадение или ретест"
            )
        if reject:
            return f"Не входить сейчас · {reject[:90]}"
        return "Не входить сейчас · ждать откат к Fib или ретест"

    if action == "skip":
        if reject:
            return f"Fib не строим: {reject[:90]} · вход только от П/С или ретеста"
        return "Fib не применяем · вход только от П/С или ретеста"

    if status in {"no_impulse", "broken", "empty"} or not ta.fib_levels:
        reason = reject or "нет валидной импульсной ноги A→B"
        return f"Fib не строим: {reason[:90]} · вход только от П/С или ретеста"

    if status == "chart_only":
        return "Fib на графике · без confluence — не вход, только ориентир"

    if status == "late_impulse" or ta.wave_phase == "late_impulse":
        return "Финал импульса — не вдогонку · ждать Fib 0.5–0.618"

    if status == "ready" and wait_px:
        return f"Fib готов · зона ≈ {fmt_price(wait_px)} (0.5–0.618)"

    return _fib_zone_text(ta) or "Fib: смотреть график"


def fib_action_line_html(ta: TAAnalysisResult, *, side: str | None = None) -> str:
    """Однострочный Fib для ручного TA / caption без TradeDecision."""
    side = side or (ta.action_priority if ta.action_priority in {"long", "short"} else "long")
    if ta.verdict == "LONG":
        action = "entry"
        location = "fib" if ta.wave_has_confluence else ""
    elif ta.verdict == "SHORT":
        action = "entry"
        location = "fib" if ta.wave_has_confluence else ""
    elif (getattr(ta, "fib_status", "") or "") in {"no_impulse", "broken", "empty"}:
        action = "skip"
        location = ""
    else:
        action = "watch"
        location = ""
    text = build_fib_action_text(ta, action=action, location=location, side=side)
    return f"📐 {text}"


def _resolve_levels(
    signal: Signal,
    ta: TAAnalysisResult,
    side: str,
) -> tuple[float | None, tuple[float, float] | None, float | None, list[float]]:
    is_long = side == "long"
    entry: float | None = None
    zone: tuple[float, float] | None = ta.entry_zone
    stop = ta.invalidation_price
    targets = [t for t in (ta.target_prices or []) if t > 0][:4]

    # Точный EW-вход перекрывает зону/пробой
    if ta.elliott_entry_ready and ta.elliott_entry_price:
        entry = float(ta.elliott_entry_price)
        if ta.elliott_stop_price:
            stop = float(ta.elliott_stop_price)
        if ta.elliott_tp_prices:
            targets = [float(t) for t in ta.elliott_tp_prices if t][:4]
        pad = abs(entry) * 0.0015
        zone = (entry - pad, entry + pad)
    elif (
        getattr(ta, "setup_ideal_ready", False)
        and getattr(ta, "setup_entry", None)
        and (getattr(ta, "setup_side", "") or "").lower() == side
    ):
        entry = float(ta.setup_entry)
        if ta.setup_stop:
            stop = float(ta.setup_stop)
        if ta.setup_tps:
            targets = [float(t) for t in ta.setup_tps if t][:4]
        pad = abs(entry) * 0.0015
        zone = (entry - pad, entry + pad)
    elif zone:
        entry = (zone[0] + zone[1]) / 2.0
    elif is_long and ta.breakout_level:
        entry = ta.breakout_level
    elif not is_long and ta.breakdown_level:
        entry = ta.breakdown_level

    if not targets:
        if is_long and ta.breakout_level:
            targets = [ta.breakout_level * 1.012, ta.breakout_level * 1.025]
        elif not is_long and ta.breakdown_level:
            targets = [ta.breakdown_level * 0.988, ta.breakdown_level * 0.975]

    return entry, zone, stop, targets


def _pullback_wait_hint(ta: TAAnalysisResult, side: str) -> str:
    """Уровень ожидания при WATCH — не цена у хая/лоя."""
    side = (side or "").lower()
    if side == "long":
        if ta.nearest_support and ta.nearest_support > 0:
            return f"откат к поддержке ≈ {fmt_price(ta.nearest_support)}"
        if ta.breakout_level and ta.current_price:
            if float(ta.current_price) > float(ta.breakout_level):
                return f"ретест пробоя ≈ {fmt_price(ta.breakout_level)}"
        if ta.fib_levels:
            for ratio in (0.618, 0.5, 0.382):
                for lv in ta.fib_levels:
                    if lv.kind == "retracement" and abs(lv.ratio - ratio) < 0.02:
                        return f"откат Fib {ratio:.3f} ≈ {fmt_price(lv.price)}"
    else:
        if ta.nearest_resistance and ta.nearest_resistance > 0:
            return f"откат к сопротивлению ≈ {fmt_price(ta.nearest_resistance)}"
        if ta.breakdown_level and ta.current_price:
            if float(ta.current_price) < float(ta.breakdown_level):
                return f"ретест пробоя ≈ {fmt_price(ta.breakdown_level)}"
        if ta.fib_levels:
            for ratio in (0.618, 0.5, 0.382):
                for lv in ta.fib_levels:
                    if lv.kind == "retracement" and abs(lv.ratio - ratio) < 0.02:
                        return f"откат Fib {ratio:.3f} ≈ {fmt_price(lv.price)}"
    return "откат к Fib / ретест уровня"


def build_trade_thesis(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    decision: TradeDecision | None = None,
    readiness: tuple[bool, str] | None = None,
) -> TradeThesis:
    """Собрать аргументированный план как у аналитика-трейдера."""
    side = _pick_side(signal, ta)
    action = decision.action if decision else ("entry" if readiness and readiness[0] else "watch")
    conf = ta_display_score(ta)
    if ta.wave_confidence:
        conf = min(10, max(conf, ta.wave_confidence))

    entry, zone, stop, targets = _resolve_levels(signal, ta, side)
    label = "LONG" if side == "long" else "SHORT"
    location = decision.location if decision else ""

    structure = ta.structure_label or "структура не определена"
    phase = ta.phase_label or ta.phase or ""
    structure_line = f"Структура: <b>{structure}</b>"
    if phase:
        structure_line += f" · {phase}"
    if ta.primary_chart_pattern:
        pat = ta.primary_chart_pattern
        status = "подтв." if pat.status == "confirmed" else "форм."
        structure_line += f" · {pat.label_ru} ({status})"
        if pat.target_price:
            structure_line += f" → цель {fmt_price(pat.target_price)}"

    wave_bits: list[str] = []
    if ta.wave_leg_start and ta.wave_leg_end:
        wave_bits.append(
            f"импульс A→B {fmt_price(ta.wave_leg_start)}→{fmt_price(ta.wave_leg_end)}"
        )
    if ta.elliott_label:
        wave_bits.append(ta.elliott_label)
    elif ta.wave_phase:
        wave_bits.append(_PHASE_RU.get(ta.wave_phase, ta.wave_phase))
    if ta.abc_label_ru and ta.abc_label_ru not in (ta.elliott_label or ""):
        wave_bits.append(ta.abc_label_ru)
    if getattr(ta, "htf_elliott_label", ""):
        wave_bits.append(f"HTF: {ta.htf_elliott_label}")
    if getattr(ta, "setup_label_ru", ""):
        wave_bits.append(ta.setup_label_ru)
    if ta.elliott_entry_mode in {"conservative", "aggressive"} and ta.elliott_entry_price:
        mode_ru = "конс." if ta.elliott_entry_mode == "conservative" else "агр."
        wave_bits.append(
            f"EW {mode_ru} вход ≈ {fmt_price(ta.elliott_entry_price)}"
            + (" ✓" if ta.elliott_entry_ready else "")
        )
    wave_line = "Импульс: " + (" · ".join(wave_bits) if wave_bits else "не подтверждён")

    fib_action = build_fib_action_text(
        ta,
        action=action,
        location=location,
        side=side,
        entry_zone=zone,
        stop_price=stop,
        target_prices=targets,
    )
    fib_line = fib_action
    if ta.wave_has_confluence and location == "fib":
        fib_line = f"{fib_action} · {_confluence_text(ta)}"
    else:
        fib_zone = _fib_zone_text(ta)
        if fib_zone and fib_zone not in fib_line:
            fib_line = f"{fib_action} · {fib_zone}"

    arguments: list[str] = []
    if ta.oi_narrative_label and ta.oi_narrative_label != "Мало данных OI":
        arguments.append(f"OI: {ta.oi_narrative_label}")
    if ta.momentum_label:
        arguments.append(f"Импульс: {ta.momentum_label} ({ta.momentum_pct:+.1f}%)")
    if ta.smc_summary:
        arguments.append(f"SMC: {ta.smc_summary[:60]}")
    if getattr(ta, "setup_factors", None):
        arguments.append("Confluence: " + " · ".join(ta.setup_factors[:3]))
    if ta.btc_context:
        arguments.append(f"BTC: {ta.btc_context[:50]}")
    if ta.narrative_basis:
        plain = ta.narrative_basis.replace("<b>", "").replace("</b>", "")[:120]
        arguments.append(plain)

    risks: list[str] = []
    if ta.wave_phase == "late_impulse":
        risks.append("финал импульса — вход только после отката")
    if ta.post_pump and ta.range_position >= 0.85:
        risks.append(f"post-pump у хая ({ta.range_position:.0%} range)")
    if ta.repeat_spike_dump_risk:
        risks.append(ta.repeat_spike_dump_note or "риск повторного сброса")
    if "вход невыгоден" in (ta.verdict_reason or "").lower():
        risks.append("плохое R:R")

    wait_for = ""
    is_chase_watch = (
        action == "watch"
        and (
            (decision is not None and decision.chase)
            or ta.wave_phase == "late_impulse"
            or (ta.post_pump and ta.range_position >= 0.80)
            or ta.range_position >= 0.82
        )
    )
    if action == "watch":
        # Главный текст ожидания — из Fib action (понятная цена)
        wait_for = fib_action
        if is_chase_watch:
            hint = _pullback_wait_hint(ta, side)
            if hint and hint not in wait_for:
                wait_for = f"{wait_for} · {hint}"
        # Нет конкретной Fib-цены — добавим уровень входа (пробой / зона)
        if "Fib ~" not in wait_for and "зона " not in wait_for.lower():
            if zone:
                mid = (zone[0] + zone[1]) / 2.0
                wait_for = f"{wait_for} · вход ≈ {fmt_price(mid)}"
            elif side == "long" and ta.breakout_level and not is_chase_watch:
                wait_for = f"{wait_for} · вход ≈ {fmt_price(ta.breakout_level)}"
            elif side == "short" and ta.breakdown_level and not is_chase_watch:
                wait_for = f"{wait_for} · вход ≈ {fmt_price(ta.breakdown_level)}"
        fib_action = wait_for  # Hot показывает ту же строку с ценой

    if action == "entry":
        headline = f"🎯 <b>{label}</b> · сетап {conf}/10"
        if location == "confluence" or (
            getattr(ta, "setup_ideal_ready", False)
            and (getattr(ta, "setup_side", "") or "").lower() == side
            and getattr(ta, "setup_grade", "") in {"A", "B"}
        ):
            thesis = (
                f"Pro confluence <b>{ta.setup_grade}</b> ({ta.setup_score}/100): "
                f"{ta.setup_label_ru or 'HTF EW + фигура + Fib/SMC'}. "
                f"{ta.setup_trigger or 'вход по согласованной структуре'}."
            )
        elif location == "elliott" or (
            ta.elliott_entry_ready and ta.elliott_entry_mode in {"conservative", "aggressive"}
        ):
            mode_ru = (
                "консервативный (пробой волны 1/3)"
                if ta.elliott_entry_mode == "conservative"
                else "агрессивный (Fib C 1.272/1.618×B)"
            )
            thesis = (
                f"Волны Эллиотта: <b>{mode_ru}</b> вход. "
                f"{ta.elliott_label or 'структура 1–5+ABC'}."
            )
        elif location == "fib" or ta.wave_phase == "wave_2_4_zone":
            thesis = (
                f"Импульс завершён, цена в зоне <b>Fib 0.5–0.618</b>. "
                f"{_confluence_text(ta)} — вход лимиткой в зону, не market вдогонку."
            )
        elif ta.abc_phase == "C":
            thesis = (
                f"Коррекция к импульсу в зоне продолжения. "
                f"{_confluence_text(ta)}."
            )
        elif decision and decision.location == "retest":
            thesis = "Ретест уровня пробоя после слома структуры — классический профи-вход."
        elif ta.primary_chart_pattern:
            pat = ta.primary_chart_pattern
            mode = getattr(pat, "entry_mode", "") or ""
            psycho = getattr(pat, "psychology_note", "") or ""
            vol_bits = []
            if getattr(pat, "volume_contracted", False):
                vol_bits.append("объём ↓ в фигуре")
            if getattr(pat, "volume_breakout", False):
                vol_bits.append("объём ↑ на пробое")
            thesis = (
                f"Графическая фигура <b>{pat.label_ru}</b> "
                f"({'подтв.' if pat.status == 'confirmed' else 'форм.'})"
            )
            if mode:
                thesis += f" · вход: {mode}"
            thesis += f" + структура подтверждают <b>{label}</b>."
            if psycho:
                thesis += f" {psycho[:90]}"
            if vol_bits:
                thesis += " · " + ", ".join(vol_bits)
            if ta.factor_lines:
                # CVD/liq факты рядом с фигурой
                flow_bit = next(
                    (x for x in ta.factor_lines if "CVD" in x or "liq" in x.lower() or "ликв" in x.lower()),
                    "",
                )
                if flow_bit:
                    thesis += f" · {flow_bit[:50]}"
        else:
            thesis = (
                f"Сканер зафиксировал движение, TA подтверждает <b>{label}</b> "
                f"у сильного уровня. {structure}."
            )
    elif action == "skip":
        headline = f"🚫 <b>НЕ ВХОДИТЬ</b> · {label}"
        thesis = decision.reason if decision else (risks[0] if risks else "сетап слабый")
    else:
        headline = f"👀 <b>WATCH</b> · движение {label} · не входить сейчас"
        thesis = (
            f"Движение есть, но <b>market-вход запрещён</b>. "
            f"{wait_for or 'ждать откат к Fib или ретест'}."
        )

    inv = ""
    if stop:
        inv = (
            f"инвалидация ниже {fmt_price(stop)}"
            if side == "long"
            else f"инвалидация выше {fmt_price(stop)}"
        )

    return TradeThesis(
        side=side,
        action=action,
        confidence=conf,
        headline=headline,
        thesis=thesis,
        structure_line=structure_line,
        wave_line=wave_line,
        fib_line=fib_line,
        entry_price=entry,
        entry_zone=zone,
        stop_price=stop,
        target_prices=targets,
        invalidation=inv,
        wait_for=wait_for,
        arguments=arguments[:4],
        risks=risks[:3],
        fib_action=fib_action,
    )


def format_thesis_hot_html(thesis: TradeThesis, *, skip_headline: bool = False) -> str:
    """Компактный план — без простыней текста."""
    label = "LONG" if thesis.side == "long" else "SHORT"
    lines: list[str] = []

    if thesis.action == "entry":
        if not skip_headline:
            lines.append(f"🎯 <b>{label}</b> · сетап {thesis.confidence}/10")
        if thesis.entry_zone:
            lo, hi = thesis.entry_zone
            lines.append(f"📍 <b>{fmt_price(lo)}</b>–<b>{fmt_price(hi)}</b>")
        elif thesis.entry_price:
            lines.append(f"📍 <b>{fmt_price(thesis.entry_price)}</b>")
        bits: list[str] = []
        if thesis.stop_price:
            bits.append(f"🛑 {fmt_price(thesis.stop_price)}")
        if thesis.target_prices:
            tps = "/".join(fmt_price(t) for t in thesis.target_prices[:3])
            bits.append(f"🎯 {tps}")
        if bits:
            lines.append(" · ".join(bits))
        action_txt = thesis.fib_action or thesis.fib_line
        if action_txt and ("Fib" in action_txt or "Вход" in action_txt):
            lines.append(f"📐 {action_txt[:95]}")
    elif thesis.action == "watch":
        if not skip_headline:
            lines.append(f"👀 <b>WATCH</b> · движение {label} · не входить сейчас")
        if thesis.fib_action:
            lines.append(f"⏳ {thesis.fib_action[:120]}")
        elif thesis.wait_for:
            lines.append(f"⏳ {thesis.wait_for[:110]}")
        chase_watch = (
            thesis.risks
            and any("post-pump" in r or "финал" in r for r in thesis.risks)
        ) or ("погоня" in (thesis.wait_for or "").lower())
        if thesis.target_prices and not chase_watch:
            tps = "/".join(fmt_price(t) for t in thesis.target_prices[:2])
            lines.append(f"🎯 после входа: {tps}")
    else:
        if not skip_headline:
            lines.append(thesis.headline)
        if thesis.fib_action:
            lines.append(f"📐 {thesis.fib_action[:120]}")
        else:
            lines.append(thesis.thesis[:120])
    return "\n".join(lines)


def format_thesis_pro_html(thesis: TradeThesis) -> str:
    """Полный аналитический разбор для «Подробнее»."""
    lines = [
        "📊 <b>Аналитический план</b>",
        thesis.thesis,
        thesis.structure_line,
        thesis.wave_line,
        f"📐 {thesis.fib_line}",
    ]
    if thesis.fib_action and thesis.fib_action not in thesis.fib_line:
        lines.append(f"➡️ <b>Как входить:</b> {thesis.fib_action}")
    if thesis.arguments:
        lines.append("<b>Аргументы:</b>")
        for a in thesis.arguments:
            lines.append(f"  • {a}")
    if thesis.action == "entry":
        if thesis.entry_zone:
            lo, hi = thesis.entry_zone
            lines.append(f"📍 <b>Вход:</b> зона {fmt_price(lo)} – {fmt_price(hi)}")
        elif thesis.entry_price:
            lines.append(f"📍 <b>Вход:</b> {fmt_price(thesis.entry_price)}")
        if thesis.stop_price:
            lines.append(f"🛑 <b>Стоп:</b> {fmt_price(thesis.stop_price)}")
        if thesis.target_prices:
            tps = " / ".join(fmt_price(t) for t in thesis.target_prices[:3])
            lines.append(f"🎯 <b>Цели:</b> {tps}")
        if thesis.invalidation:
            lines.append(f"⛔ {thesis.invalidation}")
    elif thesis.action == "watch":
        if thesis.wait_for:
            lines.append(f"⏳ <b>Ждать:</b> {thesis.wait_for}")
        if thesis.target_prices:
            tps = " / ".join(fmt_price(t) for t in thesis.target_prices[:2])
            lines.append(f"🎯 после входа: {tps}")
    if thesis.risks:
        lines.append("<b>Риски:</b> " + "; ".join(thesis.risks))
    return "\n".join(lines)
