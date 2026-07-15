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


def _pick_side(signal: Signal, ta: TAAnalysisResult) -> str:
    if ta.verdict == "LONG":
        return "long"
    if ta.verdict == "SHORT":
        return "short"
    if ta.action_priority in {"long", "short"}:
        return ta.action_priority
    return (signal.side or "long").lower()


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

    # Fib hint из wave (уже в entry_zone если confluence)
    if zone:
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

    structure = ta.structure_label or "структура не определена"
    phase = ta.phase_label or ta.phase or ""
    structure_line = f"Структура: <b>{structure}</b>"
    if phase:
        structure_line += f" · {phase}"

    wave_bits: list[str] = []
    if ta.elliott_label:
        wave_bits.append(ta.elliott_label)
    elif ta.wave_phase:
        wave_bits.append(_PHASE_RU.get(ta.wave_phase, ta.wave_phase))
    if ta.abc_label_ru:
        wave_bits.append(ta.abc_label_ru)
    wave_line = "Волны: " + (" · ".join(wave_bits) if wave_bits else "импульс не подтверждён")

    fib_line = _confluence_text(ta)
    fib_zone = _fib_zone_text(ta)
    if fib_zone:
        fib_line = f"{fib_zone} · {fib_line}"

    arguments: list[str] = []
    if ta.oi_narrative_label and ta.oi_narrative_label != "Мало данных OI":
        arguments.append(f"OI: {ta.oi_narrative_label}")
    if ta.momentum_label:
        arguments.append(f"Импульс: {ta.momentum_label} ({ta.momentum_pct:+.1f}%)")
    if ta.smc_summary:
        arguments.append(f"SMC: {ta.smc_summary[:60]}")
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
        if decision and decision.reason:
            wait_for = decision.reason
        elif readiness and not readiness[0]:
            wait_for = readiness[1]
        elif ta.wave_phase in {"late_impulse"}:
            wait_for = "откат к Fib 0.5–0.618 или ретест пробоя"
        elif not ta.wave_has_confluence:
            wait_for = "совпадение Fib с П/С / круглым / ретестом"
        else:
            wait_for = "подтверждение у уровня входа"
        if is_chase_watch:
            wait_for = f"{wait_for} · {_pullback_wait_hint(ta, side)}"
            wait_for = wait_for.replace("вход ≈", "ждать").replace("≈ ≈", "≈")
        elif zone:
            mid = (zone[0] + zone[1]) / 2.0
            wait_for = f"{wait_for} · вход ≈ {fmt_price(mid)}"
        elif side == "long" and ta.breakout_level and not is_chase_watch:
            wait_for = f"{wait_for} · вход ≈ {fmt_price(ta.breakout_level)}"
        elif side == "short" and ta.breakdown_level and not is_chase_watch:
            wait_for = f"{wait_for} · вход ≈ {fmt_price(ta.breakdown_level)}"

    if action == "entry":
        headline = f"🎯 <b>{label}</b> · сетап {conf}/10"
        if ta.abc_phase == "C":
            thesis = (
                f"Коррекция <b>ABC</b> в волне C — зона продолжения тренда "
                f"после импульса. {_confluence_text(ta)}."
            )
        elif ta.wave_phase == "wave_2_4_zone":
            thesis = (
                f"Импульс завершён, цена в <b>золотой зоне Fib</b> (волна 2/4). "
                f"{_confluence_text(ta)} — аргументированный вход."
            )
        elif decision and decision.location == "retest":
            thesis = "Ретест уровня пробоя после слома структуры — классический профи-вход."
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
        inv = f"инвалидация ниже {fmt_price(stop)}" if side == "long" else f"инвалидация выше {fmt_price(stop)}"

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
        why = thesis.wave_line.replace("Волны: ", "") if thesis.wave_line else ""
        if why and "не подтверждён" not in why:
            lines.append(f"📐 {why[:75]}")
    elif thesis.action == "watch":
        if not skip_headline:
            label = "LONG" if thesis.side == "long" else "SHORT"
            lines.append(f"👀 <b>WATCH</b> · движение {label} · не входить сейчас")
        if thesis.wait_for:
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
            tps = " → ".join(fmt_price(t) for t in thesis.target_prices[:4])
            lines.append(f"🎯 <b>Цели:</b> {tps}")
        if thesis.invalidation:
            lines.append(f"❌ {thesis.invalidation}")
    elif thesis.wait_for:
        lines.append(f"⏳ <b>Условие входа:</b> {thesis.wait_for}")
    if thesis.risks:
        lines.append("<b>Риски:</b>")
        for r in thesis.risks:
            lines.append(f"  ⚠️ {r}")
    return "\n".join(lines)
