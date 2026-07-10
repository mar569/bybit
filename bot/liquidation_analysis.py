from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .bybit_klines import BybitKlineCache
from .bybit_liquidations import BybitLiquidationTracker
from .binance_liquidations import BinanceLiquidationTracker
from .bybit_market_data import BybitAccountRatioCache
from .bybit_cvd import get_taker_cvd_cache
from .liquidation_alerts import (
    SIDE_LONG_LIQ,
    SIDE_SHORT_LIQ,
    LiquidationAlertEvent,
    base_ticker,
    coinglass_url,
    exchange_trade_url,
)
from .market_structure import FiveMinOiBar
from .probability_engine import _liquidation_strength, _long_short_strength
from .scanner_engine import SignalEngine
from .symbol_tiers import SymbolTier, classify_symbol
from .trend_liq_context import (
    ScenarioVerdict,
    build_trend_liq_context,
    resolve_scenario,
    score_trend_liq_factors,
)
from .models import Signal

logger = logging.getLogger(__name__)

OI_BAR_MAX_COUNT = 72

ANALYSIS_SIGNAL_TYPES = frozenset({
    "impulse_pump",
    "impulse_dump",
    "trend_pump",
    "trend_dump",
    "reversal_pump",
    "reversal_dump",
    "liq_cascade_pump",
    "liq_cascade_dump",
})


def resolve_analysis_min_liq_usd(
    symbol: str,
    settings: object,
    *,
    in_top_n: bool = True,
) -> tuple[float | None, str]:
    """Порог liq для разбора — не выше tier-порогов REKT-алертов."""
    tier = classify_symbol(symbol.upper(), settings, in_top_n=in_top_n)
    if bool(getattr(settings, "analysis_skip_alt_tier", False)) and tier == SymbolTier.ALT:
        return None, "alt_tier"
    alt_min = float(getattr(settings, "liquidation_alt_min_usd", 10_000.0))
    mid_min = float(getattr(settings, "liquidation_mid_min_usd", 10_000.0))
    major_min = float(getattr(settings, "liquidation_min_usd", 10_000.0))
    if tier == SymbolTier.MAJOR:
        cap = float(getattr(settings, "analysis_major_min_liq_usd", 10_000.0))
        return min(cap, major_min), ""
    if tier == SymbolTier.ALT:
        cap = float(getattr(settings, "analysis_alt_min_liq_usd", 10_000.0))
        return min(cap, alt_min), ""
    cap = float(getattr(settings, "analysis_min_liq_usd", 10_000.0))
    return min(cap, mid_min), ""


def _analysis_prefilter(
    scanner: SignalEngine,
    exchange: str,
    symbol: str,
    settings: object,
    total_usd: float,
    *,
    source: str = "liq_alert",
    event_count: int | None = None,
) -> str | None:
    """Причина отказа или None если можно планировать разбор."""
    in_top = True
    try:
        in_top = scanner._is_in_top_n(exchange, symbol)
    except Exception:
        in_top = True

    if source == "signal":
        if bool(getattr(settings, "analysis_skip_alt_tier", False)):
            tier = classify_symbol(symbol.upper(), settings, in_top_n=in_top)
            if tier == SymbolTier.ALT:
                return "alt_tier"
        min_liq = float(getattr(settings, "analysis_signal_min_liq_usd", 20_000.0))
        if total_usd < min_liq:
            return f"liq_${total_usd:.0f}_lt_{min_liq:.0f}"
        return None

    min_liq, skip = resolve_analysis_min_liq_usd(symbol, settings, in_top_n=in_top)
    if min_liq is None:
        return skip or "tier"
    if total_usd < min_liq:
        return f"liq_${total_usd:.0f}_lt_{min_liq:.0f}"

    min_oi = float(getattr(settings, "analysis_min_oi_usd", 0.0))
    if min_oi > 0:
        oi_usd = scanner.get_symbol_oi_usd(symbol)
        if oi_usd is not None and oi_usd < min_oi:
            return f"oi_${oi_usd:.0f}_lt_{min_oi:.0f}"

    min_events = int(getattr(settings, "analysis_min_cluster_events", 1))
    min_single = float(getattr(settings, "analysis_single_event_min_usd", 25_000.0))
    if (
        source == "liq_alert"
        and event_count is not None
        and min_events > 1
        and event_count < min_events
        and total_usd < min_single
    ):
        return f"cluster_{event_count}ev_lt_{min_single:.0f}"
    return None


def _finalize_verdict(
    ctx: object,
    verdict: ScenarioVerdict,
    oi_change_pct: float | None,
) -> ScenarioVerdict:
    """Слабый тренд / нет OI → выжидание вместо полного молчания."""
    if verdict.direction not in ("long", "short"):
        return verdict
    trend_quality = getattr(ctx, "trend_quality", "weak")
    oi_narrative = getattr(ctx, "oi_narrative", "")
    has_oi_delta = oi_change_pct is not None and abs(oi_change_pct) >= 0.8
    if trend_quality == "weak":
        return ScenarioVerdict(
            "wait",
            "⏸ выжидание",
            "слабый тренд — наблюдай, не входи по направлению",
            continuation_up=False,
            continuation_down=False,
        )
    if oi_narrative == "insufficient_oi" and not has_oi_delta:
        return ScenarioVerdict(
            "wait",
            "⏸ выжидание",
            "мало данных OI — жди подтверждения на графике",
            continuation_up=False,
            continuation_down=False,
        )
    return verdict


def refine_analysis_scenario(
    ctx: object,
    verdict: ScenarioVerdict,
    cluster_side: str,
    price_change_pct: float,
    *,
    long_liq_15: float,
    short_liq_15: float,
) -> tuple[ScenarioVerdict, str, bool]:
    """После обвала / каскад лонгов — не давать «чистый шорт» на опоздании."""
    total = long_liq_15 + short_liq_15
    long_share = long_liq_15 / total if total > 0 else 0.0
    cascade_note = ""
    if total >= 80_000 and long_share >= 0.65 and long_liq_15 >= 50_000:
        cascade_note = f"каскад лонгов ${long_liq_15 / 1000:.0f}K за 15м — давление вниз"

    trend_quality = getattr(ctx, "trend_quality", "weak")
    late_dump = trend_quality in {"strong_down", "moderate_down"} and price_change_pct <= -8.0
    if not late_dump:
        return verdict, cascade_note, False

    if cluster_side == SIDE_SHORT_LIQ:
        if long_share >= 0.6 and long_liq_15 >= 80_000:
            return (
                ScenarioVerdict(
                    "wait",
                    "⏸ после обвала",
                    "каскад лонгов — падение уже отыграно, не гонись за шортом",
                    continuation_up=False,
                    continuation_down=False,
                ),
                cascade_note,
                True,
            )
        if verdict.direction == "short":
            return (
                ScenarioVerdict(
                    "wait",
                    "⏸ после обвала",
                    f"цена уже {price_change_pct:+.1f}% от кластера — поздно для шорта, жди отскок",
                    continuation_up=False,
                    continuation_down=False,
                ),
                cascade_note,
                True,
            )

    if cluster_side == SIDE_LONG_LIQ and long_share >= 0.55:
        return (
            ScenarioVerdict(
                "wait",
                "⏸ после обвала",
                "лонги ещё смывают — жди стабилизации перед отскоком",
                continuation_up=False,
                continuation_down=False,
            ),
            cascade_note,
            True,
        )

    return verdict, cascade_note, late_dump


FACTOR_WEIGHTS: dict[str, float] = {
    "cluster": 0.14,
    "liq_imbalance": 0.18,
    "trend": 0.20,
    "cvd": 0.16,
    "oi_narrative": 0.12,
    "correction": 0.08,
    "post_price": 0.08,
    "crowd_ls": 0.04,
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
    trend_label: str = ""
    scenario_text: str = ""
    is_correction: bool = False
    cvd_source: str = "proxy"
    post_dump_late: bool = False
    liq_cascade_note: str = ""


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
    is_correction: bool,
) -> tuple[float, str]:
    detail = f"цена {price_change_pct:+.2f}% от кластера"
    if is_correction:
        if -3.5 < price_change_pct < 0.5 and cluster_side == SIDE_SHORT_LIQ:
            return 0.82, detail + " — коррекция после смыва шортов"
        if -0.5 < price_change_pct < 3.5 and cluster_side == SIDE_LONG_LIQ:
            return 0.82, detail + " — стабилизация после смыва лонгов"
    if predict_long:
        if price_change_pct <= -2.5:
            return 0.22, detail + " — продолжение слива"
        if -1.0 < price_change_pct <= 0.5:
            return 0.78, detail + " — удержание у дна"
        if 0.5 < price_change_pct <= 2.0:
            return 0.72, detail + " — отскок идёт"
        return 0.50, detail
    if price_change_pct >= 2.5:
        return 0.22, detail + " — продолжение роста"
    if -0.5 <= price_change_pct < 1.0:
        return 0.78, detail + " — удержание у вершины"
    if -2.0 <= price_change_pct < -0.5:
        return 0.72, detail + " — откат идёт"
    return 0.50, detail


def _estimate_window(confidence: float) -> tuple[int, int]:
    if confidence >= 75.0:
        return 15, 60
    if confidence >= 65.0:
        return 30, 120
    return 60, 240


def _invalidation_level(
    cluster_price: float,
    *,
    predict_long: bool,
    recent_low: float | None,
    recent_high: float | None,
) -> tuple[float, str]:
    if predict_long:
        level = recent_low if recent_low and recent_low < cluster_price else cluster_price * 0.992
        return level, f"пробой ${level:.4g} вниз → сценарий отменён"
    level = recent_high if recent_high and recent_high > cluster_price else cluster_price * 1.008
    return level, f"пробой ${level:.4g} вверх → сценарий отменён"


class LiquidationAnalysisEngine:
    """Разбор: тренд → кластер ликвидаций → коррекция → OI/CVD (без funding)."""

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
        self._hourly_sent: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._stats: dict[str, int] = {
            "scheduled": 0,
            "sent": 0,
            "skipped_threshold": 0,
            "skipped_alt_tier": 0,
            "skipped_oi": 0,
            "skipped_trend": 0,
            "skipped_price_move": 0,
            "skipped_no_price": 0,
            "skipped_confidence": 0,
            "skipped_rate_limit": 0,
            "skipped_cooldown": 0,
            "from_signal": 0,
            "errors": 0,
        }

    def get_diagnostics(self) -> dict[str, int | float]:
        settings = self._get_settings()
        pending = sum(1 for t in self._pending_tasks.values() if t is not None and not t.done())
        return {
            **self._stats,
            "pending": pending,
            "analysis_min_liq_usd": float(getattr(settings, "analysis_min_liq_usd", 30_000.0)),
            "analysis_min_confidence": float(getattr(settings, "analysis_min_confidence", 62.0)),
            "analysis_delay_seconds": int(getattr(settings, "analysis_delay_seconds", 90)),
        }

    def attach_liquidation_tracker(self, tracker: BybitLiquidationTracker) -> None:
        self._liquidation_tracker = tracker

    def attach_binance_liquidation_tracker(self, tracker: BinanceLiquidationTracker) -> None:
        self._binance_liquidation_tracker = tracker

    def enabled(self) -> bool:
        settings = self._get_settings()
        return bool(getattr(settings, "analysis_enabled", True))

    def _can_send_global(self, settings: object, *, confidence: float) -> bool:
        max_h = int(getattr(settings, "analysis_max_per_hour", 4))
        now = time.time()
        while self._hourly_sent and self._hourly_sent[0] < now - 3600:
            self._hourly_sent.popleft()
        if len(self._hourly_sent) < max_h:
            return True
        return confidence >= 78.0

    def _record_sent(self) -> None:
        self._hourly_sent.append(time.time())

    async def schedule(
        self,
        event: LiquidationAlertEvent,
        event_count: int,
        total_usd: float,
        *,
        source: str = "liq_alert",
        skip_trend_gate: bool = False,
    ) -> None:
        if not self.enabled():
            return
        settings = self._get_settings()
        symbol = event.symbol.upper()

        reject = _analysis_prefilter(
            self._scanner,
            event.exchange,
            symbol,
            settings,
            total_usd,
            source=source,
            event_count=event_count,
        )
        if reject:
            if reject == "alt_tier":
                self._stats["skipped_alt_tier"] += 1
            elif reject.startswith("oi_"):
                self._stats["skipped_oi"] += 1
            else:
                self._stats["skipped_threshold"] += 1
            logger.info(
                "Analysis skip %s [%s]: %s (cluster $%.0f)",
                symbol,
                source,
                reject,
                total_usd,
            )
            return

        now = time.time()
        cooldown = int(getattr(settings, "analysis_cooldown_seconds", 3600))
        async with self._lock:
            if now < self._cooldown_until.get(symbol, 0.0):
                self._stats["skipped_cooldown"] += 1
                return
            prev = self._pending_tasks.get(symbol)
            if prev is not None and not prev.done():
                prev.cancel()
            self._stats["scheduled"] += 1
            if source == "signal":
                self._stats["from_signal"] += 1
            task = asyncio.create_task(
                self._run_delayed(
                    event,
                    event_count,
                    total_usd,
                    cooldown,
                    skip_trend_gate=skip_trend_gate,
                ),
                name=f"liq-analysis-{symbol}",
            )
            self._pending_tasks[symbol] = task

    async def schedule_from_signal(self, signal: Signal) -> None:
        """Разбор по импульсу/развороту + liq из трекера (не ждём REKT-алерт)."""
        settings = self._get_settings()
        if not self.enabled():
            return
        if not bool(getattr(settings, "analysis_signal_trigger_enabled", True)):
            return
        if signal.signal_type not in ANALYSIS_SIGNAL_TYPES:
            return

        symbol = signal.symbol.upper()
        exchange = signal.exchange
        liq_tracker = (
            self._binance_liquidation_tracker
            if "binance" in exchange.lower()
            else self._liquidation_tracker
        )
        stats = None
        if liq_tracker is not None:
            stats = liq_tracker.get_stats(symbol, window_minutes=15)

        long_liq = stats.long_liq_usd if stats else 0.0
        short_liq = stats.short_liq_usd if stats else 0.0
        total_liq = long_liq + short_liq
        min_liq = float(getattr(settings, "analysis_signal_min_liq_usd", 30_000.0))
        if total_liq < min_liq:
            return
        cluster_usd = total_liq

        if signal.side == "long":
            cluster_side = SIDE_LONG_LIQ if long_liq >= short_liq else SIDE_SHORT_LIQ
        else:
            cluster_side = SIDE_SHORT_LIQ if short_liq >= long_liq else SIDE_LONG_LIQ
        if total_liq <= 0:
            cluster_side = SIDE_LONG_LIQ if signal.side == "long" else SIDE_SHORT_LIQ

        event_count = stats.event_count if stats and stats.event_count > 0 else 1
        event = LiquidationAlertEvent(
            exchange=exchange,
            timestamp=time.time(),
            symbol=symbol,
            side=cluster_side,
            usd_value=cluster_usd,
            price=float(signal.current_price or 0.0),
        )
        await self.schedule(
            event,
            event_count,
            cluster_usd,
            source="signal",
            skip_trend_gate=signal.signal_type in {"liq_cascade_pump", "liq_cascade_dump"},
        )

    async def _run_delayed(
        self,
        event: LiquidationAlertEvent,
        event_count: int,
        total_usd: float,
        cooldown: int,
        *,
        skip_trend_gate: bool = False,
    ) -> None:
        settings = self._get_settings()
        delay = int(getattr(settings, "analysis_delay_seconds", 90))
        symbol = event.symbol.upper()
        try:
            await asyncio.sleep(delay)
            result = await self._build_analysis(
                event,
                event_count,
                total_usd,
                skip_trend_gate=skip_trend_gate,
            )
            if result is None:
                logger.info("Analysis skip %s: build returned None", symbol)
                return
            if result.direction == "wait":
                min_conf = float(getattr(settings, "analysis_min_confidence_wait", 42.0))
            elif result.direction in ("long", "short"):
                min_conf = float(
                    getattr(settings, "analysis_min_confidence_directional", 58.0)
                )
            else:
                min_conf = float(getattr(settings, "analysis_min_confidence", 48.0))
            if result.confidence < min_conf:
                self._stats["skipped_confidence"] += 1
                logger.info(
                    "Analysis skip %s: confidence %.0f%% < %.0f%%",
                    symbol,
                    result.confidence,
                    min_conf,
                )
                return
            min_move = float(getattr(settings, "analysis_min_price_move_pct", 0.0))
            if (
                min_move > 0
                and not result.is_correction
                and abs(result.price_change_since_cluster_pct) < min_move
            ):
                self._stats["skipped_price_move"] += 1
                return
            if not self._can_send_global(settings, confidence=result.confidence):
                self._stats["skipped_rate_limit"] += 1
                logger.info("Analysis skip %s: hourly limit", symbol)
                return
            async with self._lock:
                self._cooldown_until[symbol] = time.time() + cooldown
            await self._on_dispatch(result)
            self._record_sent()
            self._stats["sent"] += 1
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
            self._stats["errors"] += 1
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
        *,
        skip_trend_gate: bool = False,
    ) -> LiquidationAnalysisResult | None:
        settings = self._get_settings()
        symbol = event.symbol.upper()
        exchange = event.exchange
        cluster_side = event.side

        min_usd, _ = resolve_analysis_min_liq_usd(
            symbol,
            settings,
            in_top_n=self._scanner._is_in_top_n(exchange, symbol),
        )
        if min_usd is None:
            min_usd = float(getattr(settings, "analysis_min_liq_usd", 30_000.0))

        metrics = self._scanner.get_metrics_since(exchange, symbol, event.timestamp)
        current_price = metrics.get("current_price")
        if current_price is None or current_price <= 0:
            snap = self._scanner.get_snapshot_for(exchange, symbol)
            if snap is not None and snap.price:
                current_price = snap.price
            else:
                snap_bybit = self._scanner.get_snapshot_for("Bybit", symbol)
                if snap_bybit is not None and snap_bybit.price:
                    current_price = snap_bybit.price
                    exchange = "Bybit"

        klines = await self._kline_cache.get_klines(symbol, limit=OI_BAR_MAX_COUNT)
        if current_price is None or current_price <= 0:
            if klines:
                current_price = klines[-1].close
            else:
                self._stats["skipped_no_price"] += 1
                return None

        price_change_pct = float(metrics.get("price_change_pct") or 0.0)
        if event.price > 0:
            price_change_pct = (current_price - event.price) / event.price * 100.0

        oi_change_pct = metrics.get("oi_change_pct")
        if isinstance(oi_change_pct, (int, float)):
            oi_change_pct = float(oi_change_pct)
        else:
            oi_change_pct = None

        oi_bars: list[FiveMinOiBar] = self._scanner.get_five_min_oi_bars("Bybit", symbol)
        if not oi_bars:
            oi_bars = self._scanner.get_five_min_oi_bars(exchange, symbol)

        taker_cvd = None
        try:
            taker_cvd = await get_taker_cvd_cache().get_cvd(symbol, lookback_minutes=60.0)
        except Exception:
            logger.debug("Taker CVD for analysis %s failed", symbol, exc_info=True)

        ctx = build_trend_liq_context(
            klines,
            oi_bars,
            cluster_side=cluster_side,
            price_change_since_cluster_pct=price_change_pct,
            taker_cvd=taker_cvd,
        )
        if ctx is None:
            self._stats["skipped_trend"] += 1
            return None

        require_trend = bool(getattr(settings, "analysis_require_trend", True)) and not skip_trend_gate
        min_trend = float(getattr(settings, "analysis_min_trend_pct", 1.5))
        force_liq = float(getattr(settings, "analysis_force_liq_usd", 50_000.0))
        trend_strength = max(abs(ctx.trend_pct_1h), abs(ctx.trend_pct_4h))
        if require_trend and trend_strength < min_trend and total_usd < force_liq:
            self._stats["skipped_trend"] += 1
            logger.info(
                "Analysis skip %s: weak trend 1h=%.1f%% 4h=%.1f%% (liq $%.0f)",
                symbol,
                ctx.trend_pct_1h,
                ctx.trend_pct_4h,
                total_usd,
            )
            return None

        account_ratio = await self._account_ratio_cache.get_ratio(symbol)
        buy_ratio = account_ratio.buy_ratio if account_ratio else None

        verdict = resolve_scenario(
            ctx,
            cluster_side,
            price_change_since_cluster_pct=price_change_pct,
            oi_change_pct=oi_change_pct,
            buy_ratio=buy_ratio,
        )
        verdict = _finalize_verdict(ctx, verdict, oi_change_pct)

        predict_long = verdict.direction == "long"
        if verdict.direction == "wait":
            predict_long = cluster_side == SIDE_LONG_LIQ

        long_liq_15 = 0.0
        short_liq_15 = 0.0
        liq_tracker = (
            self._binance_liquidation_tracker
            if "binance" in exchange.lower()
            else self._liquidation_tracker
        )
        if liq_tracker is not None:
            stats = liq_tracker.get_stats(symbol, window_minutes=15)
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

        verdict, liq_cascade_note, post_dump_late = refine_analysis_scenario(
            ctx,
            verdict,
            cluster_side,
            price_change_pct,
            long_liq_15=long_liq_15,
            short_liq_15=short_liq_15,
        )
        if verdict.direction == "wait":
            predict_long = cluster_side == SIDE_LONG_LIQ

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
        factors.append(
            AnalysisFactor("cluster", "Кластер liq", cluster_score, factor_weights["cluster"], cluster_detail)
        )

        liq_score, liq_detail = _liquidation_strength(
            long_liq_15,
            short_liq_15,
            predict_long,
            window_minutes=15,
        )
        factors.append(
            AnalysisFactor(
                "liq_imbalance", "Ликвидации 15м", liq_score, factor_weights["liq_imbalance"], liq_detail
            )
        )

        for key, label, score, detail in score_trend_liq_factors(
            ctx, cluster_side, predict_long=predict_long,
        ):
            weight = factor_weights.get(key, 0.1)
            factors.append(AnalysisFactor(key, label, score, weight, detail))

        post_score, post_detail = _score_post_price(
            cluster_side,
            price_change_pct,
            predict_long=predict_long,
            is_correction=ctx.is_correction,
        )
        factors.append(
            AnalysisFactor("post_price", "Коррекция", post_score, factor_weights["post_price"], post_detail)
        )

        ls_score, ls_detail = _long_short_strength(
            account_ratio.long_short_ratio if account_ratio else None,
            buy_ratio,
            account_ratio.sell_ratio if account_ratio else None,
            predict_long,
        )
        factors.append(
            AnalysisFactor("crowd_ls", "Дисбаланс L/S", ls_score, factor_weights["crowd_ls"], ls_detail)
        )

        weight_sum = sum(f.weight for f in factors)
        confidence = sum(f.score * f.weight for f in factors) / weight_sum * 100.0

        if verdict.direction == "wait":
            confidence *= 0.95
        elif ctx.near_high and cluster_side == SIDE_SHORT_LIQ and verdict.direction == "long":
            confidence *= 0.80

        continuation_risk = (
            verdict.direction == "wait"
            or (ctx.near_high and cluster_side == SIDE_SHORT_LIQ)
        )

        window_min, window_max = _estimate_window(confidence)
        low_high = self._scanner.get_price_extremes_since(exchange, symbol, event.timestamp)
        inv_price, inv_label = _invalidation_level(
            event.price,
            predict_long=predict_long,
            recent_low=low_high.get("low"),
            recent_high=low_high.get("high"),
        )

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
            direction=verdict.direction,
            direction_label=verdict.direction_label,
            confidence=round(confidence, 1),
            window_min=window_min,
            window_max=window_max,
            invalidation_price=inv_price,
            invalidation_label=inv_label,
            factors=factors,
            continuation_risk=continuation_risk,
            trend_label=ctx.trend_label,
            scenario_text=verdict.scenario_text,
            is_correction=ctx.is_correction,
            cvd_source=ctx.cvd_source,
            post_dump_late=post_dump_late,
            liq_cascade_note=liq_cascade_note,
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
        oi_part = f" · OI <b>{result.oi_change_since_cluster_pct:+.1f}%</b>"

    phase_note = " · <i>коррекция</i>" if result.is_correction else ""

    lines = [
        f"🧠 <b>АНАЛИЗ</b> · <a href=\"{cg_url}\">#{ticker}</a> · {ex_emoji} {ex_name}",
        "",
        f"📈 <b>Тренд:</b> {result.trend_label}",
        (
            f"{liq_emoji} Смылили <b>{liq_label}</b>: <b>{usd_text}</b> "
            f"({result.cluster_events} событий)"
        ),
        (
            f"Цена <b>{result.price_change_since_cluster_pct:+.2f}%</b> от кластера"
            f"{oi_part}{phase_note} · <b>${result.current_price:.4g}</b>"
        ),
        "",
        f"📊 <b>Сценарий:</b> {result.direction_label} · <b>{result.confidence:.0f}%</b>",
        f"<i>{result.scenario_text}</i>",
    ]
    if result.liq_cascade_note:
        lines.append(f"💥 <b>Ликвидации:</b> {result.liq_cascade_note}")
    if result.post_dump_late:
        lines.append("⚠️ <b>После обвала</b> — вход опоздал, не гонись за движением")
    lines.extend([
        f"⏱ Окно наблюдения: {result.window_min}–{result.window_max} мин",
        "",
        "<b>Факторы (OI / CVD / liq):</b>",
    ])

    sorted_factors = sorted(result.factors, key=lambda f: f.score * f.weight, reverse=True)
    for factor in sorted_factors[:6]:
        pct = int(round(factor.score * 100))
        bar = "🟢" if factor.score >= 0.65 else ("🟡" if factor.score >= 0.45 else "🔴")
        lines.append(f"{bar} {factor.label} ({pct}%): <i>{factor.detail}</i>")

    lines.append("")
    lines.append(f"⚠️ <b>Отмена:</b> {result.invalidation_label}")

    if result.direction == "wait":
        if result.post_dump_late:
            lines.append("⏸ <i>Жди отскок или новый пробой — сейчас не входи</i>")
        else:
            lines.append("⏸ <i>Нет чёткого edge — не входи по этому разбору</i>")
    elif result.continuation_risk:
        lines.append("⚠️ <i>Риск ложного сценария — только с подтверждением на графике</i>")

    lines.append(
        f'\n<a href="{ex_url}">Торговать</a> · <a href="{cg_url}">CoinGlass</a>'
    )
    return "\n".join(lines)
