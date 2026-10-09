"""Последние свечи словами — «живой» комментарий как на TV."""
from __future__ import annotations

from .bybit_klines import KlineBar
from .ta_analysis import fmt_price


def _body(b: KlineBar) -> float:
    return abs(float(b.close) - float(b.open))


def _upper_wick(b: KlineBar) -> float:
    top = max(float(b.open), float(b.close))
    return max(0.0, float(b.high) - top)


def _lower_wick(b: KlineBar) -> float:
    bot = min(float(b.open), float(b.close))
    return max(0.0, bot - float(b.low))


def _bar_word(b: KlineBar, *, avg_body: float) -> str:
    bull = float(b.close) >= float(b.open)
    body = _body(b)
    big = body >= avg_body * 1.35 if avg_body > 0 else body > 0
    uw, lw = _upper_wick(b), _lower_wick(b)
    reject_up = uw > max(body * 1.6, avg_body * 0.5)
    reject_dn = lw > max(body * 1.6, avg_body * 0.5)
    if reject_up and bull:
        return "зелёная с длинной верхней тенью (отказ у хая)"
    if reject_up and not bull:
        return "красная после отказа сверху"
    if reject_dn and not bull:
        return "красная с длинным нижним хвостом (покупатели подставили)"
    if reject_dn and bull:
        return "зелёная с отскоком от низа"
    if big and bull:
        return "сильная зелёная"
    if big and not bull:
        return "сильная красная"
    if bull:
        return "небольшая зелёная"
    return "небольшая красная"


def describe_recent_bars(bars: list[KlineBar], *, count: int = 4) -> str:
    from .chart_display_policy import ed_telegram_candle_narrative_enabled

    if not ed_telegram_candle_narrative_enabled():
        return ""
    if not bars or len(bars) < 2:
        return ""
    n = max(2, min(int(count), 6, len(bars)))
    seg = bars[-n:]
    bodies = [_body(b) for b in seg]
    avg_body = sum(bodies) / len(bodies) if bodies else 0.0
    words = [_bar_word(b, avg_body=avg_body) for b in seg]
    if len(words) == 2:
        line = f"Последние свечи: {words[0]} → {words[1]}."
    else:
        line = "Последние свечи: " + " → ".join(words[:-1]) + f" → сейчас: {words[-1]}."
    last = seg[-1]
    line += f" Close {fmt_price(float(last.close))}."
    return line
