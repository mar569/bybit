"""Контекст тренда + ликвидации + OI/CVD-proxy для чата анализов (как на CoinGlass)."""
from __future__ import annotations

from dataclasses import dataclass

from .bybit_klines import KlineBar
from .liquidation_alerts import SIDE_LONG_LIQ, SIDE_SHORT_LIQ
from .market_structure import FiveMinOiBar, analyze_market_structure


@dataclass(frozen=True)
class TrendLiqContext:
    trend_pct_1h: float
    trend_pct_4h: float
    trend_label: str
    phase: str
    phase_label: str
    cvd_proxy: float
    cvd_detail: str
    oi_narrative: str
    oi_narrative_label: str
    drawdown_from_high_pct: float
    is_correction: bool


@dataclass(frozen=True)
class ScenarioVerdict:
    direction: str
    direction_label: str
    scenario_text: str
    continuation_up: bool
    continuation_down: bool


def _percent_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / abs(a) * 100.0


def _trend_from_klines(klines: list[KlineBar]) -> tuple[float, float]:
    if len(klines) < 13:
        return 0.0, 0.0
    # 5m bars: 12 = 1h, 48 = 4h
    c_now = klines[-1].close
    c_1h = klines[-13].close if len(klines) >= 13 else klines[0].close
    c_4h = klines[-49].close if len(klines) >= 49 else klines[0].close
    return _percent_change(c_1h, c_now), _percent_change(c_4h, c_now)


def _volume_cvd_proxy(klines: list[KlineBar], bars: int = 12) -> tuple[float, str]:
    if len(klines) < 3:
        return 0.5, "CVD: мало данных"
    segment = klines[-bars:] if len(klines) >= bars else klines
    buy_vol = 0.0
    sell_vol = 0.0
    for bar in segment:
        if bar.close >= bar.open:
            buy_vol += bar.volume
        else:
            sell_vol += bar.volume
    total = buy_vol + sell_vol
    if total <= 0:
        return 0.5, "CVD: нет объёма"
    ratio = buy_vol / total
    if ratio >= 0.62:
        return ratio, f"CVD↑ покупки {ratio:.0%} объёма (5m×{len(segment)})"
    if ratio <= 0.38:
        return ratio, f"CVD↓ продажи {(1 - ratio):.0%} объёма (5m×{len(segment)})"
    return ratio, f"CVD≈ баланс {ratio:.0%} buy / {(1 - ratio):.0%} sell"


def build_trend_liq_context(
    klines: list[KlineBar],
    oi_bars: list[FiveMinOiBar],
    *,
    cluster_side: str,
    price_change_since_cluster_pct: float,
) -> TrendLiqContext | None:
    if not klines:
        return None

    t1h, t4h = _trend_from_klines(klines)
    predict_long = cluster_side == SIDE_LONG_LIQ
    ms = analyze_market_structure(klines, oi_bars, is_long=predict_long, hours=5)

    if t4h >= 4.0 or (t1h >= 2.5 and ms.phase in {"impulse_up", "correction_up"}):
        trend_label = f"тренд вверх +{max(t1h, 0):.1f}% (1ч) / {t4h:+.1f}% (4ч)"
    elif t4h <= -4.0 or (t1h <= -2.5 and ms.phase in {"impulse_down", "correction_down"}):
        trend_label = f"тренд вниз {min(t1h, 0):.1f}% (1ч) / {t4h:+.1f}% (4ч)"
    else:
        trend_label = f"боковик/слабый ход {t1h:+.1f}% (1ч)"

    cvd_ratio, cvd_detail = _volume_cvd_proxy(klines)

    is_correction = False
    if t1h > 2.0 and -4.0 < price_change_since_cluster_pct < 0.3:
        is_correction = True
    if t1h < -2.0 and -0.3 < price_change_since_cluster_pct < 4.0:
        is_correction = True
    if ms.phase in {"correction_up", "correction_down", "consolidation"}:
        is_correction = True

    return TrendLiqContext(
        trend_pct_1h=round(t1h, 2),
        trend_pct_4h=round(t4h, 2),
        trend_label=trend_label,
        phase=ms.phase,
        phase_label=ms.phase_label,
        cvd_proxy=cvd_ratio,
        cvd_detail=cvd_detail,
        oi_narrative=ms.oi_narrative,
        oi_narrative_label=ms.oi_narrative_label,
        drawdown_from_high_pct=ms.drawdown_from_high_pct,
        is_correction=is_correction,
    )


def resolve_scenario(
    ctx: TrendLiqContext,
    cluster_side: str,
    *,
    price_change_since_cluster_pct: float,
    oi_change_pct: float | None,
    buy_ratio: float | None,
) -> ScenarioVerdict:
    """Сценарий после кластера ликвидаций на тренде (LAB: шорты смыли → коррекция → ?)."""
    oi = oi_change_pct or 0.0
    crowd_long = (buy_ratio or 0.5) >= 0.52
    cvd_bull = ctx.cvd_proxy >= 0.55
    cvd_bear = ctx.cvd_proxy <= 0.45

    # Шорты ликвидировали на росте — типичный LAB
    if cluster_side == SIDE_SHORT_LIQ and ctx.trend_pct_1h > 2.0:
        if ctx.is_correction or price_change_since_cluster_pct < 0.5:
            if cvd_bull and oi > 0 and crowd_long:
                return ScenarioVerdict(
                    "long",
                    "↗️ продолжение вверх",
                    "шорты смыли на тренде → коррекция → OI/CVD держат покупателей",
                    continuation_up=True,
                    continuation_down=False,
                )
            if cvd_bear or oi < -2:
                return ScenarioVerdict(
                    "short",
                    "↘️ откат вниз",
                    "после смыва шортов рынок распределяется — OI/CVD слабеют",
                    continuation_up=False,
                    continuation_down=True,
                )
            return ScenarioVerdict(
                "wait",
                "⏸ выжидание",
                "коррекция после смыва шортов — жди подтверждения направления",
                continuation_up=False,
                continuation_down=False,
            )
        return ScenarioVerdict(
            "short",
            "↘️ откат",
            "перегрев после смыва шортов — возможен откат к поддержке",
            continuation_up=False,
            continuation_down=True,
        )

    # Лонги ликвидировали на падении
    if cluster_side == SIDE_LONG_LIQ and ctx.trend_pct_1h < -2.0:
        if ctx.is_correction or price_change_since_cluster_pct > -0.5:
            if cvd_bull or oi < -2:
                return ScenarioVerdict(
                    "long",
                    "↗️ отскок",
                    "лонги смыли на падении → стабилизация, возможен отскок",
                    continuation_up=True,
                    continuation_down=False,
                )
            return ScenarioVerdict(
                "short",
                "↘️ продолжение вниз",
                "смыв лонгов не развернул поток — давление сохраняется",
                continuation_up=False,
                continuation_down=True,
            )

    # Классика: long liq → отскок, short liq → откат
    if cluster_side == SIDE_LONG_LIQ:
        return ScenarioVerdict(
            "long",
            "↗️ отскок вверх",
            "кластер лонг-ликвидаций — типичная зона отскока",
            continuation_up=True,
            continuation_down=False,
        )
    return ScenarioVerdict(
        "short",
        "↘️ откат вниз",
        "кластер шорт-ликвидаций — типичная зона отката",
        continuation_up=False,
        continuation_down=True,
    )


def _score_trend_alignment(ctx: TrendLiqContext, cluster_side: str) -> tuple[float, str]:
    if cluster_side == SIDE_SHORT_LIQ and ctx.trend_pct_1h > 2.5:
        return 0.92, f"{ctx.trend_label} + смыв шортов"
    if cluster_side == SIDE_LONG_LIQ and ctx.trend_pct_1h < -2.5:
        return 0.92, f"{ctx.trend_label} + смыв лонгов"
    if abs(ctx.trend_pct_1h) < 1.0:
        return 0.35, f"{ctx.trend_label} — слабый тренд"
    return 0.58, ctx.trend_label


def _score_cvd(ctx: TrendLiqContext, predict_long: bool) -> tuple[float, str]:
    ratio = ctx.cvd_proxy
    detail = ctx.cvd_detail
    if predict_long:
        if ratio >= 0.62:
            return 0.88, detail
        if ratio >= 0.52:
            return 0.68, detail
        if ratio <= 0.38:
            return 0.28, detail
        return 0.48, detail
    if ratio <= 0.38:
        return 0.88, detail
    if ratio <= 0.48:
        return 0.68, detail
    if ratio >= 0.62:
        return 0.28, detail
    return 0.48, detail


def score_trend_liq_factors(
    ctx: TrendLiqContext,
    cluster_side: str,
    *,
    predict_long: bool,
) -> list[tuple[str, str, float, str]]:
    trend_s, trend_d = _score_trend_alignment(ctx, cluster_side)
    cvd_s, cvd_d = _score_cvd(ctx, predict_long)
    oi_s = 0.55
    oi_d = ctx.oi_narrative_label
    if ctx.oi_narrative in {"aligned_long", "accumulation"} and predict_long:
        oi_s = 0.78
    elif ctx.oi_narrative in {"aligned_short", "shorts_building"} and not predict_long:
        oi_s = 0.78
    elif ctx.oi_narrative in {"squeeze_risk", "capitulation"}:
        oi_s = 0.72
    corr_s = 0.72 if ctx.is_correction else 0.50
    corr_d = "идёт коррекция после импульса" if ctx.is_correction else "без явной коррекции"
    return [
        ("trend", "Тренд + liq", trend_s, trend_d),
        ("cvd", "CVD (объём)", cvd_s, cvd_d),
        ("oi_narrative", "Open Interest", oi_s, oi_d),
        ("correction", "Фаза", corr_s, f"{ctx.phase_label} · {corr_d}"),
    ]
