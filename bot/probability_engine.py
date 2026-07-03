from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Signal
from .settings import ExchangeThresholds, ScannerSettings

PROBABILITY_BYPASS_TYPES = frozenset({
    "vertical_pump",
    "vertical_dump",
})


@dataclass
class ProbabilityFactor:
    key: str
    label: str
    impact: float
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "impact": round(self.impact, 1),
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class ProbabilityAssessment:
    percent: float
    verdict: str
    factors: list[ProbabilityFactor] = field(default_factory=list)
    raw_score: float = 0.0

    def top_factors(self, limit: int = 5) -> list[ProbabilityFactor]:
        ranked = sorted(self.factors, key=lambda item: abs(item.impact), reverse=True)
        return ranked[:limit]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _verdict(percent: float) -> str:
    if percent >= 75:
        return "ВЫСОКАЯ"
    if percent >= 55:
        return "СРЕДНЯЯ"
    return "НИЗКАЯ"


def _add(
    factors: list[ProbabilityFactor],
    key: str,
    label: str,
    impact: float,
    passed: bool,
    detail: str,
) -> float:
    factors.append(ProbabilityFactor(key, label, impact, passed, detail))
    return impact if passed else min(impact, 0.0)


def assess_signal_probability(
    signal: Signal,
    settings: ScannerSettings,
    thresholds: ExchangeThresholds,
    *,
    btc_change_percent: float | None = None,
    vol_spike: bool = False,
) -> ProbabilityAssessment:
    factors: list[ProbabilityFactor] = []
    score = 38.0

    is_long = signal.side == "long"
    oi = signal.oi_change_percent
    price = signal.price_change_percent or 0.0
    oi_usd = signal.oi_change_usd or 0.0

    oi_aligned = (oi > 0 and price > 0) if is_long else (oi < 0 and price < 0)
    score += _add(
        factors,
        "oi_price_align",
        "OI и цена в одну сторону",
        20.0 if oi_aligned else -28.0,
        oi_aligned,
        f"OI {oi:+.2f}% | цена {price:+.2f}%",
    )

    oi_thr = thresholds.oi_rise_percent if is_long else thresholds.oi_drop_percent
    price_thr = thresholds.price_rise_percent if is_long else thresholds.price_drop_percent
    oi_ok = abs(oi) >= oi_thr
    price_ok = (price >= price_thr) if is_long else (price <= -price_thr)
    dual = oi_ok and price_ok
    score += _add(
        factors,
        "dual_threshold",
        "Пороги OI и цены",
        16.0,
        dual,
        f"нужно OI≥{oi_thr}% и цена≥{price_thr}%"
        if is_long
        else f"нужно OI≥{oi_thr}% и цена≥{price_thr}%",
    )

    usd_strong = abs(oi_usd) >= settings.min_oi_change_usd * 1.5
    score += _add(
        factors,
        "oi_usd_flow",
        "Приток OI в USD",
        12.0,
        usd_strong or abs(oi_usd) >= settings.min_oi_change_usd,
        f"{abs(oi_usd):,.0f} $ (мин. {settings.min_oi_change_usd:,.0f} $)".replace(",", " "),
    )

    score += _add(
        factors,
        "volume_spike",
        "Всплеск объёма",
        10.0,
        vol_spike,
        "объём выше среднего" if vol_spike else "без всплеска",
    )

    speed = float(signal.details.get("price_speed_pct_per_min", 0.0))
    momentum_ok = abs(speed) >= (price_thr / max(signal.oi_period_minutes, 1)) * 0.8
    score += _add(
        factors,
        "momentum",
        "Импульс цены",
        9.0,
        momentum_ok,
        f"{speed:+.2f}%/мин",
    )

    ema_short = signal.ema_short
    ema_long = signal.ema_long
    price_now = signal.current_price
    trend_ok = False
    if price_now and ema_short and ema_long:
        trend_ok = price_now >= ema_short >= ema_long if is_long else price_now <= ema_short <= ema_long
    score += _add(
        factors,
        "trend_ema",
        "Тренд EMA 9/21",
        8.0,
        trend_ok,
        "по тренду" if trend_ok else "против или флэт",
    )

    rsi = signal.rsi
    rsi_ok = False
    if rsi is not None:
        if is_long:
            rsi_ok = 35 <= rsi <= 78
        else:
            rsi_ok = 22 <= rsi <= 65
    score += _add(
        factors,
        "rsi",
        "RSI не перегрет",
        7.0 if rsi_ok else -6.0,
        rsi_ok,
        f"RSI {rsi:.1f}" if rsi is not None else "нет данных",
    )

    funding = signal.funding_rate
    funding_ok = True
    if funding is not None:
        if is_long:
            funding_ok = funding < 0.001
        else:
            funding_ok = funding > -0.001
    score += _add(
        factors,
        "funding",
        "Funding благоприятный",
        6.0,
        funding_ok,
        f"{funding:.4f}" if funding is not None else "—",
    )

    spread = signal.spread
    liquidity_ok = True
    if spread is not None and price_now and price_now > 0:
        spread_bps = (spread / price_now) * 10_000
        liquidity_ok = spread_bps < 15
    score += _add(
        factors,
        "liquidity",
        "Спред / ликвидность",
        5.0,
        liquidity_ok,
        "узкий спред" if liquidity_ok else "широкий спред",
    )

    quality_types = {
        "vertical_pump", "vertical_dump", "mega_pump", "mega_dump",
        "pump", "dump", "short_squeeze",
    }
    pattern_ok = signal.signal_type in quality_types
    score += _add(
        factors,
        "pattern",
        "Качество паттерна",
        11.0,
        pattern_ok,
        signal.signal_type,
    )

    if signal.signal_type in {"vertical_pump", "vertical_dump"}:
        flat_rng = signal.details.get("flat_range_percent")
        vel = signal.details.get("velocity_ratio")
        breakout_ok = flat_rng is not None and vel is not None
        score += _add(
            factors,
            "breakout",
            "Выход из проторговки",
            14.0,
            breakout_ok,
            f"флет {flat_rng}% → ускорение {vel}×" if breakout_ok else "—",
        )

    if signal.signal_type in {"mega_pump", "mega_dump"}:
        tier = signal.details.get("flash_tier", 0)
        score += _add(
            factors,
            "mega_move",
            "Мега-импульс",
            13.0,
            float(tier or 0) >= 10,
            f"≥{tier}% за {signal.oi_period_minutes}м",
        )

    early_ok = signal.signal_score <= 3
    score += _add(
        factors,
        "timing",
        "Ранность входа",
        8.0,
        early_ok,
        f"ранность {signal.signal_score}/10",
    )

    if btc_change_percent is not None:
        if is_long:
            btc_ok = btc_change_percent >= -0.15
        else:
            btc_ok = btc_change_percent <= 0.15
        score += _add(
            factors,
            "btc_context",
            "Контекст BTC",
            9.0 if btc_ok else -11.0,
            btc_ok,
            f"BTC {btc_change_percent:+.2f}% / 5м",
        )

    misaligned_penalty = not oi_aligned and abs(oi) >= oi_thr
    if misaligned_penalty:
        score += _add(
            factors,
            "divergence",
            "Дивергенция OI/цена",
            -18.0,
            False,
            "сильный OI без движения цены",
        )

    percent = _clamp(score, 8.0, 94.0)

    if signal.signal_type in PROBABILITY_BYPASS_TYPES:
        percent = max(percent, 72.0)

    return ProbabilityAssessment(
        percent=round(percent, 1),
        verdict=_verdict(percent),
        factors=factors,
        raw_score=score,
    )


def format_probability_block(assessment: ProbabilityAssessment) -> str:
    bar_filled = int(_clamp(assessment.percent / 10, 1, 10))
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    lines = [
        f"🎯 <b>Вероятность: {assessment.percent:.0f}%</b> ({assessment.verdict})",
        f"<code>{bar}</code>",
        "",
        "<b>Факторы:</b>",
    ]
    for factor in assessment.top_factors(6):
        mark = "✅" if factor.passed and factor.impact > 0 else "⚠️" if factor.impact > 0 else "❌"
        sign = "+" if factor.impact > 0 else ""
        lines.append(f"{mark} {factor.label}: <i>{factor.detail}</i> ({sign}{factor.impact:.0f}%)")
    return "\n".join(lines)


def format_probability_from_signal(signal: Signal) -> str:
    factors_raw = signal.details.get("probability_factors") or []
    factors = [
        ProbabilityFactor(
            key=item.get("key", ""),
            label=item.get("label", ""),
            impact=float(item.get("impact", 0)),
            passed=bool(item.get("passed")),
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
