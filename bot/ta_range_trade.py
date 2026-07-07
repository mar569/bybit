"""Безопасные входы в боковике и усиление вердикта факторами OI / ликвидаций / CVD."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bybit_klines import KlineBar
from .market_structure import MarketStructureContext


@dataclass(frozen=True)
class RangeTradeSetup:
    direction: str
    entry_price: float
    stop_price: float
    targets: list[float]
    label: str
    reason: str
    score_boost: int = 2


@dataclass(frozen=True)
class TaFactorContext:
    cvd_ratio: float
    cvd_detail: str
    liq_detail: str
    liq_long_boost: int
    liq_short_boost: int
    factor_lines: list[str]


@dataclass(frozen=True)
class MarketFlowScores:
    """Сводка потока рынка (OI + CVD + liq) — как матрица CoinGlass, под капотом."""
    continuation: int
    correction: int
    convergence: str  # aligned_up / aligned_down / mixed / weak
    notes: list[str]


def evaluate_market_flow(
    *,
    momentum: str,
    momentum_pct: float,
    phase: str,
    oi_narrative: str,
    oi_context_strength: float,
    cvd_ratio: float,
    liq_long_boost: int,
    liq_short_boost: int,
    range_position: float,
    post_pump: bool,
    drawdown_from_high_pct: float,
) -> MarketFlowScores:
    """
    Матрица «цена + OI + CVD + ликвидации» для сценария коррекция vs продолжение.
    Не требует CoinGlass API — те же сигналы из Bybit OI, liq-трекера и CVD-прокси.
    """
    cont = 42
    corr = 42
    notes: list[str] = []

    # CVD (агрессивный поток)
    if cvd_ratio >= 0.62:
        cont += 16
        notes.append("CVD: агрессивные покупки")
    elif cvd_ratio <= 0.38:
        corr += 16
        notes.append("CVD: агрессивные продажи")
    elif cvd_ratio >= 0.55:
        cont += 6
    elif cvd_ratio <= 0.45:
        corr += 6

    # Ликвидации
    if liq_long_boost > 0:
        cont += 14
        notes.append("смыв шортов (liq)")
    if liq_short_boost > 0:
        corr += 14
        notes.append("смыв лонгов (liq)")

    # OI-нарратив (позиции на бирже)
    oi_map_cont = {
        "aligned_long": 18,
        "squeeze_risk": 12,
        "accumulation": 8,
    }
    oi_map_corr = {
        "aligned_short": 18,
        "shorts_building": 14,
        "long_unwind": 16,
        "capitulation": 10,
    }
    if oi_narrative in oi_map_cont:
        cont += oi_map_cont[oi_narrative]
        notes.append(f"OI: {oi_narrative}")
    if oi_narrative in oi_map_corr:
        corr += oi_map_corr[oi_narrative]
        if oi_narrative not in {"accumulation"}:
            notes.append(f"OI: {oi_narrative}")

    # Конвергенция: импульс + поток в одну сторону
    if momentum == "up" and cvd_ratio >= 0.58 and oi_narrative in {"aligned_long", "squeeze_risk"}:
        cont += 12
        notes.append("цена↑ + CVD↑ + OI поддерживают рост")
    elif momentum == "up" and cvd_ratio <= 0.42:
        corr += 18
        notes.append("цена↑ при CVD↓ — слабый рост / distribution")
    elif momentum == "down" and cvd_ratio <= 0.42 and oi_narrative in {"aligned_short", "shorts_building"}:
        corr += 12
        notes.append("цена↓ + CVD↓ + OI шорты")
    elif momentum == "down" and cvd_ratio >= 0.58:
        cont += 10
        notes.append("цена↓ при CVD↑ — поглощение, возможен отскок")

    if oi_context_strength >= 0.82 and oi_narrative in {"aligned_long", "squeeze_risk", "accumulation"}:
        cont += 6
    elif oi_context_strength >= 0.82 and oi_narrative in {"aligned_short", "shorts_building"}:
        corr += 6

    # Позиция в range / post-pump
    if post_pump or phase == "impulse_up":
        if range_position > 0.82 and drawdown_from_high_pct < 3.0:
            corr += 14
            notes.append("перегрев у хая после импульса")
        elif momentum == "up" and liq_long_boost > 0:
            cont += 8

    cont = min(100, cont)
    corr = min(100, corr)
    spread = cont - corr
    if spread >= 22:
        convergence = "aligned_up"
    elif spread <= -22:
        convergence = "aligned_down"
    elif abs(spread) <= 8:
        convergence = "mixed"
    else:
        convergence = "weak"

    return MarketFlowScores(
        continuation=cont,
        correction=corr,
        convergence=convergence,
        notes=notes,
    )


def compute_cvd_proxy(bars: list[KlineBar], *, lookback: int = 12) -> tuple[float, str]:
    if len(bars) < 3:
        return 0.5, "CVD: мало данных"
    segment = bars[-lookback:] if len(bars) >= lookback else bars
    buy_vol = sum(b.volume for b in segment if b.close >= b.open)
    sell_vol = sum(b.volume for b in segment if b.close < b.open)
    total = buy_vol + sell_vol
    if total <= 0:
        return 0.5, "CVD: нет объёма"
    ratio = buy_vol / total
    if ratio >= 0.62:
        return ratio, f"CVD↑ покупки {ratio:.0%} объёма"
    if ratio <= 0.38:
        return ratio, f"CVD↓ продажи {(1 - ratio):.0%} объёма"
    return ratio, f"CVD нейтр. ({ratio:.0%} buy)"


def parse_liq_context(liq: dict[str, Any] | None) -> TaFactorContext:
    if not liq:
        return TaFactorContext(0.5, "", "", 0, 0, [])
    total = float(liq.get("total_usd") or 0)
    long_liq = float(liq.get("long_liq_usd") or 0)
    short_liq = float(liq.get("short_liq_usd") or 0)
    if total < 10_000:
        return TaFactorContext(0.5, "", "ликв.: мало событий за окно", 0, 0, [])
    detail = f"ликв. ${total/1000:.0f}K (L${long_liq/1000:.0f}K / S${short_liq/1000:.0f}K)"
    long_boost = 0
    short_boost = 0
    lines = [detail]
    if short_liq > long_liq * 1.4:
        long_boost = 1
        lines.append("шорты ликвидированы — топливо для отскока")
    elif long_liq > short_liq * 1.4:
        short_boost = 1
        lines.append("лонги ликвидированы — давление вниз")
    return TaFactorContext(0.5, "", detail, long_boost, short_boost, lines)


def build_factor_context(
    bars: list[KlineBar],
    liq: dict[str, Any] | None,
) -> TaFactorContext:
    cvd_ratio, cvd_detail = compute_cvd_proxy(bars)
    liq_ctx = parse_liq_context(liq)
    lines = []
    if cvd_detail:
        lines.append(cvd_detail)
    lines.extend(liq_ctx.factor_lines)
    return TaFactorContext(
        cvd_ratio=cvd_ratio,
        cvd_detail=cvd_detail,
        liq_detail=liq_ctx.liq_detail,
        liq_long_boost=liq_ctx.liq_long_boost,
        liq_short_boost=liq_ctx.liq_short_boost,
        factor_lines=lines,
    )


def _fp(price: float) -> str:
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.5f}"
    return f"{price:.7g}"


def _range_bounds(
    consolidation: object | None,
    breakout: float | None,
    breakdown: float | None,
    bars: list[KlineBar],
) -> tuple[float, float] | None:
    if consolidation is not None:
        return float(consolidation.bottom), float(consolidation.top)  # type: ignore[attr-defined]
    if breakout and breakdown and breakout > breakdown:
        return breakdown, breakout
    if not bars:
        return None
    seg = bars[-min(36, len(bars)) :]
    return min(b.low for b in seg), max(b.high for b in seg)


def evaluate_range_trade(
    bars: list[KlineBar],
    *,
    consolidation: object | None,
    breakout: float | None,
    breakdown: float | None,
    ms: MarketStructureContext,
    patterns: list[object],
    momentum: str,
    channel: object | None,
    factors: TaFactorContext,
) -> RangeTradeSetup | None:
    bounds = _range_bounds(consolidation, breakout, breakdown, bars)
    if bounds is None or not bars:
        return None
    zone_bottom, zone_top = bounds
    current = bars[-1].close
    if current <= 0 or zone_top <= zone_bottom:
        return None
    width = zone_top - zone_bottom
    range_pct = width / current * 100.0
    if range_pct < 0.6 or range_pct > 12.0:
        return None

    pos = (current - zone_bottom) / width
    max_risk_pct = 2.8

    def _long_setup(stop: float, reason: str, label: str) -> RangeTradeSetup | None:
        risk = (current - stop) / current * 100
        if risk <= 0 or risk > max_risk_pct:
            return None
        targets = [zone_top * 0.998]
        if breakout and breakout > current:
            targets.append(breakout)
        if len(targets) == 1:
            targets.append(zone_top * 1.01)
        return RangeTradeSetup(
            direction="long",
            entry_price=current,
            stop_price=stop,
            targets=targets[:3],
            label=label,
            reason=reason,
            score_boost=2,
        )

    def _short_setup(stop: float, reason: str, label: str) -> RangeTradeSetup | None:
        risk = (stop - current) / current * 100
        if risk <= 0 or risk > max_risk_pct:
            return None
        targets = [zone_bottom * 1.002]
        if breakdown and breakdown < current:
            targets.append(breakdown)
        if len(targets) == 1:
            targets.append(zone_bottom * 0.99)
        return RangeTradeSetup(
            direction="short",
            entry_price=current,
            stop_price=stop,
            targets=targets[:3],
            label=label,
            reason=reason,
            score_boost=2,
        )

    bullish_pat = any(getattr(p, "bullish", None) is True for p in patterns[-2:])
    bearish_pat = any(getattr(p, "bullish", None) is False for p in patterns[-2:])
    oi_long = ms.oi_narrative in {"accumulation", "aligned_long"}
    oi_short = ms.oi_narrative in {"aligned_short", "shorts_building"}
    cvd_long = factors.cvd_ratio >= 0.55
    cvd_short = factors.cvd_ratio <= 0.45

    # От поддержки боковика (нижние 28% range)
    if pos <= 0.28:
        confirms = sum([
            bullish_pat,
            oi_long,
            cvd_long,
            factors.liq_long_boost > 0,
            momentum != "down",
            channel is not None and getattr(channel, "kind", "") == "bull",
        ])
        if confirms >= 2:
            stop = min(zone_bottom * 0.996, current * 0.985)
            return _long_setup(
                stop,
                f"у поддержки боковика ({_fp(zone_bottom)}) · отскок к {_fp(zone_top)}",
                "LONG от поддержки range",
            )

    # От сопротивления (верхние 28%)
    if pos >= 0.72:
        confirms = sum([
            bearish_pat,
            oi_short,
            cvd_short,
            factors.liq_short_boost > 0,
            momentum == "down",
            ms.range_position > 0.72,
            channel is not None and getattr(channel, "kind", "") == "bear",
        ])
        if confirms >= 2:
            stop = max(zone_top * 1.004, current * 1.015)
            return _short_setup(
                stop,
                f"у сопротивления боковика ({_fp(zone_top)}) · откат к {_fp(zone_bottom)}",
                "SHORT от сопротивления range",
            )

    # Почти пробой вверх (EPIC-кейс): <0.8% до resistance + импульс
    if breakout and current >= breakout * 0.992 and momentum in {"up", "flat"}:
        confirms = sum([momentum == "up", cvd_long, oi_long, factors.liq_long_boost > 0, pos >= 0.65])
        if confirms >= 2:
            stop = max(zone_bottom * 0.995, current * 0.985)
            return _long_setup(
                stop,
                f"тест пробоя {_fp(breakout)} · закрепление = long",
                "LONG пробой / тест сопр.",
            )

    # Почти пробой вниз
    if breakdown and current <= breakdown * 1.008 and momentum in {"down", "flat"}:
        confirms = sum([momentum == "down", cvd_short, oi_short, factors.liq_short_boost > 0, pos <= 0.35])
        if confirms >= 2:
            stop = min(zone_top * 1.005, current * 1.015)
            return _short_setup(
                stop,
                f"тест пробоя {_fp(breakdown)} · закрепление = short",
                "SHORT пробой / тест подд.",
            )

    return None
