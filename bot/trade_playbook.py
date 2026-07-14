"""Компактный торговый план (Hot) и полный разбор (Pro) для сигналов."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ta_analysis import (
    TAAnalysisResult,
    fmt_price,
    ta_display_score,
    ta_plain_forecast_line,
    ta_scanner_conflict_line_html,
    ta_signal_caption_html,
    ta_signal_forecast_summary_line,
    format_smc_compact_html,
    entry_readiness_line_html,
    ta_signal_scenario_line_html,
)
from .trade_analyst import (
    TradeThesis,
    build_trade_thesis,
    format_thesis_hot_html,
    format_thesis_pro_html,
)

if TYPE_CHECKING:
    from .models import Signal
    from .trade_decision_gate import TradeDecision

_POST_PUMP_SHORT_TYPES = frozenset({
    "vertical_pump",
    "trend_dump",
    "reversal_dump",
    "impulse_dump",
    "mega_dump",
})


@dataclass(frozen=True)
class TradePlaybook:
    side: str
    entry_price: float | None
    entry_op: str
    stop_price: float | None
    target_prices: list[float]
    logic: str
    aligned: bool


def _pick_side(signal: Signal, ta: TAAnalysisResult) -> str:
    sig_side = (signal.side or "").lower()
    if ta.verdict == "LONG":
        return "long"
    if ta.verdict == "SHORT":
        return "short"
    if (
        ta.post_pump
        and ta.range_position >= 0.72
        and ta.action_priority == "short"
        and signal.signal_type in _POST_PUMP_SHORT_TYPES | {"reversal_pump", "pump", "pulse_pump"}
    ):
        return "short"
    if ta.action_priority in {"long", "short"}:
        return ta.action_priority
    return sig_side if sig_side in {"long", "short"} else "long"


def resolve_trade_playbook(signal: Signal, ta: TAAnalysisResult) -> TradePlaybook | None:
    side = _pick_side(signal, ta)
    is_long = side == "long"
    sig_side = (signal.side or "").lower()
    aligned = (
        (is_long and ta.verdict == "LONG")
        or (not is_long and ta.verdict == "SHORT")
        or (
            is_long
            and sig_side == "long"
            and ta.verdict == "WAIT"
            and ta.action_priority != "short"
        )
        or (
            not is_long
            and sig_side == "short"
            and ta.verdict == "WAIT"
            and ta.action_priority != "long"
        )
    )

    entry = ta.breakout_level if is_long else ta.breakdown_level
    entry_op = "≥" if is_long else "≤"
    st = (signal.signal_type or "").lower()
    chase_type = st in {
        "mega_pump", "mega_dump", "impulse_pump", "impulse_dump",
        "vertical_pump", "vertical_dump", "pulse_pump", "pulse_dump",
    }
    # План позиции: уровень, не «текущая цена» на импульсе
    if ta.entry_zone and ta.wave_has_confluence:
        entry = (ta.entry_zone[0] + ta.entry_zone[1]) / 2.0
        entry_op = "≈"
    elif chase_type and entry is not None:
        entry_op = "≥" if is_long else "≤"
    elif entry is None and ta.current_price and not chase_type:
        entry = ta.current_price
        entry_op = "≈"
    stop = ta.invalidation_price
    targets = [t for t in (ta.target_prices or []) if t > 0][:3]
    if not targets and is_long and ta.breakout_level:
        targets = [ta.breakout_level * 1.015, ta.breakout_level * 1.03]
    elif not targets and not is_long and ta.breakdown_level:
        targets = [ta.breakdown_level * 0.985, ta.breakdown_level * 0.97]

    if not entry and not stop and not targets:
        return None

    logic = _playbook_logic(signal, ta, side)
    return TradePlaybook(
        side=side,
        entry_price=entry,
        entry_op=entry_op,
        stop_price=stop,
        target_prices=targets,
        logic=logic,
        aligned=bool(aligned),
    )


def _playbook_logic(signal: Signal, ta: TAAnalysisResult, side: str) -> str:
    st = signal.signal_type or ""
    if ta.elliott_label and ta.wave_has_confluence:
        bits = [ta.elliott_label]
        if ta.abc_label_ru:
            bits.append(ta.abc_label_ru)
        return " · ".join(bits)[:100]
    if ta.abc_label_ru:
        return ta.abc_label_ru[:90]
    if st in {"trend_dump", "trend_pump"}:
        prior = signal.details.get("trend_prior_pct")
        if prior is not None:
            return f"тренд {prior}% → перегрев → откат"
        return "тренд → перегрев → откат"
    if st in {"vertical_pump", "vertical_dump"}:
        spike = signal.details.get("spike_percent", signal.price_change_percent)
        if spike is not None:
            return f"вертикаль {float(spike):+.1f}% из флета"
        return "вертикальный выход из флета"
    if st in {"impulse_pump", "impulse_dump"}:
        win = signal.details.get("impulse_window_min", signal.oi_period_minutes)
        move = signal.details.get("impulse_move_pct", signal.price_change_percent)
        if move is not None:
            return f"импульс {float(move):+.1f}% за {win}м"
        return f"импульс за {win}м"
    if st in {"mega_pump", "mega_dump"}:
        return f"мега-движение {signal.price_change_percent:+.1f}%"
    if ta.post_pump and side == "short":
        return "памп у хая → шорт от сопротивления"
    if ta.post_pump and side == "long":
        return "пробой после пампа"
    if ta.primary_scenario:
        return ta.primary_scenario[:80]
    if ta.narrative_plain:
        plain = ta.narrative_plain.replace("<b>", "").replace("</b>", "")[:80]
        return plain
    return "OI + цена · структура"


def _risk_pct(entry: float | None, stop: float | None) -> str | None:
    if not entry or not stop or entry <= 0:
        return None
    pct = abs(entry - stop) / entry * 100.0
    return f"{pct:.1f}%"


def format_playbook_html(
    pb: TradePlaybook,
    *,
    readiness: tuple[bool, str] | None = None,
    minimal: bool = False,
) -> str:
    label = "LONG" if pb.side == "long" else "SHORT"
    emoji = "🟢" if pb.side == "long" else "🔴"
    lines = [f"{emoji} <b>{label}</b>"]
    if readiness and readiness[0]:
        lines.append("✅ <b>Готов</b> по плану")
    elif readiness and not readiness[0] and readiness[1]:
        if minimal and pb.entry_price:
            lines.append(
                f"🔶 <b>Ждать</b> · {pb.entry_op} <b>{fmt_price(pb.entry_price)}</b>"
            )
        else:
            lines.append(f"🔶 <b>Ждать</b> · {readiness[1][:70]}")
    elif not pb.aligned:
        lines.append("⚠️ <b>Подтвердите</b> уровень на графике")

    if pb.entry_price:
        lines.append(f"📍 Вход: {pb.entry_op} <b>{fmt_price(pb.entry_price)}</b>")
    if pb.stop_price:
        risk = _risk_pct(pb.entry_price, pb.stop_price)
        risk_s = f" (риск ~{risk})" if risk else ""
        lines.append(f"🛑 Стоп: <b>{fmt_price(pb.stop_price)}</b>{risk_s}")
    if pb.target_prices:
        tps = " / ".join(fmt_price(t) for t in pb.target_prices[:3])
        lines.append(f"🎯 Цели: <b>{tps}</b>")
    if pb.logic and not minimal:
        lines.append(f"📐 {pb.logic}")
    return "\n".join(lines)


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(text: str) -> str:
    return _HTML_TAG_RE.sub("", text or "").strip()


def _already_covered(line: str, body: str, *, min_len: int = 28) -> bool:
    plain = _plain_text(line)
    if len(plain) < min_len:
        return False
    return plain in _plain_text(body)


def _append_unique(lines: list[str], body: str, block: str) -> str:
    block = (block or "").strip()
    if not block or _already_covered(block, body):
        return body
    lines.append(block)
    return f"{body}\n{block}" if body else block


def build_hot_caption(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    header: str,
    readiness: tuple[bool, str] | None = None,
    quality_html: str = "",
    quality_tier: str | None = None,
    decision: TradeDecision | None = None,
) -> str:
    """Короткий caption: профи-тезис + план входа."""
    thesis = build_trade_thesis(
        signal, ta, decision=decision, readiness=readiness,
    )
    parts = [header.strip()]
    if quality_html:
        parts.append(quality_html.strip())
    parts.append(format_thesis_hot_html(thesis, skip_headline=bool(quality_tier)))
    # Fallback playbook если нет уровней в тезисе
    if thesis.action == "entry" and not thesis.entry_price and not thesis.entry_zone:
        pb = resolve_trade_playbook(signal, ta)
        if pb:
            parts.append(format_playbook_html(pb, readiness=readiness, minimal=True))

    text = "\n\n".join(p for p in parts if p)
    if len(text) > 980:
        text = text[:977] + "…"
    return text


def build_pro_detail_html(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    readiness: tuple[bool, str] | None = None,
    quality_html: str = "",
    decision: TradeDecision | None = None,
) -> str:
    """Полный разбор: аналитический план + факторы."""
    exchange = signal.exchange
    sym = signal.symbol
    score = ta_display_score(ta)
    lines = [
        f"📖 <b>Подробный разбор</b> · <b>{sym}</b> · {exchange}",
        f"Тип: <code>{signal.signal_type}</code> · TA {score}/10 · "
        f"OI {signal.oi_change_percent:+.2f}% · цена {signal.price_change_percent or 0:+.2f}%",
    ]
    body = "\n".join(lines)
    if quality_html:
        body = _append_unique(lines, body, quality_html)

    thesis = build_trade_thesis(
        signal, ta, decision=decision, readiness=readiness,
    )
    body = _append_unique(lines, body, format_thesis_pro_html(thesis))

    conflict = ta_scanner_conflict_line_html(ta, signal.side)
    body = _append_unique(lines, body, conflict)

    pb = resolve_trade_playbook(signal, ta)
    if pb:
        body = _append_unique(lines, body, format_playbook_html(pb, readiness=readiness))

    plain = ta_plain_forecast_line(ta)
    body = _append_unique(lines, body, plain)

    basis = ta_signal_forecast_summary_line(ta)
    body = _append_unique(lines, body, basis)

    if ta.smc and ta.smc.smc_score >= 4:
        smc = format_smc_compact_html(ta.smc)
        body = _append_unique(lines, body, smc or "")

    if not pb:
        verbose = ta_signal_caption_html(
            ta,
            signal_side=signal.side,
            readiness=readiness,
            show_readiness_badge=True,
            compact=False,
            signal_type=signal.signal_type,
        )
        body = _append_unique(lines, body, verbose)

    prob = signal.details.get("probability_percent")
    if prob:
        lines.append(f"🎯 Вероятность сканера: <b>{float(prob):.0f}%</b>")
    trade_dec = signal.details.get("trade_decision_reason")
    if trade_dec:
        lines.append(f"⚖️ Арбитр: <i>{trade_dec}</i>")
    lines.append(
        "📈 <b>График:</b> Fib / уровни / ABC — разметка на картинке."
    )
    return "\n".join(lines)
