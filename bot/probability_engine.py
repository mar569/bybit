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
    "oi_price_sync": 0.16,
    "threshold_excess": 0.12,
    "oi_usd_flow": 0.10,
    "momentum": 0.09,
    "volume": 0.06,
    "trend": 0.06,
    "rsi": 0.05,
    "funding": 0.04,
    "liquidity": 0.02,
    "pattern": 0.07,
    "timing": 0.04,
    "btc": 0.06,
    "market_phase": 0.06,
    "htf_oi_context": 0.04,
    "long_short": 0.05,
    "liquidations": 0.04,
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
        if rsi < 28:
            return 0.28
        if rsi < 42:
            return _clamp(0.38 + (rsi - 28) / 14 * 0.22, 0.0, 1.0)
        if rsi <= 50:
            return 0.48
        if rsi <= 64:
            return _clamp(0.52 + (rsi - 50) / 14 * 0.38, 0.0, 1.0)
        if rsi <= 75:
            return _clamp(0.88 - (rsi - 64) / 11 * 0.40, 0.0, 1.0)
        return 0.32
    if rsi > 72:
        return 0.28
    if rsi > 58:
        return _clamp(0.38 + (72 - rsi) / 14 * 0.22, 0.0, 1.0)
    if rsi >= 50:
        return 0.48
    if rsi >= 36:
        return _clamp(0.52 + (50 - rsi) / 14 * 0.38, 0.0, 1.0)
    if rsi >= 25:
        return _clamp(0.88 - (36 - rsi) / 11 * 0.40, 0.0, 1.0)
    return 0.32


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


def _long_short_strength(
    ls_ratio: float | None,
    buy_ratio: float | None,
    sell_ratio: float | None,
    is_long: bool,
) -> tuple[float, str]:
    if ls_ratio is None:
        return 0.45, "нет данных Bybit L/S"
    long_pct = (buy_ratio or 0) * 100
    short_pct = (sell_ratio or 0) * 100
    detail = f"L/S {ls_ratio:.2f} (лонг {long_pct:.0f}% / шорт {short_pct:.0f}% аккаунтов)"
    if is_long:
        if ls_ratio < 0.85:
            return 0.90, detail + " — перекос в шорты"
        if ls_ratio < 1.0:
            return 0.74, detail + " — чуть больше шортов"
        if ls_ratio <= 1.15:
            return 0.56, detail + " — баланс"
        if ls_ratio <= 1.35:
            return 0.36, detail + " — перекос в лонги"
        return 0.20, detail + " — толпа в лонге"
    if ls_ratio > 1.15:
        return 0.90, detail + " — перекос в лонги (контр-тренд short)"
    if ls_ratio > 1.0:
        return 0.74, detail + " — чуть больше лонгов"
    if ls_ratio >= 0.85:
        return 0.56, detail + " — баланс"
    if ls_ratio >= 0.65:
        return 0.36, detail + " — перекос в шорты"
    return 0.20, detail + " — толпа в шорте"


def _liquidation_strength(
    long_liq_usd: float,
    short_liq_usd: float,
    is_long: bool,
    window_minutes: int = 15,
) -> tuple[float, str]:
    total = long_liq_usd + short_liq_usd
    if total < 1_000:
        return 0.45, f"ликвидаций <1k$ за {window_minutes}м"
    long_share = long_liq_usd / total
    short_share = short_liq_usd / total
    detail = (
        f"за {window_minutes}м: лонги {long_liq_usd:,.0f}$ | шорты {short_liq_usd:,.0f}$"
        .replace(",", " ")
    )
    if is_long:
        if short_share >= 0.65:
            return 0.92, detail + " — шорты ликвидируют"
        if short_share >= 0.52:
            return 0.72, detail
        if long_share >= 0.65:
            return 0.22, detail + " — лонги смывают"
        return 0.50, detail
    if long_share >= 0.65:
        return 0.92, detail + " — лонги ликвидируют"
    if long_share >= 0.52:
        return 0.72, detail
    if short_share >= 0.65:
        return 0.22, detail + " — шорты смывают"
    return 0.50, detail


def _compute_structure_penalty(
    market_structure: dict[str, object] | None,
    *,
    is_long: bool,
    signal_type: str,
    signal_score: int,
) -> tuple[float, str]:
    """Штраф к вероятности, если краткий импульс против старшей структуры (как LAB)."""
    if not isinstance(market_structure, dict):
        return 0.0, ""

    penalty = 0.0
    notes: list[str] = []

    post_crash = bool(market_structure.get("post_crash"))
    lower_highs = bool(market_structure.get("lower_highs"))
    dead_cat = bool(market_structure.get("dead_cat_bounce"))
    try:
        drawdown = float(market_structure.get("drawdown_from_high_pct", 0) or 0)
        range_pos = float(market_structure.get("range_position", 0.5) or 0.5)
    except (TypeError, ValueError):
        drawdown = 0.0
        range_pos = 0.5

    pulse_types = {"pulse_pump", "pulse_dump", "oi_pump", "oi_dump", "price_pump", "price_dump"}

    if is_long:
        if post_crash:
            penalty += 7.0 if drawdown < 18 else 10.0
            notes.append(f"−{drawdown:.0f}% от хая")
        if lower_highs:
            penalty += 7.0
            notes.append("lower highs")
        if dead_cat:
            penalty += 11.0
            notes.append("отскок после дампа")
        if range_pos > 0.68 and drawdown >= 8.0:
            penalty += 5.0
            notes.append("у сопротивления")
        if signal_type in pulse_types and (post_crash or lower_highs or dead_cat):
            penalty += 6.0
            notes.append("пульс vs структура")
        if signal_score >= 3 and (post_crash or dead_cat):
            penalty += 3.0
    else:
        if post_crash and dead_cat and not lower_highs:
            penalty += 5.0
            notes.append("отскок — short рано")
        if not post_crash and drawdown < 5 and lower_highs is False:
            chg_3h = market_structure.get("price_changes", {})
            try:
                c3 = float(chg_3h.get("3", chg_3h.get("2", 0)) or 0)
            except (TypeError, ValueError):
                c3 = 0.0
            if c3 > 2.0:
                penalty += 6.0
                notes.append("short против локального роста")

    penalty = min(penalty, 32.0)
    return penalty, " | ".join(notes)


def assess_signal_probability(
    signal: Signal,
    settings: ScannerSettings,
    thresholds: ExchangeThresholds,
    *,
    btc_change_percent: float | None = None,
    vol_spike: bool = False,
    market_structure: dict[str, object] | None = None,
    account_ratio: dict[str, object] | None = None,
    liquidations: dict[str, object] | None = None,
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

    ms = market_structure or signal.details.get("market_structure")
    if isinstance(ms, dict) and ms.get("phase"):
        phase_strength = float(ms.get("phase_strength", 0.5))
        oi_ctx_strength = float(ms.get("oi_context_strength", 0.5))
        phase_label = str(ms.get("phase_label", ""))
        oi_label = str(ms.get("oi_narrative_label", ""))
    else:
        phase_strength = 0.48
        oi_ctx_strength = 0.48
        phase_label = ""
        oi_label = ""

    ar = account_ratio or signal.details.get("account_ratio")
    ls_ratio = None
    buy_ratio = None
    sell_ratio = None
    if isinstance(ar, dict):
        try:
            ls_ratio = float(ar["long_short_ratio"]) if ar.get("long_short_ratio") is not None else None
            buy_ratio = float(ar["buy_ratio"]) if ar.get("buy_ratio") is not None else None
            sell_ratio = float(ar["sell_ratio"]) if ar.get("sell_ratio") is not None else None
        except (KeyError, TypeError, ValueError):
            pass
    long_short_strength, long_short_detail = _long_short_strength(
        ls_ratio, buy_ratio, sell_ratio, is_long,
    )

    liq = liquidations or signal.details.get("liquidations")
    long_liq = 0.0
    short_liq = 0.0
    liq_window = 15
    if isinstance(liq, dict):
        try:
            long_liq = float(liq.get("long_liq_usd", 0) or 0)
            short_liq = float(liq.get("short_liq_usd", 0) or 0)
            liq_window = int(liq.get("window_minutes", 15))
        except (TypeError, ValueError):
            pass
    liquidation_strength, liquidation_detail = _liquidation_strength(
        long_liq, short_liq, is_long, liq_window,
    )

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
        "market_phase": phase_strength,
        "htf_oi_context": oi_ctx_strength,
        "long_short": long_short_strength,
        "liquidations": liquidation_strength,
    }

    weighted = sum(FACTOR_WEIGHTS[k] * strengths[k] for k in FACTOR_WEIGHTS)
    percent = 18.0 + weighted * 72.0

    if not oi_aligned:
        align_penalty = 0.35 + 0.25 * max(oi_strength, price_strength)
        percent *= align_penalty

    if not oi_aligned and abs(oi) >= oi_thr * 1.2 and abs(price) < price_thr * 0.35:
        percent *= 0.55

    structure_penalty, structure_penalty_detail = _compute_structure_penalty(
        ms if isinstance(ms, dict) else None,
        is_long=is_long,
        signal_type=signal.signal_type,
        signal_score=signal.signal_score,
    )
    if structure_penalty > 0:
        percent -= structure_penalty

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

    if isinstance(ms, dict) and ms.get("phase"):
        factors.append(ProbabilityFactor(
            "market_phase",
            f"Фаза рынка ({ms.get('hours_analyzed', 5)}ч)",
            phase_strength,
            FACTOR_WEIGHTS["market_phase"],
            phase_strength * FACTOR_WEIGHTS["market_phase"] * 72,
            phase_label or str(ms.get("phase_detail", "")),
        ))
        factors.append(ProbabilityFactor(
            "htf_oi_context",
            "Позиции трейдеров",
            oi_ctx_strength,
            FACTOR_WEIGHTS["htf_oi_context"],
            oi_ctx_strength * FACTOR_WEIGHTS["htf_oi_context"] * 72,
            oi_label or str(ms.get("oi_narrative", "")),
        ))

    if isinstance(ar, dict) and ls_ratio is not None:
        factors.append(ProbabilityFactor(
            "long_short",
            "Long/Short ratio (Bybit)",
            long_short_strength,
            FACTOR_WEIGHTS["long_short"],
            long_short_strength * FACTOR_WEIGHTS["long_short"] * 72,
            long_short_detail,
        ))

    if isinstance(liq, dict):
        factors.append(ProbabilityFactor(
            "liquidations",
            "Ликвидации (Bybit WS)",
            liquidation_strength,
            FACTOR_WEIGHTS["liquidations"],
            liquidation_strength * FACTOR_WEIGHTS["liquidations"] * 72,
            liquidation_detail,
        ))

    if structure_penalty > 0:
        factors.append(ProbabilityFactor(
            "structure_penalty",
            "Структура графика",
            0.0,
            0.0,
            -structure_penalty,
            structure_penalty_detail or "импульс против старшего ТФ",
        ))
        percent = _clamp(percent, 11, 89)

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
    for factor in assessment.top_factors(8):
        if factor.key in ("divergence", "structure_penalty"):
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
