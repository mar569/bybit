from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .models import Signal
from .settings import ExchangeThresholds, ScannerSettings

PROBABILITY_BYPASS_TYPES = frozenset({
    "vertical_pump",
    "vertical_dump",
})

# Веса факторов (сумма = 1.0)
FACTOR_WEIGHTS: dict[str, float] = {
    "oi_price_sync": 0.20,
    "threshold_excess": 0.16,
    "oi_usd_flow": 0.12,
    "momentum": 0.10,
    "volume": 0.07,
    "trend": 0.07,
    "rsi": 0.06,
    "funding": 0.04,
    "liquidity": 0.04,
    "pattern": 0.08,
    "timing": 0.06,
    "btc": 0.10,
}


@dataclass
class ProbabilityFactor:
    key: str
    label: str
    strength: float
    weight: float
    contribution: float
    detail: str

    @property
    def passed(self) -> bool:
        return self.strength >= 0.55

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "strength": round(self.strength, 3),
            "weight": round(self.weight, 3),
            "contribution": round(self.contribution, 1),
            "passed": self.passed,
            "detail": self.detail,
            "impact": round(self.contribution, 1),
        }


@dataclass
class ProbabilityAssessment:
    percent: float
    verdict: str
    factors: list[ProbabilityFactor] = field(default_factory=list)
    raw_score: float = 0.0

    def top_factors(self, limit: int = 6) -> list[ProbabilityFactor]:
        ranked = sorted(self.factors, key=lambda item: abs(item.contribution), reverse=True)
        return ranked[:limit]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _verdict(percent: float) -> str:
    if percent >= 75:
        return "ВЫСОКАЯ"
    if percent >= 58:
        return "СРЕДНЯЯ"
    return "НИЗКАЯ"


def _ratio_strength(value: float, threshold: float, cap: float = 2.5) -> float:
    if threshold <= 0:
        return 0.0
    return _clamp(abs(value) / threshold, 0.0, cap) / cap


def _rsi_strength(rsi: float | None, is_long: bool) -> float:
    if rsi is None:
        return 0.45
    if is_long:
        if rsi < 25:
            return 0.25
        if rsi <= 55:
            return _clamp(0.4 + (rsi - 25) / 30 * 0.55, 0.0, 1.0)
        if rsi <= 72:
            return _clamp(1.0 - (rsi - 55) / 17 * 0.35, 0.0, 1.0)
        return _clamp(0.65 - (rsi - 72) / 28 * 0.55, 0.0, 1.0)
    if rsi > 75:
        return 0.25
    if rsi >= 45:
        return _clamp(0.4 + (75 - rsi) / 30 * 0.55, 0.0, 1.0)
    if rsi >= 28:
        return _clamp(1.0 - (45 - rsi) / 17 * 0.35, 0.0, 1.0)
    return _clamp(0.65 - (28 - rsi) / 28 * 0.55, 0.0, 1.0)


def _pattern_strength(signal: Signal) -> float:
    base = {
        "vertical_pump": 0.95,
        "vertical_dump": 0.95,
        "mega_pump": 0.88,
        "mega_dump": 0.88,
        "pump": 0.82,
        "dump": 0.82,
        "short_squeeze": 0.78,
        "pulse_pump": 0.62,
        "pulse_dump": 0.62,
        "oi_pump": 0.48,
        "oi_dump": 0.48,
        "price_pump": 0.40,
        "price_dump": 0.40,
    }.get(signal.signal_type, 0.50)
    tier = signal.details.get("flash_tier")
    if tier is not None:
        base = _clamp(base + float(tier) / 100 * 0.15, 0.0, 1.0)
    vel = signal.details.get("velocity_ratio")
    if vel is not None:
        base = _clamp(base + min(float(vel) / 10, 0.12), 0.0, 1.0)
    return base


def assess_signal_probability(
    signal: Signal,
    settings: ScannerSettings,
    thresholds: ExchangeThresholds,
    *,
    btc_change_percent: float | None = None,
    vol_spike: bool = False,
) -> ProbabilityAssessment:
    is_long = signal.side == "long"
    oi = signal.oi_change_percent
    price = signal.price_change_percent or 0.0
    oi_usd = abs(signal.oi_change_usd or 0.0)
    oi_thr = thresholds.oi_rise_percent if is_long else thresholds.oi_drop_percent
    price_thr = thresholds.price_rise_percent if is_long else thresholds.price_drop_percent
    period = max(signal.oi_period_minutes, 1)

    oi_aligned = (oi > 0 and price > 0) if is_long else (oi < 0 and price < 0)
    oi_strength = _ratio_strength(oi, oi_thr)
    price_strength = _ratio_strength(price, price_thr)

    if oi_aligned:
        sync_strength = math.sqrt(max(oi_strength, 0.01) * max(price_strength, 0.01))
    else:
        sync_strength = 0.08 * max(oi_strength, price_strength)

    threshold_strength = math.sqrt(max(oi_strength, 0.0) * max(price_strength, 0.0))

    usd_target = settings.min_oi_change_usd * 2.5
    usd_strength = _clamp(oi_usd / usd_target, 0.0, 1.0) if usd_target > 0 else 0.5
    if oi_usd < settings.min_oi_change_usd * 0.5:
        usd_strength *= 0.4

    speed = float(signal.details.get("price_speed_pct_per_min", 0.0))
    speed_target = max(price_thr / period, 0.05)
    momentum_strength = _clamp(abs(speed) / (speed_target * 2), 0.0, 1.0)

    volume_strength = 0.92 if vol_spike else 0.28

    price_now = signal.current_price
    ema_s, ema_l = signal.ema_short, signal.ema_long
    if price_now and ema_s and ema_l:
        if is_long:
            if price_now >= ema_s >= ema_l:
                gap = (price_now - ema_l) / ema_l * 100 if ema_l else 0
                trend_strength = _clamp(0.55 + gap / 3, 0.0, 1.0)
            elif price_now >= ema_s:
                trend_strength = 0.42
            else:
                trend_strength = 0.18
        elif price_now <= ema_s <= ema_l:
            gap = (ema_l - price_now) / ema_l * 100 if ema_l else 0
            trend_strength = _clamp(0.55 + gap / 3, 0.0, 1.0)
        elif price_now <= ema_s:
            trend_strength = 0.42
        else:
            trend_strength = 0.18
    else:
        trend_strength = 0.40

    rsi_strength = _rsi_strength(signal.rsi, is_long)

    funding = signal.funding_rate
    if funding is None:
        funding_strength = 0.50
    elif is_long:
        funding_strength = _clamp(1.0 - max(funding, 0) / 0.0015, 0.0, 1.0)
    else:
        funding_strength = _clamp(1.0 - max(-funding, 0) / 0.0015, 0.0, 1.0)

    spread = signal.spread
    if spread is not None and price_now and price_now > 0:
        spread_bps = (spread / price_now) * 10_000
        liquidity_strength = _clamp(1.0 - spread_bps / 25, 0.0, 1.0)
    else:
        liquidity_strength = 0.45

    pattern_strength = _pattern_strength(signal)
    timing_strength = _clamp((11 - signal.signal_score) / 10, 0.0, 1.0)

    if btc_change_percent is None:
        btc_strength = 0.48
    elif is_long:
        btc_strength = _clamp(0.5 + btc_change_percent / 0.8, 0.0, 1.0)
    else:
        btc_strength = _clamp(0.5 - btc_change_percent / 0.8, 0.0, 1.0)

    strengths: dict[str, float] = {
        "oi_price_sync": sync_strength,
        "threshold_excess": threshold_strength,
        "oi_usd_flow": usd_strength,
        "momentum": momentum_strength,
        "volume": volume_strength,
        "trend": trend_strength,
        "rsi": rsi_strength,
        "funding": funding_strength,
        "liquidity": liquidity_strength,
        "pattern": pattern_strength,
        "timing": timing_strength,
        "btc": btc_strength,
    }

    weighted = sum(FACTOR_WEIGHTS[k] * strengths[k] for k in FACTOR_WEIGHTS)
    percent = 18.0 + weighted * 72.0

    if not oi_aligned:
        align_penalty = 0.35 + 0.25 * max(oi_strength, price_strength)
        percent *= align_penalty

    if not oi_aligned and abs(oi) >= oi_thr * 1.2 and abs(price) < price_thr * 0.35:
        percent *= 0.55

    percent = _clamp(percent, 11.0, 89.0)

    if signal.signal_type in PROBABILITY_BYPASS_TYPES:
        percent = max(percent, 68.0 + pattern_strength * 12.0)

    factors: list[ProbabilityFactor] = [
        ProbabilityFactor(
            "oi_price_sync",
            "Согласованность OI и цены",
            sync_strength,
            FACTOR_WEIGHTS["oi_price_sync"],
            sync_strength * FACTOR_WEIGHTS["oi_price_sync"] * 72,
            f"OI {oi:+.2f}% | цена {price:+.2f}% | сила {sync_strength:.0%}",
        ),
        ProbabilityFactor(
            "threshold_excess",
            "Превышение порогов",
            threshold_strength,
            FACTOR_WEIGHTS["threshold_excess"],
            threshold_strength * FACTOR_WEIGHTS["threshold_excess"] * 72,
            f"OI×{oi_strength:.0%} цена×{price_strength:.0%} (порог {oi_thr}/{price_thr}%)",
        ),
        ProbabilityFactor(
            "oi_usd_flow",
            "Приток OI в USD",
            usd_strength,
            FACTOR_WEIGHTS["oi_usd_flow"],
            usd_strength * FACTOR_WEIGHTS["oi_usd_flow"] * 72,
            f"{oi_usd:,.0f} $ → {usd_strength:.0%}".replace(",", " "),
        ),
        ProbabilityFactor(
            "momentum",
            "Импульс цены",
            momentum_strength,
            FACTOR_WEIGHTS["momentum"],
            momentum_strength * FACTOR_WEIGHTS["momentum"] * 72,
            f"{speed:+.2f}%/мин (цель ≥{speed_target * 2:.2f})",
        ),
        ProbabilityFactor(
            "volume",
            "Объём",
            volume_strength,
            FACTOR_WEIGHTS["volume"],
            volume_strength * FACTOR_WEIGHTS["volume"] * 72,
            "всплеск" if vol_spike else "обычный поток",
        ),
        ProbabilityFactor(
            "trend",
            "Тренд EMA 9/21",
            trend_strength,
            FACTOR_WEIGHTS["trend"],
            trend_strength * FACTOR_WEIGHTS["trend"] * 72,
            "по тренду" if trend_strength >= 0.55 else "слабый / против",
        ),
        ProbabilityFactor(
            "rsi",
            "RSI",
            rsi_strength,
            FACTOR_WEIGHTS["rsi"],
            rsi_strength * FACTOR_WEIGHTS["rsi"] * 72,
            f"RSI {signal.rsi:.1f} → {rsi_strength:.0%}" if signal.rsi is not None else "нет данных",
        ),
        ProbabilityFactor(
            "funding",
            "Funding",
            funding_strength,
            FACTOR_WEIGHTS["funding"],
            funding_strength * FACTOR_WEIGHTS["funding"] * 72,
            f"{funding:.5f}" if funding is not None else "нейтрально",
        ),
        ProbabilityFactor(
            "liquidity",
            "Ликвидность",
            liquidity_strength,
            FACTOR_WEIGHTS["liquidity"],
            liquidity_strength * FACTOR_WEIGHTS["liquidity"] * 72,
            "спред узкий" if liquidity_strength >= 0.6 else "спред широкий",
        ),
        ProbabilityFactor(
            "pattern",
            f"Паттерн: {signal.signal_type}",
            pattern_strength,
            FACTOR_WEIGHTS["pattern"],
            pattern_strength * FACTOR_WEIGHTS["pattern"] * 72,
            f"качество {pattern_strength:.0%}",
        ),
        ProbabilityFactor(
            "timing",
            "Ранность",
            timing_strength,
            FACTOR_WEIGHTS["timing"],
            timing_strength * FACTOR_WEIGHTS["timing"] * 72,
            f"ранность {signal.signal_score}/10 → {timing_strength:.0%}",
        ),
        ProbabilityFactor(
            "btc",
            "Контекст BTC 5м",
            btc_strength,
            FACTOR_WEIGHTS["btc"],
            btc_strength * FACTOR_WEIGHTS["btc"] * 72,
            f"BTC {btc_change_percent:+.2f}%" if btc_change_percent is not None else "нет данных",
        ),
    ]

    if not oi_aligned and abs(oi) >= oi_thr:
        factors.append(ProbabilityFactor(
            "divergence",
            "Дивергенция OI/цена",
            0.0,
            0.0,
            -8.0,
            "OI сильный, цена почти стоит",
        ))
        percent = _clamp(percent - 8, 11, 89)

    return ProbabilityAssessment(
        percent=round(percent, 1),
        verdict=_verdict(percent),
        factors=factors,
        raw_score=weighted,
    )


def format_probability_block(assessment: ProbabilityAssessment) -> str:
    bar_filled = int(_clamp(assessment.percent / 10, 1, 10))
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    lines = [
        f"🎯 <b>Вероятность: {assessment.percent:.0f}%</b> ({assessment.verdict})",
        f"<code>{bar}</code>",
        "",
        "<b>Факторы (вклад в итог):</b>",
    ]
    for factor in assessment.top_factors(6):
        if factor.key == "divergence":
            lines.append(f"❌ {factor.label}: <i>{factor.detail}</i> ({factor.contribution:.0f}%)")
            continue
        if factor.strength >= 0.65:
            mark = "✅"
        elif factor.strength >= 0.40:
            mark = "⚠️"
        else:
            mark = "❌"
        lines.append(
            f"{mark} {factor.label}: <i>{factor.detail}</i> "
            f"→ <b>+{factor.contribution:.1f}%</b>"
        )
    return "\n".join(lines)


def format_probability_from_signal(signal: Signal) -> str:
    factors_raw = signal.details.get("probability_factors") or []
    factors = [
        ProbabilityFactor(
            key=item.get("key", ""),
            label=item.get("label", ""),
            strength=float(item.get("strength", item.get("passed", 0) and 0.7 or 0.3)),
            weight=float(item.get("weight", 0)),
            contribution=float(item.get("contribution", item.get("impact", 0))),
            detail=str(item.get("detail", "")),
        )
        for item in factors_raw
    ]
    assessment = ProbabilityAssessment(
        percent=float(signal.details.get("probability_percent", 0)),
        verdict=str(signal.details.get("probability_verdict", "")),
        factors=factors,
    )
    return format_probability_block(assessment)
