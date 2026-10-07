"""Blow-off top / short squeeze: не шортить на первом sweep, ждать кульминацию.

Пример SOON: ранний шорт после перехая → +35% добой, пиковый объём и short liq на финальном хае,
затем разворот (RSI bear div, OI сброс).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from .bybit_klines import KlineBar
from .market_reading import MarketReading


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[len(s) // 2]


def volume_climax_near_high(
    bars: Sequence[KlineBar],
    *,
    lookback: int = 32,
) -> tuple[bool, str]:
    """Пиковый объём на последних хаях (кульминация, не «объёмов нет»)."""
    if len(bars) < 12:
        return False, ""
    window = list(bars[-min(lookback, len(bars)) :])
    hi_i = max(range(len(window)), key=lambda i: window[i].high)
    vols = [max(0.0, float(b.volume)) for b in window]
    med = _median(vols) or 1.0
    vol_hi = vols[hi_i]
    near_top = hi_i >= len(window) - 5
    if not near_top:
        return False, ""
    if vol_hi >= med * 1.55 and vol_hi >= max(vols) * 0.88:
        return True, f"пиковый объём на хае (~{vol_hi / med:.1f}× медианы окна)"
    return False, ""


def higher_high_rising_volume(
    bars: Sequence[KlineBar],
    swings: Sequence[Any],
) -> tuple[bool, str]:
    """Новый хай на большем объёме — топливо squeeze ещё может добить."""
    highs = [s for s in swings if getattr(s, "kind", "") == "high"]
    if len(highs) < 2 or len(bars) < 10:
        return False, ""
    a, b = highs[-2], highs[-1]
    if b.index >= len(bars) or a.index >= len(bars):
        return False, ""
    if b.price <= a.price * 1.002:
        return False, ""
    vol_a = max(0.0, float(bars[a.index].volume))
    vol_b = max(0.0, float(bars[b.index].volume))
    if vol_a <= 0:
        return False, ""
    if vol_b >= vol_a * 1.12:
        pct = (b.price / a.price - 1.0) * 100.0
        return True, (
            f"хай +{pct:.1f}% на объёме {vol_b / vol_a:.0%} от прошлого пика "
            "— возможен добой short squeeze"
        )
    return False, ""


def _short_liq_climax(liq_context: dict[str, Any] | None) -> tuple[bool, str]:
    if not liq_context:
        return False, ""
    short_usd = float(
        liq_context.get("short_liquidation_usd")
        or liq_context.get("short_usd")
        or liq_context.get("short_vol_usd")
        or 0
    )
    long_usd = float(
        liq_context.get("long_liquidation_usd")
        or liq_context.get("long_usd")
        or liq_context.get("long_vol_usd")
        or 0
    )
    total = float(liq_context.get("total_usd") or short_usd + long_usd or 0)
    if short_usd <= 0 and total <= 0:
        return False, ""
    if short_usd >= max(long_usd * 1.8, 50_000) and short_usd >= total * 0.55:
        return True, "крупные ликвидации шортов на хаях (squeeze climax)"
    return False, ""


def _crowded_long_funding(market_metrics: dict[str, object] | None) -> tuple[bool, str]:
    if not market_metrics:
        return False, ""
    fr = market_metrics.get("funding_rate")
    try:
        rate = float(fr) if fr is not None else 0.0
    except (TypeError, ValueError):
        rate = 0.0
    if rate >= 0.0008:
        return True, f"funding {rate:.3%} — перегретые лонги"
    return False, ""


def _bear_rsi_ready(rsi_div: Any) -> tuple[bool, str]:
    if rsi_div is None or not getattr(rsi_div, "active", False):
        return False, ""
    last = getattr(rsi_div, "last", None)
    if last is None:
        return False, ""
    if getattr(rsi_div, "bias", "") != "short":
        return False, ""
    if float(getattr(last, "strength", 0) or 0) < 0.38:
        return False, ""
    label = str(getattr(last, "label", "") or "Bear div")
    if getattr(last, "is_regular", True):
        return True, f"RSI {label} — разворотный сигнал"
    return False, ""


def apply_blowoff_squeeze_context(
    reading: MarketReading,
    *,
    bars: Sequence[KlineBar],
    swings: Sequence[Any],
    verdict: str,
    conf: int,
    reason: str,
    action_priority: str,
    momentum_pct: float,
    range_position: float,
    drawdown_from_high_pct: float,
    rsi_div: Any = None,
    liq_context: dict[str, Any] | None = None,
    market_metrics: dict[str, object] | None = None,
) -> tuple[MarketReading, str, int, str, str]:
    """
    early_trap: у хая после sweep — не SHORT (ещё могут снять шортов).
    climax: объём/liq/RSI — можно рассматривать SHORT по плану.
    """
    at_highs = range_position >= 0.82 and drawdown_from_high_pct < 4.0
    parabolic = momentum_pct >= 1.8 or (at_highs and momentum_pct >= 0.9)

    climax_vol, vol_note = volume_climax_near_high(bars)
    fuel, fuel_note = higher_high_rising_volume(bars, swings)
    liq_ok, liq_note = _short_liq_climax(liq_context)
    fund_ok, fund_note = _crowded_long_funding(market_metrics)
    rsi_ok, rsi_note = _bear_rsi_ready(rsi_div)

    present = list(reading.present)
    absent = list(reading.absent)
    seek = reading.seek_label
    live = reading.live_scenario
    notes: list[str] = []

    climax = climax_vol or (liq_ok and (rsi_ok or climax_vol))
    early_trap = (
        at_highs
        and parabolic
        and not climax
        and (fuel or fund_ok or (not rsi_ok and not reading.divergence))
    )

    if fuel and fuel_note not in present:
        present.append(fuel_note)
    if fund_ok and fund_note not in present:
        present.append(fund_note)
    if climax_vol and vol_note not in present:
        present.append(vol_note)
    if liq_ok and liq_note not in present:
        present.append(liq_note)
    if rsi_ok and rsi_note not in present:
        present.append(rsi_note)

    new_verdict, new_conf, new_reason, new_pri = verdict, conf, reason, action_priority

    if early_trap:
        trap = (
            "ранний шорт опасен: перехай ≠ финал — возможен добой на short squeeze "
            "(ждать пик объёма + liq шортов + bear RSI)"
        )
        if trap not in absent:
            absent.append("кульминация squeeze ещё не прошла")
        seek = trap if not climax else seek
        live = "exhaustion" if live != "reversal" else live
        if verdict == "SHORT" or action_priority == "short":
            new_verdict = "WAIT"
            new_conf = min(new_conf, 6)
            new_reason = f"{reason} · {trap}" if reason else trap
            notes.append(trap)
    elif climax and (rsi_ok or liq_ok):
        rev = "кульминация на хаях — разворот вниз вероятен после sweep шортов"
        if rev not in present:
            present.append(rev)
        seek = "SHORT только после реакции вниз / close под локальной структурой"
        live = "reversal"
        if verdict == "WAIT" and rsi_ok and action_priority != "long":
            new_pri = "short"
            tip = "blow-off: climax + RSI — bias SHORT, не market в хвосте"
            new_reason = f"{reason} · {tip}" if reason else tip

    draft = replace(
        reading,
        present=present[:10],
        absent=absent[:10],
        seek_label=seek or reading.seek_label,
        live_scenario=live,
    )
    from .market_reading import compose_human_narrative, compose_tf_stack

    draft = replace(
        draft,
        tf_stack=compose_tf_stack(draft),
        narrative=compose_human_narrative(draft),
    )
    return draft, new_verdict, new_conf, new_reason, new_pri
