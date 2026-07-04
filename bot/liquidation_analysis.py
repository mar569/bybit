from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .bybit_klines import BybitKlineCache
from .bybit_liquidations import BybitLiquidationTracker
from .binance_liquidations import BinanceLiquidationTracker
from .bybit_market_data import BybitAccountRatioCache
from .liquidation_alerts import (
    SIDE_LONG_LIQ,
    SIDE_SHORT_LIQ,
    LiquidationAlertEvent,
    base_ticker,
    coinglass_url,
    exchange_trade_url,
)
from .market_structure import FiveMinOiBar, analyze_market_structure
from .probability_engine import _liquidation_strength, _long_short_strength
from .scanner_engine import SignalEngine

logger = logging.getLogger(__name__)

OI_BAR_MAX_COUNT = 72

FACTOR_WEIGHTS: dict[str, float] = {
    "cluster": 0.16,
    "liq_imbalance": 0.22,
    "post_price": 0.18,
    "oi_flow": 0.12,
    "funding": 0.08,
    "crowd_ls": 0.10,
    "structure": 0.12,
    "btc": 0.05,
}


@dataclass
class AnalysisFactor:
    key: str
    label: str
    score: float
    weight: float
    detail: str


@dataclass
class LiquidationAnalysisResult:
    symbol: str
    exchange: str
    cluster_side: str
    cluster_usd: float
    cluster_events: int
    cluster_price: float
    cluster_time: float
    current_price: float
    price_change_since_cluster_pct: float
    oi_change_since_cluster_pct: float | None
    direction: str
    direction_label: str
    confidence: float
    window_min: int
    window_max: int
    invalidation_price: float
    invalidation_label: str
    factors: list[AnalysisFactor] = field(default_factory=list)
    continuation_risk: bool = False


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _score_cluster(cluster_usd: float, event_count: int, min_usd: float) -> tuple[float, str]:
    ratio = cluster_usd / max(min_usd, 1.0)
    score = _clamp(0.45 + (ratio - 1.0) * 0.12, 0.40, 0.95)
    if event_count >= 20:
        score = _clamp(score + 0.08, 0.0, 1.0)
    elif event_count >= 10:
        score = _clamp(score + 0.04, 0.0, 1.0)
    detail = f"${cluster_usd:,.0f} за всплеск ({event_count} событ.)".replace(",", " ")
    if ratio >= 2.0:
        detail += " — крупный кластер"
    elif ratio >= 1.3:
        detail += " — выше порога"
    return score, detail


def _score_post_price(
    cluster_side: str,
    price_change_pct: float,
    *,
    predict_long: bool,
) -> tuple[float, str]:
    """Оценка реакции цены после кластера (через delay)."""
    detail = f"цена {price_change_pct:+.2f}% от кластера"
    if predict_long:
        if price_change_pct <= -2.5:
            return 0.22, detail + " — продолжение слива"
        if price_change_pct <= -1.0:
            return 0.48, detail + " — ещё давят вниз"
        if -1.0 < price_change_pct <= 0.2:
            return 0.82, detail + " — стабилизация у дна"
        if 0.2 < price_change_pct <= 1.5:
            return 0.90, detail + " — начало отскока"
        if 1.5 < price_change_pct <= 3.0:
            return 0.62, detail + " — отскок уже идёт"
        return 0.40, detail + " — поздно, ход частично отыгран"
    # predict short (откат вниз после short liq)
    if price_change_pct >= 2.5:
        return 0.22, detail + " — продолжение роста"
    if price_change_pct >= 1.0:
        return 0.48, detail + " — ещё тянут вверх"
    if -0.2 <= price_change_pct < 1.0:
        return 0.82, detail + " — стабилизация у вершины"
    if -1.5 <= price_change_pct < -0.2:
        return 0.90, detail + " — начало отката"
    if -3.0 <= price_change_pct < -1.5:
        return 0.62, detail + " — откат уже идёт"
    return 0.40, detail + " — поздно, ход частично отыгран"


def _score_oi_flow(
    oi_change_pct: float | None,
    *,
    predict_long: bool,
) -> tuple[float, str]:
    if oi_change_pct is None:
        return 0.50, "нет данных OI"
    detail = f"OI {oi_change_pct:+.1f}% с момента кластера"
    if predict_long:
        if oi_change_pct <= -5.0:
            return 0.92, detail + " — капитуляция, позиции смыты"
        if oi_change_pct <= -2.0:
            return 0.78, detail + " — закрытие лонгов"
        if oi_change_pct >= 2.0:
            return 0.32, detail + " — набор новых позиций против отскока"
        return 0.55, detail + " — нейтральный поток"
    if oi_change_pct <= -5.0:
        return 0.92, detail + " — шорты закрывают, топливо кончилось"
    if oi_change_pct <= -2.0:
        return 0.78, detail + " — сокращение OI"
    if oi_change_pct >= 2.0:
        return 0.32, detail + " — набор шортов против отката"
    return 0.55, detail + " — нейтральный поток"


def _score_funding(funding_rate: float | None, *, predict_long: bool) -> tuple[float, str]:
    if funding_rate is None:
        return 0.50, "нет funding"
    pct = funding_rate * 100.0
    detail = f"funding {pct:+.3f}%"
    if predict_long:
        if pct >= 0.05:
            return 0.88, detail + " — перегрев лонгов"
        if pct >= 0.02:
            return 0.72, detail + " — лонги платят"
        if pct <= -0.02:
            return 0.38, detail + " — шорты платят, слабый контртренд"
        return 0.55, detail + " — нейтрально"
    if pct <= -0.05:
        return 0.88, detail + " — перегрев шортов"
    if pct <= -0.02:
        return 0.72, detail + " — шорты платят"
    if pct >= 0.02:
        return 0.38, detail + " — лонги платят, слабый контртренд"
    return 0.55, detail + " — нейтрально"


def _score_structure(
    ms_dict: dict[str, object] | None,
    *,
    predict_long: bool,
) -> tuple[float, str]:
    if not isinstance(ms_dict, dict):
        return 0.50, "структура: нет данных"
    phase = str(ms_dict.get("phase", "neutral"))
    narrative = str(ms_dict.get("oi_narrative", "mixed"))
    phase_label = str(ms_dict.get("phase_label", phase))
    narrative_label = str(ms_dict.get("oi_narrative_label", narrative))
    detail = f"{phase_label} · {narrative_label}"

    if predict_long:
        phase_scores = {
            "impulse_down": 0.25,
            "post_crash_weak": 0.55,
            "correction_down": 0.72,
            "consolidation": 0.68,
            "breakout_setup": 0.60,
            "correction_up": 0.45,
            "impulse_up": 0.30,
            "neutral": 0.52,
        }
        narrative_bonus = {
            "capitulation": 0.18,
            "long_unwind": 0.12,
            "squeeze_risk": 0.10,
            "aligned_short": 0.08,
            "shorts_building": -0.15,
            "aligned_long": -0.10,
        }
        score = phase_scores.get(phase, 0.50) + narrative_bonus.get(narrative, 0.0)
        if ms_dict.get("post_crash"):
            score += 0.06
        if ms_dict.get("dead_cat_bounce"):
            score -= 0.12
        return _clamp(score, 0.0, 1.0), detail
    phase_scores = {
        "impulse_up": 0.25,
        "correction_up": 0.72,
        "consolidation": 0.68,
        "breakout_setup": 0.58,
        "correction_down": 0.45,
        "impulse_down": 0.30,
        "post_crash_weak": 0.40,
        "neutral": 0.52,
    }
    narrative_bonus = {
        "squeeze_risk": 0.15,
        "aligned_long": 0.08,
        "capitulation": -0.08,
        "aligned_short": -0.10,
        "shorts_building": 0.06,
    }
    score = phase_scores.get(phase, 0.50) + narrative_bonus.get(narrative, 0.0)
    return _clamp(score, 0.0, 1.0), detail


def _score_btc(btc_change_pct: float | None, *, predict_long: bool) -> tuple[float, str]:
    if btc_change_pct is None:
        return 0.50, "BTC: нет данных"
    detail = f"BTC {btc_change_pct:+.2f}% за 5м"
    if predict_long:
        if btc_change_pct <= -0.8:
            return 0.28, detail + " — рынок слабый"
        if btc_change_pct <= -0.3:
            return 0.45, detail + " — BTC давит"
        if btc_change_pct >= 0.3:
            return 0.78, detail + " — BTC поддерживает"
        return 0.58, detail + " — BTC нейтрален"
    if btc_change_pct >= 0.8:
        return 0.28, detail + " — рынок сильный вверх"
    if btc_change_pct >= 0.3:
        return 0.45, detail + " — BTC тянет вверх"
    if btc_change_pct <= -0.3:
        return 0.78, detail + " — BTC слабеет"
    return 0.58, detail + " — BTC нейтрален"


def _estimate_window(confidence: float) -> tuple[int, int]:
    if confidence >= 75.0:
        return 5, 20
    if confidence >= 65.0:
        return 10, 30
    return 15, 45


def _invalidation_level(
    cluster_price: float,
    *,
    predict_long: bool,
    recent_low: float | None,
    recent_high: float | None,
) -> tuple[float, str]:
    if predict_long:
        level = recent_low if recent_low and recent_low < cluster_price else cluster_price * 0.992
        return level, f"пробой ${level:.4g} вниз → слив продолжается"
    level = recent_high if recent_high and recent_high > cluster_price else cluster_price * 1.008
    return level, f"пробой ${level:.4g} вверх → рост продолжается"


class LiquidationAnalysisEngine:
    """Post-liquidation разбор для отдельного аналитического чата."""

    def __init__(
        self,
        settings_getter: Callable[[], object],
        scanner: SignalEngine,
        liquidation_tracker: BybitLiquidationTracker | None,
        on_dispatch: Callable[[LiquidationAnalysisResult], Awaitable[None]],
        *,
        weights_getter: Callable[[], Awaitable[dict[str, float] | None]] | None = None,
    ) -> None:
        self._get_settings = settings_getter
        self._scanner = scanner
        self._liquidation_tracker = liquidation_tracker
        self._binance_liquidation_tracker: BinanceLiquidationTracker | None = None
        self._weights_getter = weights_getter
        self._on_dispatch = on_dispatch
        self._kline_cache = BybitKlineCache()
        self._account_ratio_cache = BybitAccountRatioCache()
        self._cooldown_until: dict[str, float] = {}
        self._pending_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def attach_liquidation_tracker(self, tracker: BybitLiquidationTracker) -> None:
        self._liquidation_tracker = tracker

    def attach_binance_liquidation_tracker(self, tracker: BinanceLiquidationTracker) -> None:
        self._binance_liquidation_tracker = tracker

    def enabled(self) -> bool:
        settings = self._get_settings()
        return bool(getattr(settings, "analysis_enabled", True))

    async def schedule(
        self,
        event: LiquidationAlertEvent,
        event_count: int,
        total_usd: float,
    ) -> None:
        if not self.enabled():
            return
        settings = self._get_settings()
        min_usd = float(getattr(settings, "analysis_min_liq_usd", 80_000.0))
        if total_usd < min_usd:
            return

        symbol = event.symbol.upper()
        now = time.time()
        cooldown = int(getattr(settings, "analysis_cooldown_seconds", 1800))
        async with self._lock:
            if now < self._cooldown_until.get(symbol, 0.0):
                return
            prev = self._pending_tasks.get(symbol)
            if prev is not None and not prev.done():
                prev.cancel()
            task = asyncio.create_task(
                self._run_delayed(event, event_count, total_usd, cooldown),
                name=f"liq-analysis-{symbol}",
            )
            self._pending_tasks[symbol] = task

    async def _run_delayed(
        self,
        event: LiquidationAlertEvent,
        event_count: int,
        total_usd: float,
        cooldown: int,
    ) -> None:
        settings = self._get_settings()
        delay = int(getattr(settings, "analysis_delay_seconds", 90))
        symbol = event.symbol.upper()
        try:
            await asyncio.sleep(delay)
            result = await self._build_analysis(event, event_count, total_usd)
            if result is None:
                return
            min_conf = float(getattr(settings, "analysis_min_confidence", 60.0))
            if result.confidence < min_conf:
                logger.info(
                    "Analysis skip %s: confidence %.0f%% < %.0f%%",
                    symbol,
                    result.confidence,
                    min_conf,
                )
                return
            async with self._lock:
                self._cooldown_until[symbol] = time.time() + cooldown
            await self._on_dispatch(result)
            logger.info(
                "Analysis sent %s %s conf=%.0f%% dir=%s",
                event.exchange,
                symbol,
                result.confidence,
                result.direction,
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Liquidation analysis failed for %s", symbol)
        finally:
            async with self._lock:
                if self._pending_tasks.get(symbol) is asyncio.current_task():
                    self._pending_tasks.pop(symbol, None)

    async def _build_analysis(
        self,
        event: LiquidationAlertEvent,
        event_count: int,
        total_usd: float,
    ) -> LiquidationAnalysisResult | None:
        settings = self._get_settings()
        min_usd = float(getattr(settings, "analysis_min_liq_usd", 80_000.0))
        symbol = event.symbol.upper()
        exchange = event.exchange
        cluster_side = event.side
        predict_long = cluster_side == SIDE_LONG_LIQ

        metrics = self._scanner.get_metrics_since(exchange, symbol, event.timestamp)
        current_price = metrics.get("current_price")
        if current_price is None or current_price <= 0:
            snap = self._scanner.get_snapshot_for(exchange, symbol)
            if snap is None or snap.price is None:
                logger.debug("Analysis skip %s: no price", symbol)
                return None
            current_price = snap.price
            metrics = self._scanner.get_metrics_since(exchange, symbol, event.timestamp)

        price_change_pct = float(metrics.get("price_change_pct") or 0.0)
        oi_change_pct = metrics.get("oi_change_pct")
        if isinstance(oi_change_pct, (int, float)):
            oi_change_pct = float(oi_change_pct)
        else:
            oi_change_pct = None

        funding_rate = metrics.get("funding_rate")
        if isinstance(funding_rate, (int, float)):
            funding_rate = float(funding_rate)
        else:
            funding_rate = None

        factor_weights = dict(FACTOR_WEIGHTS)
        if self._weights_getter is not None:
            adaptive = await self._weights_getter()
            if adaptive:
                for key, weight in adaptive.items():
                    if key in factor_weights:
                        factor_weights[key] = weight
                weight_total = sum(factor_weights.values())
                if weight_total > 0:
                    factor_weights = {k: v / weight_total for k, v in factor_weights.items()}

        factors: list[AnalysisFactor] = []

        cluster_score, cluster_detail = _score_cluster(total_usd, event_count, min_usd)
        factors.append(AnalysisFactor("cluster", "Кластер", cluster_score, factor_weights["cluster"], cluster_detail))

        long_liq_15 = 0.0
        short_liq_15 = 0.0
        liq_window = 15
        liq_tracker = (
            self._binance_liquidation_tracker
            if "binance" in exchange.lower()
            else self._liquidation_tracker
        )
        if liq_tracker is not None:
            stats = liq_tracker.get_stats(symbol, window_minutes=liq_window)
            long_liq_15 = stats.long_liq_usd
            short_liq_15 = stats.short_liq_usd
            if stats.total_usd < total_usd * 0.5:
                long_liq_15 += total_usd if cluster_side == SIDE_LONG_LIQ else 0.0
                short_liq_15 += total_usd if cluster_side == SIDE_SHORT_LIQ else 0.0
        else:
            if cluster_side == SIDE_LONG_LIQ:
                long_liq_15 = total_usd
            else:
                short_liq_15 = total_usd

        liq_score, liq_detail = _liquidation_strength(
            long_liq_15,
            short_liq_15,
            predict_long,
            window_minutes=liq_window,
        )
        factors.append(
            AnalysisFactor("liq_imbalance", "Ликвидации 15м", liq_score, factor_weights["liq_imbalance"], liq_detail)
        )

        post_score, post_detail = _score_post_price(cluster_side, price_change_pct, predict_long=predict_long)
        factors.append(AnalysisFactor("post_price", "Реакция цены", post_score, factor_weights["post_price"], post_detail))

        oi_score, oi_detail = _score_oi_flow(oi_change_pct, predict_long=predict_long)
        factors.append(AnalysisFactor("oi_flow", "Поток OI", oi_score, factor_weights["oi_flow"], oi_detail))

        fund_score, fund_detail = _score_funding(funding_rate, predict_long=predict_long)
        factors.append(AnalysisFactor("funding", "Funding", fund_score, factor_weights["funding"], fund_detail))

        account_ratio = await self._account_ratio_cache.get_ratio(symbol)
        ls_score, ls_detail = _long_short_strength(
            account_ratio.long_short_ratio if account_ratio else None,
            account_ratio.buy_ratio if account_ratio else None,
            account_ratio.sell_ratio if account_ratio else None,
            predict_long,
        )
        factors.append(AnalysisFactor("crowd_ls", "Толпа L/S", ls_score, factor_weights["crowd_ls"], ls_detail))

        ms_dict: dict[str, object] | None = None
        klines = await self._kline_cache.get_klines(symbol, limit=OI_BAR_MAX_COUNT)
        oi_bars: list[FiveMinOiBar] = self._scanner.get_five_min_oi_bars("Bybit", symbol)
        if not oi_bars:
            oi_bars = self._scanner.get_five_min_oi_bars(exchange, symbol)
        if klines:
            ms_ctx = analyze_market_structure(
                klines,
                oi_bars,
                is_long=predict_long,
                hours=int(getattr(self._get_settings(), "market_structure_hours", 5)),
            )
            ms_dict = ms_ctx.to_dict()
        struct_score, struct_detail = _score_structure(ms_dict, predict_long=predict_long)
        factors.append(AnalysisFactor("structure", "Структура", struct_score, factor_weights["structure"], struct_detail))

        btc_change = self._scanner.get_btc_change_percent(5)
        btc_score, btc_detail = _score_btc(btc_change, predict_long=predict_long)
        factors.append(AnalysisFactor("btc", "BTC контекст", btc_score, factor_weights["btc"], btc_detail))

        weight_sum = sum(f.weight for f in factors)
        confidence = sum(f.score * f.weight for f in factors) / weight_sum * 100.0

        continuation_risk = (
            (predict_long and post_score < 0.35 and struct_score < 0.35)
            or (not predict_long and post_score < 0.35 and struct_score < 0.35)
        )
        if continuation_risk:
            confidence = min(confidence, 58.0)

        window_min, window_max = _estimate_window(confidence)
        low_high = self._scanner.get_price_extremes_since(exchange, symbol, event.timestamp)
        inv_price, inv_label = _invalidation_level(
            event.price,
            predict_long=predict_long,
            recent_low=low_high.get("low"),
            recent_high=low_high.get("high"),
        )

        direction = "long" if predict_long else "short"
        direction_label = "↗️ отскок вверх" if predict_long else "↘️ откат вниз"

        return LiquidationAnalysisResult(
            symbol=symbol,
            exchange=exchange,
            cluster_side=cluster_side,
            cluster_usd=total_usd,
            cluster_events=event_count,
            cluster_price=event.price,
            cluster_time=event.timestamp,
            current_price=current_price,
            price_change_since_cluster_pct=price_change_pct,
            oi_change_since_cluster_pct=oi_change_pct,
            direction=direction,
            direction_label=direction_label,
            confidence=round(confidence, 1),
            window_min=window_min,
            window_max=window_max,
            invalidation_price=inv_price,
            invalidation_label=inv_label,
            factors=factors,
            continuation_risk=continuation_risk,
        )


def format_liquidation_analysis(result: LiquidationAnalysisResult) -> str:
    ticker = base_ticker(result.symbol)
    exchange_key = "bybit" if "bybit" in result.exchange.lower() else "binance"
    ex_emoji = "⚫" if exchange_key == "bybit" else "🟡"
    ex_name = "ByBit" if exchange_key == "bybit" else "Binance"
    cg_url = coinglass_url(result.symbol, result.exchange)
    ex_url = exchange_trade_url(result.symbol, result.exchange)

    is_long_liq = result.cluster_side == SIDE_LONG_LIQ
    liq_emoji = "🔴" if is_long_liq else "🟢"
    liq_label = "лонги" if is_long_liq else "шорты"
    usd_text = f"${int(round(result.cluster_usd)):,}".replace(",", "")

    oi_part = ""
    if result.oi_change_since_cluster_pct is not None:
        oi_part = f", OI <b>{result.oi_change_since_cluster_pct:+.1f}%</b>"

    lines = [
        f"🧠 <b>АНАЛИЗ</b> · <a href=\"{cg_url}\">#{ticker}</a> · {ex_emoji} {ex_name}",
        "",
        (
            f"{liq_emoji} Смылили <b>{liq_label}</b>: <b>{usd_text}</b> "
            f"({result.cluster_events} событий)"
        ),
        (
            f"Цена: <b>{result.price_change_since_cluster_pct:+.2f}%</b> от кластера"
            f"{oi_part} · сейчас <b>${result.current_price:.4g}</b>"
        ),
        "",
        (
            f"📊 <b>Вердикт:</b> {result.direction_label} · "
            f"<b>{result.confidence:.0f}%</b>"
        ),
        f"⏱ <b>Окно:</b> {result.window_min}–{result.window_max} мин",
        "",
        "<b>Почему:</b>",
    ]

    sorted_factors = sorted(result.factors, key=lambda f: f.score * f.weight, reverse=True)
    for factor in sorted_factors[:5]:
        pct = int(round(factor.score * 100))
        bar = "🟢" if factor.score >= 0.65 else ("🟡" if factor.score >= 0.45 else "🔴")
        lines.append(f"{bar} {factor.label} ({pct}%): <i>{factor.detail}</i>")

    lines.append("")
    lines.append(f"⚠️ <b>Отмена:</b> {result.invalidation_label}")

    if result.continuation_risk:
        lines.append(
            "⚡ <i>Риск продолжения тренда — вход только с подтверждением</i>"
        )

    lines.append(
        f'\n<a href="{ex_url}">Торговать</a> · <a href="{cg_url}">CoinGlass</a>'
    )
    return "\n".join(lines)
