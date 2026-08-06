"""Единый гейт сигналов UKOUSD: тренд + MACD первыми, новости только подтверждают.

Правило: нет LONG/SHORT против импульса и MACD.
Deal-tape / Ормуз-слух сам по себе НЕ даёт SHORT.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class OilSignalGate:
    """Итог фильтра перед любым LONG/SHORT в чат."""

    allow_long: bool
    allow_short: bool
    trend: str  # up | down | range
    macd_bias: str  # bull | bear | neutral | none
    move_30m_pct: float
    move_60m_pct: float
    reason_ru: str
    factors_ru: tuple[str, ...]

    @property
    def force_wait(self) -> bool:
        return not self.allow_long and not self.allow_short


def _closes(bars: Sequence[Any] | None) -> list[float]:
    out: list[float] = []
    for b in list(bars or []):
        try:
            c = float(getattr(b, "close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out.append(c)
    return out


def detect_price_trend(
    bars: Sequence[Any] | None,
    *,
    lookback: int = 18,
) -> tuple[str, float, str]:
    """Грубый тренд по close: up / down / range + % за lookback."""
    closes = _closes(bars)
    if len(closes) < max(8, lookback // 2):
        return "range", 0.0, "мало баров для тренда"
    window = closes[-lookback:] if len(closes) >= lookback else closes
    a, b = window[0], window[-1]
    if a <= 0:
        return "range", 0.0, "нет цены"
    chg = (b - a) / a * 100.0
    # Структура: последние 3 swing-прокси
    third = max(3, len(window) // 3)
    p1 = sum(window[:third]) / third
    p2 = sum(window[third : 2 * third]) / max(1, len(window[third : 2 * third]))
    p3 = sum(window[2 * third :]) / max(1, len(window[2 * third :]))
    rising = p3 > p2 > p1 and chg >= 0.20
    falling = p3 < p2 < p1 and chg <= -0.20
    if rising or chg >= 0.55:
        return "up", chg, f"тренд↑ {chg:+.2f}% / {len(window)}бар"
    if falling or chg <= -0.55:
        return "down", chg, f"тренд↓ {chg:+.2f}% / {len(window)}бар"
    return "range", chg, f"range {chg:+.2f}%"


def evaluate_oil_signal_gate(
    bars: Sequence[Any] | None,
    *,
    interval_minutes: int = 5,
    proposed_side: str | None = None,
) -> OilSignalGate:
    """Разрешить LONG/SHORT только с трендом и MACD.

    proposed_side — если задан, reason заточен под него.
    """
    from .oil_entry_filters import measure_recent_move
    from .oil_macd import compute_oil_macd

    factors: list[str] = []
    trend, trend_pct, trend_ru = detect_price_trend(bars)
    factors.append(trend_ru)

    move_30 = move_60 = 0.0
    move = measure_recent_move(
        bars,
        interval_minutes=interval_minutes,
        priced_in_30m_pct=0.20,
        priced_in_60m_pct=0.35,
    )
    if move is not None:
        move_30 = float(move.move_30m_pct)
        move_60 = float(move.move_60m_pct)
        factors.append(f"ход 30м {move_30:+.2f}% · 60м {move_60:+.2f}%")

    macd = compute_oil_macd(bars)
    macd_bias = macd.bias if macd is not None else "none"
    if macd is not None:
        factors.append(macd.line_ru)

    # Импульс = жёсткий блок против стороны
    impulse_up = move_30 >= 0.20 or move_60 >= 0.35 or trend == "up"
    impulse_down = move_30 <= -0.20 or move_60 <= -0.35 or trend == "down"

    allow_long = True
    allow_short = True
    blocks: list[str] = []

    if impulse_up:
        allow_short = False
        blocks.append("рост/тренд↑ — SHORT закрыт")
    if impulse_down:
        allow_long = False
        blocks.append("падение/тренд↓ — LONG закрыт")

    if macd_bias == "bull":
        allow_short = False
        if "MACD" not in " ".join(blocks):
            blocks.append("MACD↑ — SHORT закрыт")
    elif macd_bias == "bear":
        allow_long = False
        if "MACD" not in " ".join(blocks):
            blocks.append("MACD↓ — LONG закрыт")

    # Согласование: LONG только если не против MACD/тренда
    if trend == "down" and macd_bias == "bear":
        allow_long = False
    if trend == "up" and macd_bias == "bull":
        allow_short = False

    # Range без MACD-пересечения — обе стороны слабые (не market)
    if trend == "range" and macd_bias == "neutral":
        # разрешаем только от уровня позже; здесь не режем наглухо
        factors.append("range+MACD flat — только от уровня")

    reason = " · ".join(blocks) if blocks else "тренд и MACD не блокируют"
    if proposed_side:
        ps = proposed_side.upper()
        if ps == "SHORT" and not allow_short:
            reason = blocks[0] if blocks else "SHORT запрещён гейтом"
        elif ps == "LONG" and not allow_long:
            reason = blocks[0] if blocks else "LONG запрещён гейтом"

    return OilSignalGate(
        allow_long=allow_long,
        allow_short=allow_short,
        trend=trend,
        macd_bias=macd_bias,
        move_30m_pct=move_30,
        move_60m_pct=move_60,
        reason_ru=reason[:180],
        factors_ru=tuple(factors[:6]),
    )


def gate_apply_to_side(
    gate: OilSignalGate,
    side: str,
) -> str:
    """LONG/SHORT/WAIT → WAIT если гейт закрыл сторону."""
    s = (side or "WAIT").upper()
    if s == "LONG" and not gate.allow_long:
        return "WAIT"
    if s == "SHORT" and not gate.allow_short:
        return "WAIT"
    return s


def news_may_confirm_side(
    *,
    news_bias: str,
    news_for_entry: bool,
    gate: OilSignalGate,
) -> str | None:
    """Новость может подтвердить сторону, но не открыть против гейта.

    Returns: LONG | SHORT | None
    """
    if not news_for_entry:
        return None
    nb = (news_bias or "").lower()
    if nb == "bullish" and gate.allow_long:
        return "LONG"
    if nb == "bearish" and gate.allow_short:
        return "SHORT"
    return None
