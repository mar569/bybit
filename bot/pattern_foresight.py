"""BuyHold-style foresight 1–3ч: формирующиеся фигуры → путь цены → триггер/цель/отмена."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .pattern_specs import (
    MAX_REPORT_PATTERNS,
    MIN_FORMING_REPORT_CONFIDENCE,
    PATTERN_LABELS_RU,
)

if TYPE_CHECKING:
    from .chart_pattern_models import ChartPattern


def _fmt_price(price: float) -> str:
    """Локальный формат цены — без импорта ta_analysis (цикл)."""
    ax = abs(float(price))
    if ax >= 1000:
        return f"{price:.2f}"
    if ax >= 1:
        return f"{price:.4g}"
    return f"{price:.6g}"


# Горизонт по типу фигуры (часы) — статья: вымпел быстрый, треугольник/клин дольше
_HORIZON_BY_KIND: dict[str, float] = {
    "pennant": 1.0,
    "flag": 1.5,
    "false_breakout": 1.0,
    "one_two_three": 1.5,
    "double_top": 2.0,
    "double_bottom": 2.0,
    "triple_top": 2.5,
    "triple_bottom": 2.5,
    "head_shoulders": 2.5,
    "inverse_head_shoulders": 2.5,
    "baskerville_bullish": 2.0,
    "baskerville_bearish": 2.0,
    "three_indians": 2.0,
    "triangle_symmetric": 2.5,
    "triangle_ascending": 2.5,
    "triangle_descending": 2.5,
    "wedge_rising": 2.5,
    "wedge_falling": 2.5,
    "expanding_triangle": 2.5,
    "rectangle": 3.0,
    "diamond": 3.0,
    "cup_handle": 3.0,
    "inverse_cup_handle": 3.0,
    "rounded_bottom": 3.0,
    "rounded_top": 3.0,
}

_SUBTYPE_RU = {
    "continuation": "продолжение",
    "reversal": "разворот",
}


@dataclass(frozen=True)
class PatternForesight:
    """Что ждать от монеты на 1–3 часа по графическим фигурам (BuyHold)."""

    horizon_hours: float
    bias: str  # long | short | neutral
    status: str  # forming | awaiting_breakout | confirmed | conflict | none
    summary: str
    path_text: str
    trigger_text: str
    target_text: str
    invalidation_text: str
    ltf_label: str = ""
    htf_label: str = ""
    htf_conflict: bool = False
    watch_only: bool = False
    confidence: float = 0.0
    primary_kind: str = ""

    @property
    def active(self) -> bool:
        return self.status not in {"", "none"} and bool(self.summary)

    def hot_line(self) -> str:
        if not self.active:
            return ""
        h = f"{self.horizon_hours:.0f}ч" if self.horizon_hours >= 1 else "1ч"
        bits = [f"📐 {h}: {self.summary}"]
        if self.trigger_text and self.watch_only:
            bits.append(f"ждать {self.trigger_text}")
        elif self.path_text:
            bits.append(self.path_text[:70])
        return " · ".join(bits)[:160]

    def pro_html(self) -> str:
        if not self.active:
            return ""
        h = f"~{self.horizon_hours:.0f}ч"
        lines = [
            f"📐 <b>Фигуры · foresight {h}</b>",
            f"• {self.summary}",
        ]
        if self.path_text:
            lines.append(f"• Путь: {self.path_text}")
        if self.trigger_text:
            lines.append(f"• Триггер: {self.trigger_text}")
        if self.target_text:
            lines.append(f"• Цель: {self.target_text}")
        if self.invalidation_text:
            lines.append(f"• Отмена: {self.invalidation_text}")
        if self.htf_label:
            conflict = " ⚠️ конфликт с LTF" if self.htf_conflict else ""
            lines.append(f"• HTF 1h: {self.htf_label}{conflict}")
        if self.watch_only:
            lines.append("• Режим: <b>WATCH</b> — фигура ещё не подтверждена пробоем")
        return "\n".join(lines)


def tag_patterns_timeframe(
    patterns: list["ChartPattern"],
    timeframe: str,
) -> list["ChartPattern"]:
    return [replace(p, timeframe=timeframe) for p in patterns]


def estimate_horizon_hours(
    pattern: "ChartPattern | None",
    *,
    atr: float = 0.0,
    interval_minutes: int = 5,
) -> float:
    """Оценка горизонта отработки: тип фигуры + высота/ATR, clamp 1–3ч."""
    if pattern is None:
        return 2.0
    base = _HORIZON_BY_KIND.get(pattern.kind, 2.0)
    # Уточнение по размеру: большая фигура → ближе к 3ч
    height = float(pattern.pole_height or 0)
    if height <= 0 and pattern.zone_top and pattern.zone_bottom:
        height = abs(float(pattern.zone_top) - float(pattern.zone_bottom))
    if atr > 0 and height > 0:
        bars_est = height / atr
        hours_from_size = bars_est * max(1, interval_minutes) / 60.0
        # смесь базы и размера
        base = 0.55 * base + 0.45 * hours_from_size
    if pattern.status == "forming":
        base = min(3.0, base + 0.5)  # ещё ждать пробой
    return float(max(1.0, min(3.0, round(base * 2) / 2)))  # шаг 0.5


def _dir_to_bias(direction: str) -> str:
    if direction == "bullish":
        return "long"
    if direction == "bearish":
        return "short"
    return "neutral"


def _status_label(pattern: "ChartPattern") -> str:
    if pattern.status == "confirmed":
        return "подтв."
    return "форм."


def _subtype_ru(pattern: "ChartPattern") -> str:
    return _SUBTYPE_RU.get(pattern.subtype, pattern.subtype or "")


def _build_trigger(pattern: "ChartPattern") -> str:
    mode = (pattern.entry_mode or "").strip()
    kind = getattr(pattern, "kind", "") or ""
    if pattern.status == "forming" or mode == "wait":
        if kind == "expanding_triangle" and pattern.neckline:
            lvl = pattern.neckline.end_price
            if pattern.direction == "bearish":
                return (
                    f"импульсный пробой {_fmt_price(lvl)} вниз + ретест "
                    f"(расходящийся △ у пика)"
                )
            return (
                f"импульсный пробой {_fmt_price(lvl)} вверх + ретест "
                f"(расходящийся △ у дна)"
            )
        if pattern.neckline:
            lvl = pattern.neckline.end_price
            side = "вверх" if pattern.direction == "bullish" else "вниз"
            return f"пробой {_fmt_price(lvl)} {side} + закрепление"
        if pattern.zone_top and pattern.direction == "bullish":
            return f"пробой {_fmt_price(pattern.zone_top)} вверх"
        if pattern.zone_bottom and pattern.direction == "bearish":
            return f"пробой {_fmt_price(pattern.zone_bottom)} вниз"
        return "дождаться пробоя границы фигуры"
    if mode == "retest":
        if kind == "expanding_triangle" and pattern.neckline:
            return (
                f"ретест пробитой границы ~{_fmt_price(pattern.neckline.end_price)} "
                f"— вход по BuyHold"
            )
        return "ретест пробитой границы"
    if mode == "breakout":
        if kind == "expanding_triangle":
            return "импульсный пробой есть — лучше дождаться ретеста границы"
        return "уже пробой — вход от границы / по рынку осторожно"
    return mode or "подтверждённый пробой"


def _build_target(pattern: "ChartPattern") -> str:
    if pattern.target_price:
        return f"{_fmt_price(pattern.target_price)} (высота фигуры / шток)"
    return ""


def _build_invalidation(pattern: "ChartPattern") -> str:
    if pattern.stop_price:
        if pattern.direction == "bullish":
            return f"закрытие ниже {_fmt_price(pattern.stop_price)}"
        if pattern.direction == "bearish":
            return f"закрытие выше {_fmt_price(pattern.stop_price)}"
        return f"слом стоп-уровня {_fmt_price(pattern.stop_price)}"
    if pattern.zone_bottom and pattern.direction == "bullish":
        return f"слом дна фигуры {_fmt_price(pattern.zone_bottom)}"
    if pattern.zone_top and pattern.direction == "bearish":
        return f"слом верха фигуры {_fmt_price(pattern.zone_top)}"
    return "слом противоположной границы фигуры"


def _build_path(pattern: "ChartPattern", horizon: float) -> str:
    sub = _subtype_ru(pattern)
    label = pattern.label_ru or PATTERN_LABELS_RU.get(pattern.kind, pattern.kind)
    psycho = (pattern.psychology_note or "").strip()
    if pattern.status == "forming":
        base = (
            f"вырисовывается {label} ({sub}) — на ~{horizon:.0f}ч ждать пробой "
            f"и отработку как {_dir_to_bias(pattern.direction).upper() or 'нейтраль'}"
        )
    else:
        bias = _dir_to_bias(pattern.direction).upper()
        base = f"{label} подтверждён → путь {bias} к цели на ~{horizon:.0f}ч"
    if psycho:
        base = f"{base}. {psycho[:80]}"
    vol = []
    if pattern.volume_contracted:
        vol.append("объём сжат")
    if pattern.volume_breakout:
        vol.append("объём на пробое ↑")
    if vol:
        base = f"{base} ({', '.join(vol)})"
    return base


def _pick_watch_candidates(
    patterns: list["ChartPattern"],
) -> list["ChartPattern"]:
    """Forming с conf ≥ порога — для WATCH-нарратива (не ENTRY)."""
    out = [
        p
        for p in patterns
        if p.status == "forming" and p.confidence >= MIN_FORMING_REPORT_CONFIDENCE
    ]
    out.sort(key=lambda p: p.confidence, reverse=True)
    return out[:MAX_REPORT_PATTERNS]


def build_pattern_foresight(
    ltf_patterns: list["ChartPattern"],
    *,
    htf_patterns: list["ChartPattern"] | None = None,
    primary: "ChartPattern | None" = None,
    atr: float = 0.0,
    interval_minutes: int = 5,
    current_price: float = 0.0,
) -> PatternForesight:
    """Собрать foresight 1–3ч из LTF (+ HTF) фигур по логике BuyHold."""
    htf_patterns = htf_patterns or []
    primary = primary or (ltf_patterns[0] if ltf_patterns else None)
    # Если primary confirmed слабый — подтянуть лучший forming для WATCH-текста
    forming = _pick_watch_candidates(ltf_patterns)
    focus = primary
    if focus is None and forming:
        focus = forming[0]
    elif (
        focus is not None
        and focus.status == "confirmed"
        and focus.confidence < 0.72
        and forming
        and forming[0].confidence > focus.confidence
    ):
        # оставляем confirmed для gate, но path может усилить forming — берём primary
        pass

    if focus is None and not htf_patterns:
        return PatternForesight(
            horizon_hours=2.0,
            bias="neutral",
            status="none",
            summary="",
            path_text="",
            trigger_text="",
            target_text="",
            invalidation_text="",
        )

    htf_primary = None
    if htf_patterns:
        from .chart_patterns import pick_primary_pattern

        htf_primary = pick_primary_pattern(htf_patterns)

    horizon = estimate_horizon_hours(
        focus, atr=atr, interval_minutes=interval_minutes,
    )
    bias = _dir_to_bias(focus.direction) if focus else "neutral"
    htf_bias = _dir_to_bias(htf_primary.direction) if htf_primary else "neutral"
    htf_conflict = bool(
        focus
        and htf_primary
        and bias in {"long", "short"}
        and htf_bias in {"long", "short"}
        and bias != htf_bias
    )

    if htf_conflict:
        status = "conflict"
        watch_only = True
        # Статья: старший ТФ приоритетнее — bias смещаем к HTF, но WATCH
        bias = htf_bias
    elif focus and focus.status == "forming":
        status = "forming" if not focus.entry_mode or focus.entry_mode == "wait" else "awaiting_breakout"
        watch_only = True
    elif focus:
        status = "confirmed"
        watch_only = False
    else:
        # только HTF
        focus = htf_primary
        status = focus.status if focus else "none"
        bias = htf_bias
        watch_only = status != "confirmed"
        horizon = estimate_horizon_hours(
            focus, atr=atr, interval_minutes=max(60, interval_minutes),
        )

    assert focus is not None
    sub = _subtype_ru(focus)
    summary = (
        f"{focus.label_ru} ({_status_label(focus)}"
        + (f", {sub}" if sub else "")
        + f") → {bias.upper() if bias != 'neutral' else 'WAIT'}"
    )
    if htf_conflict and htf_primary:
        summary = (
            f"LTF {focus.label_ru} vs HTF {htf_primary.label_ru} — "
            f"приоритет HTF → {bias.upper()}, без market"
        )

    path = _build_path(focus, horizon)
    if htf_conflict and htf_primary:
        path = (
            f"на LTF {focus.label_ru}, на 1h {htf_primary.label_ru} — "
            f"не бить против HTF; ждать согласования или отработки HTF"
        )

    # Доп. forming рядом
    extra_forming = [
        p for p in forming if p is not focus and p.kind != focus.kind
    ][:1]
    if extra_forming and not htf_conflict:
        ef = extra_forming[0]
        path = f"{path}; также форм. {ef.label_ru}"

    ltf_label = f"{focus.label_ru} ({_status_label(focus)})"
    htf_label = ""
    if htf_primary:
        htf_label = f"{htf_primary.label_ru} ({_status_label(htf_primary)})"

    # Если confirmed далеко от цели — всё равно foresight
    _ = current_price  # reserved for dist-to-target refinements

    return PatternForesight(
        horizon_hours=horizon,
        bias=bias,
        status=status,
        summary=summary,
        path_text=path,
        trigger_text=_build_trigger(focus),
        target_text=_build_target(focus),
        invalidation_text=_build_invalidation(focus),
        ltf_label=ltf_label,
        htf_label=htf_label,
        htf_conflict=htf_conflict,
        watch_only=watch_only,
        confidence=float(focus.confidence),
        primary_kind=focus.kind,
    )


def foresight_enriches_scenario(foresight: PatternForesight) -> str:
    """Короткая строка для primary_scenario / forecast."""
    if not foresight.active:
        return ""
    parts = [foresight.summary]
    if foresight.target_text:
        parts.append(f"цель {foresight.target_text.split('(')[0].strip()}")
    return " — ".join(parts)[:120]


def foresight_to_dict(foresight: PatternForesight | None) -> dict | None:
    if foresight is None or not foresight.active:
        return None
    return {
        "horizon_hours": foresight.horizon_hours,
        "bias": foresight.bias,
        "status": foresight.status,
        "summary": foresight.summary,
        "path": foresight.path_text,
        "trigger": foresight.trigger_text,
        "target": foresight.target_text,
        "invalidation": foresight.invalidation_text,
        "ltf": foresight.ltf_label,
        "htf": foresight.htf_label,
        "htf_conflict": foresight.htf_conflict,
        "watch_only": foresight.watch_only,
        "confidence": round(foresight.confidence, 3),
        "kind": foresight.primary_kind,
    }
