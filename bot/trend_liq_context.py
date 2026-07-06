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
    trend_quality: str
    phase: str
    phase_label: str
    cvd_proxy: float
    cvd_detail: str
    oi_narrative: str
    oi_narrative_label: str
    drawdown_from_high_pct: float
    is_correction: bool
    near_high: bool


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
    c_now = klines[-1].close
    c_1h = klines[-13].close if len(klines) >= 13 else klines[0].close
    c_4h = klines[-49].close if len(klines) >= 49 else klines[0].close
    return _percent_change(c_1h, c_now), _percent_change(c_4h, c_now)


def _classify_trend_quality(t1h: float, t4h: float) -> str:
    if t4h >= 5.0 or t1h >= 4.0:
        return "strong_up"
    if t4h <= -5.0 or t1h <= -4.0:
        return "strong_down"
    if t1h >= 2.5 and t4h > 0:
        return "moderate_up"
    if t1h <= -2.5 and t4h < 0:
        return "moderate_down"
    return "weak"


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


def _oi_supports_direction(oi_change_pct: float | None, *, predict_long: bool) -> bool:
    oi = oi_change_pct if oi_change_pct is not None else 0.0
    if predict_long:
        return oi >= 1.0
    return oi <= -1.0


def _has_reliable_oi(ctx: TrendLiqContext) -> bool:
    return ctx.oi_narrative not in {"insufficient_oi", "mixed"}


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
    quality = _classify_trend_quality(t1h, t4h)
    predict_long = cluster_side == SIDE_LONG_LIQ
    ms = analyze_market_structure(klines, oi_bars, is_long=predict_long, hours=5)

    if quality == "strong_up":
        trend_label = f"тренд вверх +{max(t1h, 0):.1f}% (1ч) / {t4h:+.1f}% (4ч)"
    elif quality == "strong_down":
        trend_label = f"тренд вниз {min(t1h, 0):.1f}% (1ч) / {t4h:+.1f}% (4ч)"
    elif quality in {"moderate_up", "moderate_down"}:
        trend_label = f"умеренный ход {t1h:+.1f}% (1ч) / {t4h:+.1f}% (4ч)"
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

    near_high = ms.drawdown_from_high_pct < 2.5 and t1h > 3.0

    return TrendLiqContext(
        trend_pct_1h=round(t1h, 2),
        trend_pct_4h=round(t4h, 2),
        trend_label=trend_label,
        trend_quality=quality,
        phase=ms.phase,
        phase_label=ms.phase_label,
        cvd_proxy=cvd_ratio,
        cvd_detail=cvd_detail,
        oi_narrative=ms.oi_narrative,
        oi_narrative_label=ms.oi_narrative_label,
        drawdown_from_high_pct=ms.drawdown_from_high_pct,
        is_correction=is_correction,
        near_high=near_high,
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
    oi = oi_change_pct if oi_change_pct is not None else 0.0
    crowd_long = (buy_ratio or 0.5) >= 0.58
    crowd_short = (buy_ratio or 0.5) <= 0.42
    cvd_strong_bull = ctx.cvd_proxy >= 0.62
    cvd_strong_bear = ctx.cvd_proxy <= 0.38
    oi_ok_long = _oi_supports_direction(oi_change_pct, predict_long=True)
    oi_ok_short = _oi_supports_direction(oi_change_pct, predict_long=False)
    reliable_oi = _has_reliable_oi(ctx) or (
        oi_change_pct is not None and abs(oi_change_pct) >= 0.8
    )

    # Шорты смыли на росте
    if cluster_side == SIDE_SHORT_LIQ:
        # Перегрев у хая: смыв шортов часто = разворот, не continuation
        if ctx.near_high or (ctx.trend_pct_1h > 7.0 and ctx.drawdown_from_high_pct < 3.0):
            if cvd_strong_bear or oi < -0.5 or crowd_long:
                return ScenarioVerdict(
                    "short",
                    "↘️ разворот вниз",
                    "у хая смыли шортов — чаще фиксация/разворот, не новый лонг",
                    continuation_up=False,
                    continuation_down=True,
                )
            return ScenarioVerdict(
                "wait",
                "⏸ выжидание",
                "перегрев после смыва шортов — жди откат или пробой",
                continuation_up=False,
                continuation_down=False,
            )

        # Сильный тренд вверх + коррекция — единственный кейс для continuation long
        if ctx.trend_quality in {"strong_up", "moderate_up"} and ctx.is_correction:
            if (
                cvd_strong_bull
                and oi_ok_long
                and not crowd_long
            ):
                return ScenarioVerdict(
                    "long",
                    "↗️ продолжение вверх",
                    "сильный тренд → смыв шортов → коррекция → OI/CVD подтверждают",
                    continuation_up=True,
                    continuation_down=False,
                )
            if cvd_strong_bear or oi < -1.0 or (reliable_oi and not oi_ok_long and oi_change_pct is not None):
                return ScenarioVerdict(
                    "short",
                    "↘️ откат вниз",
                    "после смыва шортов поток слабеет — вероятен откат",
                    continuation_up=False,
                    continuation_down=True,
                )
            return ScenarioVerdict(
                "wait",
                "⏸ выжидание",
                "коррекция после смыва — нет чистого подтверждения OI/CVD",
                continuation_up=False,
                continuation_down=False,
            )

        # Слабый/боковой тренд: short liq = чаще ловушка для лонгов
        if ctx.trend_quality == "weak":
            return ScenarioVerdict(
                "wait",
                "⏸ выжидание",
                "слабый тренд + смыв шортов — нет edge, жди структуру",
                continuation_up=False,
                continuation_down=False,
            )

        return ScenarioVerdict(
            "short",
            "↘️ откат вниз",
            "шорты смыли без сильного тренда — типичная зона отката",
            continuation_up=False,
            continuation_down=True,
        )

    # Лонги смыли на падении
    if cluster_side == SIDE_LONG_LIQ:
        if ctx.near_high and ctx.trend_quality in {"strong_down", "moderate_down"}:
            pass
        if ctx.trend_quality in {"strong_down", "moderate_down"} and ctx.is_correction:
            if (cvd_strong_bull or oi < -2.0) and not crowd_short:
                return ScenarioVerdict(
                    "long",
                    "↗️ отскок",
                    "падение → смыв лонгов → стабилизация, возможен отскок",
                    continuation_up=True,
                    continuation_down=False,
                )
            if cvd_strong_bear or (reliable_oi and oi > 1.5):
                return ScenarioVerdict(
                    "short",
                    "↘️ продолжение вниз",
                    "смыв лонгов не развернул поток — давление вниз",
                    continuation_up=False,
                    continuation_down=True,
                )
            return ScenarioVerdict(
                "wait",
                "⏸ выжидание",
                "после смыва лонгов — жди подтверждения отскока",
                continuation_up=False,
                continuation_down=False,
            )

        if ctx.trend_quality == "weak":
            return ScenarioVerdict(
                "wait",
                "⏸ выжидание",
                "слабый тренд + смыв лонгов — нет чёткого сценария",
                continuation_up=False,
                continuation_down=False,
            )

        return ScenarioVerdict(
            "long",
            "↗️ отскок вверх",
            "кластер лонг-ликвидаций — зона возможного отскока",
            continuation_up=True,
            continuation_down=False,
        )

    return ScenarioVerdict(
        "wait",
        "⏸ выжидание",
        "недостаточно контекста для направленного сценария",
        continuation_up=False,
        continuation_down=False,
    )


def _score_trend_alignment(ctx: TrendLiqContext, cluster_side: str) -> tuple[float, str]:
    q = ctx.trend_quality
    if cluster_side == SIDE_SHORT_LIQ and q in {"strong_up", "moderate_up"}:
        return 0.85, f"{ctx.trend_label} + смыв шортов"
    if cluster_side == SIDE_LONG_LIQ and q in {"strong_down", "moderate_down"}:
        return 0.85, f"{ctx.trend_label} + смыв лонгов"
    if q == "weak":
        return 0.30, f"{ctx.trend_label} — слабый тренд, низкий edge"
    if cluster_side == SIDE_SHORT_LIQ and q in {"strong_down", "moderate_down"}:
        return 0.35, f"{ctx.trend_label} — шорты смыли против тренда"
    if cluster_side == SIDE_LONG_LIQ and q in {"strong_up", "moderate_up"}:
        return 0.35, f"{ctx.trend_label} — лонги смыли против тренда"
    return 0.50, ctx.trend_label


def _score_cvd(ctx: TrendLiqContext, predict_long: bool) -> tuple[float, str]:
    ratio = ctx.cvd_proxy
    detail = ctx.cvd_detail
    if predict_long:
        if ratio >= 0.62:
            return 0.82, detail
        if ratio >= 0.55:
            return 0.55, detail + " (слабое подтверждение)"
        if ratio <= 0.38:
            return 0.22, detail
        return 0.42, detail
    if ratio <= 0.38:
        return 0.82, detail
    if ratio <= 0.45:
        return 0.55, detail + " (слабое подтверждение)"
    if ratio >= 0.62:
        return 0.22, detail
    return 0.42, detail


def score_trend_liq_factors(
    ctx: TrendLiqContext,
    cluster_side: str,
    *,
    predict_long: bool,
) -> list[tuple[str, str, float, str]]:
    trend_s, trend_d = _score_trend_alignment(ctx, cluster_side)
    cvd_s, cvd_d = _score_cvd(ctx, predict_long)
    oi_s = 0.40
    oi_d = ctx.oi_narrative_label
    if ctx.oi_narrative == "insufficient_oi":
        oi_s = 0.22
        oi_d = "мало данных OI — сценарий ненадёжен"
    elif ctx.oi_narrative in {"aligned_long", "accumulation"} and predict_long:
        oi_s = 0.80
    elif ctx.oi_narrative in {"aligned_short", "shorts_building"} and not predict_long:
        oi_s = 0.80
    elif ctx.oi_narrative in {"squeeze_risk", "capitulation"}:
        oi_s = 0.70
    elif ctx.oi_narrative == "long_unwind" and not predict_long:
        oi_s = 0.72

    if ctx.near_high and cluster_side == SIDE_SHORT_LIQ:
        trend_s = min(trend_s, 0.45)
        trend_d += " · у локального хая"

    corr_s = 0.68 if ctx.is_correction else 0.48
    corr_d = "коррекция после импульса" if ctx.is_correction else "без явной коррекции"
    return [
        ("trend", "Тренд + liq", trend_s, trend_d),
        ("cvd", "CVD (объём)", cvd_s, cvd_d),
        ("oi_narrative", "Open Interest", oi_s, oi_d),
        ("correction", "Фаза", corr_s, f"{ctx.phase_label} · {corr_d}"),
    ]
