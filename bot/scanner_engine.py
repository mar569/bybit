from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .models import Signal, SnapshotPoint
from .bybit_klines import BybitKlineCache
from .bybit_market_data import BybitAccountRatioCache
from .bybit_liquidations import BybitLiquidationTracker
from .market_structure import FiveMinOiBar, analyze_market_structure, bar_open_time
from .smc_analysis import analyze_smc, smc_to_dict
from .probability_engine import PROBABILITY_BYPASS_TYPES, assess_signal_probability
from .settings import ExchangeThresholds, ScannerSettings, SettingsManager
from .symbol_tiers import TierThresholds, tier_thresholds
from .trend_exhaustion import TrendExhaustionRisk, detect_trend_exhaustion, detect_trend_exhaustion_risk
from .anomaly_alerts import AnomalyBatcher, detect_anomaly_for_symbol

HISTORY_MAX_POINTS = 3600
OI_BAR_INTERVAL_SECONDS = 300
OI_BAR_MAX_COUNT = 72  # 6 часов по 5 минут

logger = logging.getLogger(__name__)

_OI_FLOW_BYPASS_TYPES = frozenset({
    "mega_pump", "mega_dump", "vertical_pump", "vertical_dump",
    "reversal_pump", "reversal_dump", "liq_cascade_pump", "liq_cascade_dump",
    "impulse_pump", "impulse_dump", "price_pump", "price_dump",
    "trend_dump", "trend_pump",
})


def _evaluate_oi_flow(
    *,
    oi_change_usd: float | None,
    price_change_percent: float,
    tier: TierThresholds,
    settings: ScannerSettings,
    signal_type: str,
) -> tuple[bool, bool]:
    """
    Приток OI: полный порог tier или мягкий диапазон при сильном % движении.
    Returns: (passes, weak_flow).
    """
    if signal_type in _OI_FLOW_BYPASS_TYPES:
        return True, False
    if oi_change_usd is None:
        return True, False
    oi_flow = abs(oi_change_usd)
    min_flow = tier.min_oi_change_usd
    if oi_flow >= min_flow:
        return True, False
    soft_floor = float(getattr(settings, "min_oi_change_soft_usd", 0.0))
    if soft_floor <= 0 or oi_flow < soft_floor:
        return False, False
    price_pct = abs(price_change_percent)
    price_thr = tier.price_rise_percent if price_change_percent >= 0 else tier.price_drop_percent
    strong_mult = float(getattr(settings, "min_oi_change_strong_price_mult", 1.35))
    if price_pct >= price_thr * strong_mult:
        return True, True
    return False, False


@dataclass
class SignalCandidate:
    signal_type: str
    period_minutes: int
    oi_change_percent: float
    price_change_percent: float
    earlier: SnapshotPoint
    urgency: int
    flash_tier: float | None = None
    breakout_meta: dict[str, float] | None = None


@dataclass
class MarketChanges:
    oi_change_percent: float
    price_change_percent: float
    volume_change_percent: float
    oi_change_value: float
    oi_change_usd: float | None
    lookback_seconds: int


def format_oi_usd(value: float | None) -> str:
    if value is None:
        return "—"
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{abs_value / 1_000_000:.2f} млн. $"
    if abs_value >= 1_000:
        return f"{abs_value / 1_000:.1f} тыс. $"
    return f"{abs_value:.0f} $"


class SignalEngine:
    def __init__(
        self,
        settings: SettingsManager,
        on_signal: Callable[[Signal], Awaitable[None]],
        *,
        on_trend_risk: Callable[[TrendExhaustionRisk, str, str], Awaitable[None]] | None = None,
        liquidation_tracker: BybitLiquidationTracker | None = None,
        binance_liquidation_tracker: object | None = None,
    ) -> None:
        self.settings = settings
        self.on_signal = on_signal
        self.on_trend_risk = on_trend_risk
        self._liquidation_tracker = liquidation_tracker
        self._binance_liquidation_tracker = binance_liquidation_tracker
        self.history: dict[str, deque[SnapshotPoint]] = {}
        self.last_signal_time: dict[str, float] = {}
        self._last_trend_risk_time: dict[str, float] = {}
        self.daily_signal_counts: dict[str, int] = {}
        self._daily_reset_key: str | None = None
        self._volumes: dict[str, float] = {}
        self._top_symbols: dict[str, set[str]] = {}
        self._last_top_refresh = 0.0
        self._dirty_keys: set[str] = set()
        self._btc_history: deque[SnapshotPoint] = deque(maxlen=HISTORY_MAX_POINTS)
        self._five_min_bars: dict[str, deque[FiveMinOiBar]] = {}
        self._kline_cache = BybitKlineCache()
        self._account_ratio_cache = BybitAccountRatioCache()
        self._anomaly_batcher: AnomalyBatcher | None = None
        self.lock = asyncio.Lock()

    def attach_anomaly_batcher(self, batcher: AnomalyBatcher) -> None:
        self._anomaly_batcher = batcher

    def attach_liquidation_tracker(self, tracker: BybitLiquidationTracker) -> None:
        self._liquidation_tracker = tracker

    def attach_binance_liquidation_tracker(self, tracker: object) -> None:
        self._binance_liquidation_tracker = tracker

    def _get_liquidation_stats(
        self,
        exchange: str,
        symbol: str,
        window_minutes: int,
    ):
        key = exchange.lower()
        if "bybit" in key and self._liquidation_tracker is not None:
            return self._liquidation_tracker.get_stats(symbol, window_minutes=window_minutes)
        if "binance" in key and self._binance_liquidation_tracker is not None:
            return self._binance_liquidation_tracker.get_stats(symbol, window_minutes=window_minutes)
        return None

    @staticmethod
    def _symbol_cooldown_key(symbol: str) -> str:
        """Один cooldown на тикер — не дублировать Binance+Bybit и mega/pulse/pump."""
        return f"sym:{symbol.upper()}"

    def get_bybit_top_symbols(self) -> list[str]:
        self._refresh_top_symbols_if_needed()
        top = self._top_symbols.get("Bybit")
        if top:
            return sorted(top)
        return [
            key.split(":", 1)[1]
            for key in self._volumes
            if key.startswith("Bybit:") and self._volumes[key] > 0
        ][: self.settings.settings.top_n_symbols or 150]

    def _reset_daily_counts_if_needed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_key != today:
            self.daily_signal_counts.clear()
            self._daily_reset_key = today

    def _is_exchange_enabled(self, exchange: str) -> bool:
        settings = self.settings.settings
        key = exchange.lower()
        if "binance" in key:
            return settings.enabled_binance
        if "bybit" in key:
            return settings.enabled_bybit
        return True

    def _refresh_top_symbols_if_needed(self) -> None:
        settings = self.settings.settings
        if not settings.top_n_symbols:
            self._top_symbols = {}
            return

        now = time.time()
        if now - self._last_top_refresh < 60:
            return
        self._last_top_refresh = now

        for exchange in ("Binance", "Bybit"):
            items = [
                (key, volume)
                for key, volume in self._volumes.items()
                if key.startswith(f"{exchange}:") and volume > 0
            ]
            items.sort(key=lambda item: item[1], reverse=True)
            self._top_symbols[exchange] = {
                key.split(":", 1)[1] for key, _ in items[: settings.top_n_symbols]
            }

    def _is_in_top_n(self, exchange: str, symbol: str) -> bool:
        settings = self.settings.settings
        if not settings.top_n_symbols:
            return True
        top = self._top_symbols.get(exchange)
        if not top:
            return True
        return symbol in top

    def get_symbol_oi_usd(self, symbol: str) -> float | None:
        symbol = symbol.upper()
        for exchange in ("Bybit", "Binance"):
            snap = self.get_snapshot_for(exchange, symbol)
            if snap is None:
                continue
            value = self._oi_usd_value(
                snap.open_interest, snap.price, snap.additional,
            )
            if value is not None and value > 0:
                return value
        return None

    async def update_snapshot(
        self,
        exchange: str,
        symbol: str,
        price: float | None,
        open_interest: float | None,
        volume_24h: float | None,
        bid_price: float | None,
        ask_price: float | None,
        timestamp: float | None = None,
        additional: dict[str, object] | None = None,
    ) -> None:
        if not self._is_exchange_enabled(exchange):
            return
        if price is None and open_interest is None:
            return
        if price is None or open_interest is None or price <= 0 or open_interest <= 0:
            return

        now = timestamp or time.time()
        key = f"{exchange}:{symbol}"
        if volume_24h is not None:
            self._volumes[key] = volume_24h
        self._refresh_top_symbols_if_needed()

        point = SnapshotPoint(
            timestamp=now,
            price=price,
            open_interest=open_interest,
            volume_24h=volume_24h,
            bid_price=bid_price,
            ask_price=ask_price,
            additional=additional or {},
        )

        async with self.lock:
            history = self.history.setdefault(key, deque(maxlen=HISTORY_MAX_POINTS))
            history.append(point)
            if symbol == "BTCUSDT":
                self._btc_history.append(point)
            self._update_five_min_bar(key, point)

        self._dirty_keys.add(key)

    def _update_five_min_bar(self, key: str, point: SnapshotPoint) -> None:
        if point.open_interest is None or point.price is None:
            return
        if point.open_interest <= 0 or point.price <= 0:
            return

        open_time = bar_open_time(point.timestamp, OI_BAR_INTERVAL_SECONDS)
        bars = self._five_min_bars.setdefault(key, deque(maxlen=OI_BAR_MAX_COUNT))

        if bars and bars[-1].open_time == open_time:
            bar = bars[-1]
            bar.oi_high = max(bar.oi_high, point.open_interest)
            bar.oi_low = min(bar.oi_low, point.open_interest)
            bar.oi_close = point.open_interest
            bar.price_close = point.price
            bar.samples += 1
            return

        bars.append(FiveMinOiBar(
            open_time=open_time,
            oi_open=point.open_interest,
            oi_high=point.open_interest,
            oi_low=point.open_interest,
            oi_close=point.open_interest,
            price_close=point.price,
            samples=1,
        ))

    def get_snapshot_for(self, exchange: str, symbol: str) -> SnapshotPoint | None:
        history = self.history.get(f"{exchange}:{symbol}")
        if not history:
            return None
        return history[-1]

    def get_five_min_oi_bars(self, exchange: str, symbol: str) -> list[FiveMinOiBar]:
        return list(self._five_min_bars.get(f"{exchange}:{symbol}", []))

    def get_metrics_since(self, exchange: str, symbol: str, since_ts: float) -> dict[str, float | None]:
        key = f"{exchange}:{symbol}"
        history = self.history.get(key)
        if not history:
            return {"current_price": None, "price_change_pct": None, "oi_change_pct": None, "funding_rate": None}

        current = history[-1]
        current_price = current.price
        funding_rate = self._extract_funding(current.additional)

        baseline = None
        for point in reversed(history):
            if point.timestamp <= since_ts and point.price:
                baseline = point
                break
        if baseline is None:
            baseline = history[0]

        price_change_pct = None
        if current_price and baseline.price:
            price_change_pct = self._percent_change(baseline.price, current_price)

        oi_change_pct = None
        if current.open_interest and baseline.open_interest and baseline.open_interest > 0:
            oi_change_pct = self._percent_change(baseline.open_interest, current.open_interest)

        return {
            "current_price": current_price,
            "price_change_pct": price_change_pct,
            "oi_change_pct": oi_change_pct,
            "funding_rate": funding_rate,
        }

    def get_price_extremes_since(
        self,
        exchange: str,
        symbol: str,
        since_ts: float,
    ) -> dict[str, float | None]:
        history = self.history.get(f"{exchange}:{symbol}")
        if not history:
            return {"low": None, "high": None}
        prices = [
            p.price for p in history
            if p.timestamp >= since_ts and p.price is not None
        ]
        if not prices:
            return {"low": None, "high": None}
        return {"low": min(prices), "high": max(prices)}

    @staticmethod
    def _extract_funding(additional: dict[str, object]) -> float | None:
        raw = additional.get("funding_rate")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def get_btc_change_percent(self, minutes: int = 5) -> float | None:
        if len(self._btc_history) < 2:
            return None
        current = self._btc_history[-1]
        if current.price is None or current.timestamp is None:
            return None
        cutoff = current.timestamp - minutes * 60
        if self._btc_history[0].timestamp > cutoff:
            return None
        earlier = next(
            (p for p in reversed(self._btc_history) if p.timestamp <= cutoff and p.price),
            None,
        )
        if earlier is None or earlier.price is None:
            return None
        return self._percent_change(earlier.price, current.price)

    async def run_evaluation_loop(self, interval: float = 2.0) -> None:
        while True:
            await asyncio.sleep(interval)
            keys = list(self._dirty_keys)
            self._dirty_keys.clear()
            for key in keys:
                parts = key.split(":", 1)
                if len(parts) != 2:
                    continue
                await self._evaluate_signals(key, parts[0], parts[1])

    def get_diagnostics(self) -> dict[str, object]:
        settings = self.settings.settings
        pairs_tracked = len(self.history)
        pairs_ready = 0
        pairs_with_oi = 0
        max_history = 0

        for key, history in self.history.items():
            if not history:
                continue
            max_history = max(max_history, len(history))
            current = history[-1]
            if current.open_interest and current.open_interest > 0:
                pairs_with_oi += 1
            exchange = key.split(":", 1)[0]
            thresholds = settings.for_exchange(exchange)
            min_period = min(
                thresholds.long_period_minutes,
                thresholds.short_period_minutes,
                settings.pulse_period_minutes,
                min(settings.flash_window_minutes) if settings.flash_window_minutes else 5,
            )
            lookback_seconds = min_period * 60
            cutoff = current.timestamp - lookback_seconds
            if len(history) >= 2 and history[0].timestamp <= cutoff:
                if current.price and current.open_interest:
                    pairs_ready += 1

        return {
            "signals_enabled": settings.signals_enabled,
            "oi_period_minutes": settings.oi_period_minutes,
            "long_period_minutes": settings.long_period_minutes,
            "short_period_minutes": settings.short_period_minutes,
            "pulse_period_minutes": settings.pulse_period_minutes,
            "oi_rise_percent": settings.oi_rise_percent,
            "price_rise_percent": settings.price_rise_percent,
            "min_open_interest": settings.min_open_interest,
            "min_signal_score": settings.min_signal_score,
            "top_n_symbols": settings.top_n_symbols,
            "pairs_tracked": pairs_tracked,
            "pairs_with_oi": pairs_with_oi,
            "pairs_ready": pairs_ready,
            "max_history_points": max_history,
            "dirty_queue": len(self._dirty_keys),
        }

    async def _evaluate_signals(self, key: str, exchange: str, symbol: str) -> None:
        async with self.lock:
            history = self.history.get(key)
            if not history or len(history) < 2:
                return
            current = history[-1]
            settings = self.settings.settings
            if current.open_interest is None or current.price is None:
                return
            thresholds = settings.for_exchange(exchange)
            in_top = self._is_in_top_n(exchange, symbol)
            tier = tier_thresholds(symbol, settings, thresholds, in_top_n=in_top)

        await self._maybe_dispatch_anomaly(exchange, symbol, history, current, settings, tier)
        await self._maybe_dispatch_trend_risk(
            exchange, symbol, history, current, settings, tier,
        )

        if not settings.signals_enabled:
            return

        async with self.lock:
            history = self.history.get(key)
            if not history or len(history) < 2:
                return
            current = history[-1]
            settings = self.settings.settings
            thresholds = settings.for_exchange(exchange)
            if current.open_interest is None or current.price is None:
                return

            in_top = self._is_in_top_n(exchange, symbol)
            tier = tier_thresholds(symbol, settings, thresholds, in_top_n=in_top)
            effective_thresholds = ExchangeThresholds(
                long_period_minutes=thresholds.long_period_minutes,
                short_period_minutes=thresholds.short_period_minutes,
                oi_rise_percent=tier.oi_rise_percent,
                oi_drop_percent=tier.oi_drop_percent,
                price_rise_percent=tier.price_rise_percent,
                price_drop_percent=tier.price_drop_percent,
            )

            candidate = self._pick_best_candidate(
                history, current, settings, effective_thresholds, exchange, symbol, tier
            )
            if candidate is None:
                return

            if (
                candidate.signal_type == "reversal_pump"
                and settings.reversal_block_long_after_dump
            ):
                dump_pct = self._max_drop_in_window(
                    history,
                    current,
                    settings.reversal_block_dump_window_minutes,
                )
                if dump_pct <= -settings.reversal_block_min_dump_pct:
                    logger.info(
                        "Blocked reversal_pump %s %s: dump %.1f%% in %dm window",
                        exchange,
                        symbol,
                        dump_pct,
                        settings.reversal_block_dump_window_minutes,
                    )
                    return

            earlier = candidate.earlier
            changes = self._compute_changes(current, earlier, candidate.period_minutes * 60)

            oi_usd_now = self._oi_usd_value(current.open_interest, current.price, current.additional)
            is_breakout = candidate.signal_type in {"vertical_pump", "vertical_dump"}
            is_reversal = candidate.signal_type in {"reversal_pump", "reversal_dump"}
            is_liq_cascade = candidate.signal_type in {"liq_cascade_pump", "liq_cascade_dump"}
            is_impulse = candidate.signal_type in {"impulse_pump", "impulse_dump"}
            is_trend_ex = candidate.signal_type in {"trend_dump", "trend_pump"}
            min_liquidity = tier.min_open_interest_usd
            if is_breakout:
                min_liquidity = max(min_liquidity, settings.breakout_min_liquidity_oi_usd)
            elif is_reversal:
                min_liquidity = max(min_liquidity, settings.reversal_min_liquidity_oi_usd)
            elif is_impulse:
                min_liquidity = max(min_liquidity, settings.impulse_min_liquidity_oi_usd)
            elif is_trend_ex:
                min_liquidity = max(
                    min_liquidity,
                    float(getattr(settings, "trend_exhaustion_min_liquidity_oi_usd", 120_000.0)),
                )
            if oi_usd_now is None or oi_usd_now < min_liquidity:
                return

            if settings.min_volume > 0 and (current.volume_24h or 0.0) < settings.min_volume:
                return

            is_mega = candidate.signal_type in {"mega_pump", "mega_dump"}
            weak_oi_flow = False
            if (
                not is_mega
                and not is_breakout
                and not is_reversal
                and not is_liq_cascade
                and not is_impulse
                and not is_trend_ex
            ):
                flow_ok, weak_oi_flow = _evaluate_oi_flow(
                    oi_change_usd=changes.oi_change_usd,
                    price_change_percent=changes.price_change_percent,
                    tier=tier,
                    settings=settings,
                    signal_type=candidate.signal_type,
                )
                if not flow_ok:
                    return

            now = time.time()
            symbol_cd_key = self._symbol_cooldown_key(symbol)
            symbol_cd = settings.signal_cooldown_seconds
            last_symbol = self.last_signal_time.get(symbol_cd_key, 0.0)
            if now - last_symbol < symbol_cd:
                return

            if is_breakout:
                cooldown_key = f"{key}:breakout"
                cooldown = settings.breakout_cooldown_seconds
            elif is_reversal:
                cooldown_key = f"{key}:reversal"
                cooldown = settings.reversal_cooldown_seconds
            elif is_liq_cascade:
                cooldown_key = f"{key}:liq_cascade"
                cooldown = settings.liq_cascade_cooldown_seconds
            elif is_impulse:
                cooldown_key = f"{key}:impulse"
                cooldown = settings.impulse_cooldown_seconds
            elif is_trend_ex:
                cooldown_key = f"{key}:trend_exhaustion"
                cooldown = int(getattr(settings, "trend_exhaustion_cooldown_seconds", 180))
            elif is_mega:
                cooldown_key = f"{key}:mega"
                cooldown = settings.mega_cooldown_seconds
            else:
                cooldown_key = key
                cooldown = settings.signal_cooldown_seconds
            last_time = self.last_signal_time.get(cooldown_key, 0.0)
            if now - last_time < cooldown:
                return

            if is_breakout or is_reversal or is_liq_cascade or is_impulse or is_trend_ex:
                if is_impulse:
                    tier_hit = float((candidate.breakout_meta or {}).get("impulse_tier_pct", 0))
                    score = 1 if tier_hit >= 10 else 2
                elif is_trend_ex:
                    te_score = float((candidate.breakout_meta or {}).get("trend_exhaustion_score", 0))
                    leg = abs(float((candidate.breakout_meta or {}).get("trend_leg_pct", 0)))
                    score = 1 if leg >= 8.0 or te_score >= 2.5 else 2
                else:
                    score = 1 if (is_breakout or is_liq_cascade) else (
                        1 if float((candidate.breakout_meta or {}).get("reversal_peak_age_min", 99)) <= 2.5 else 2
                    )
            else:
                score = self._calculate_score(
                    changes.oi_change_percent,
                    changes.price_change_percent,
                    changes.volume_change_percent,
                    candidate.signal_type,
                    flash_tier=candidate.flash_tier,
                )

            min_score = (
                tier.min_signal_score
                if settings.tier_enabled
                else settings.min_signal_score
            )
            if weak_oi_flow:
                min_score += 1.0
            if (
                not is_breakout
                and not is_reversal
                and not is_liq_cascade
                and not is_impulse
                and not is_trend_ex
                and min_score
                and score < min_score
            ):
                return

            lookback_seconds = changes.lookback_seconds
            interval_volume = None
            try:
                prev_vol = history[-2].volume_24h if len(history) >= 2 else None
                if current.volume_24h is not None and prev_vol is not None:
                    interval_volume = max(0.0, current.volume_24h - prev_vol)
            except Exception:
                interval_volume = None

            spread = self._compute_spread(current.bid_price, current.ask_price)
            funding_rate = self._extract_funding(current.additional)
            price_speed = self._safe_div(changes.price_change_percent, max(lookback_seconds / 60.0, 1.0))
            oi_direction = self._direction(changes.oi_change_percent)
            price_direction = self._direction(changes.price_change_percent)
            indicators = self._compute_indicators(history, lookback_seconds)

            vol_spike = False
            try:
                deltas = []
                prev = None
                for p in list(history):
                    if prev is not None and p.volume_24h is not None and prev.volume_24h is not None:
                        delta = p.volume_24h - prev.volume_24h
                        if delta > 0:
                            deltas.append(delta)
                    prev = p
                avg_interval_vol = sum(deltas) / len(deltas) if deltas else None
                if interval_volume is not None and avg_interval_vol and avg_interval_vol > 0:
                    if interval_volume >= settings.volume_spike_multiplier * avg_interval_vol:
                        vol_spike = True
            except Exception:
                vol_spike = False

            self._reset_daily_counts_if_needed()
            self.daily_signal_counts[key] = self.daily_signal_counts.get(key, 0) + 1
            self.last_signal_time[cooldown_key] = now
            self.last_signal_time[symbol_cd_key] = now
            side = self._determine_side(changes.price_change_percent, candidate.signal_type)

            market_structure_dict = None
            smc_dict = None
            account_ratio_dict = None
            liquidations_dict = None
            if "bybit" in exchange.lower():
                if settings.market_structure_enabled:
                    klines = await self._kline_cache.get_klines(symbol, limit=OI_BAR_MAX_COUNT)
                    oi_bars = list(self._five_min_bars.get(key, []))
                    ms_ctx = analyze_market_structure(
                        klines,
                        oi_bars,
                        is_long=(side == "long"),
                        hours=settings.market_structure_hours,
                    )
                    market_structure_dict = ms_ctx.to_dict()
                    htf_klines = await self._kline_cache.get_klines(
                        symbol, limit=48, interval_minutes=60,
                    )
                    smc_ctx = analyze_smc(
                        klines, htf_bars=htf_klines or None, interval_minutes=5,
                    )
                    smc_dict = smc_to_dict(smc_ctx)

                ratio = await self._account_ratio_cache.get_ratio(symbol)
                if ratio is not None:
                    account_ratio_dict = {
                        "buy_ratio": round(ratio.buy_ratio, 4),
                        "sell_ratio": round(ratio.sell_ratio, 4),
                        "long_short_ratio": ratio.long_short_ratio,
                        "period": ratio.period,
                        "long_pct": round(ratio.buy_ratio * 100, 1),
                        "short_pct": round(ratio.sell_ratio * 100, 1),
                    }

                if self._liquidation_tracker is not None:
                    liq_stats = self._liquidation_tracker.get_stats(symbol, window_minutes=15)
                    liquidations_dict = liq_stats.to_dict()

            liq_estimate = None
            if liquidations_dict:
                liq_estimate = float(liquidations_dict.get("total_usd", 0) or 0)

            signal = Signal(
                exchange=exchange,
                symbol=symbol,
                signal_type=candidate.signal_type,
                oi_period_minutes=candidate.period_minutes,
                oi_change_percent=round(changes.oi_change_percent, 2),
                oi_change_value=round(changes.oi_change_value, 2),
                oi_change_usd=round(changes.oi_change_usd, 2) if changes.oi_change_usd is not None else None,
                oi_direction=oi_direction,
                price_change_percent=round(changes.price_change_percent, 2),
                price_change_value=round(current.price - earlier.price, 6),
                price_direction=price_direction,
                volume_change_percent=round(changes.volume_change_percent, 2),
                trade_count=int(current.additional.get("trade_count")) if current.additional.get("trade_count") is not None else None,
                spread=spread,
                funding_rate=funding_rate,
                vwap=indicators.get("vwap"),
                atr=indicators.get("atr"),
                rsi=indicators.get("rsi"),
                ema_short=indicators.get("ema_short"),
                ema_long=indicators.get("ema_long"),
                liquidation_estimate=liq_estimate if liq_estimate else indicators.get("liquidation_estimate"),
                volume_24h=current.volume_24h,
                volume_speed=indicators.get("volume_speed"),
                signal_score=min(max(score, 1), 10),
                side=side,
                signals_today=self.daily_signal_counts[key],
                current_price=current.price,
                current_open_interest=current.open_interest,
                link=self._coinglass_url(symbol, exchange),
                details={
                    "price_speed_pct_per_min": round(price_speed, 3),
                    "lookback_seconds": lookback_seconds,
                    "history_points": len(history),
                    "volume_spike": vol_spike,
                    "flash_tier": candidate.flash_tier,
                    "urgency": candidate.urgency,
                    "oi_usd_formatted": format_oi_usd(changes.oi_change_usd),
                    "weak_oi_flow": weak_oi_flow,
                    **(candidate.breakout_meta or {}),
                    **({"symbol_tier": tier.tier.value} if settings.tier_enabled else {}),
                    **({"market_structure": market_structure_dict} if market_structure_dict else {}),
                    **({"smc": smc_dict} if smc_dict else {}),
                    **({"account_ratio": account_ratio_dict} if account_ratio_dict else {}),
                    **({"liquidations": liquidations_dict} if liquidations_dict else {}),
                },
            )

            assessment = assess_signal_probability(
                signal,
                settings,
                thresholds,
                btc_change_percent=self.get_btc_change_percent(5),
                vol_spike=vol_spike,
                market_structure=market_structure_dict,
                smc=smc_dict,
                account_ratio=account_ratio_dict,
                liquidations=liquidations_dict,
            )
            signal.details["probability_percent"] = assessment.percent
            signal.details["probability_verdict"] = assessment.verdict
            signal.details["probability_factors"] = [
                f.to_dict() for f in assessment.factors
            ]

            if settings.probability_filter_enabled:
                bypass = signal.signal_type in PROBABILITY_BYPASS_TYPES
                min_prob = tier.min_probability_percent if settings.tier_enabled else settings.min_probability_percent
                if not bypass and assessment.percent < min_prob:
                    logger.info(
                        "Signal filtered %s %s: probability %.0f%% < %.0f%% (tier %s)",
                        exchange,
                        symbol,
                        assessment.percent,
                        min_prob,
                        tier.tier.value,
                    )
                    return

            logger.info(
                "Signal %s %s %s | OI %.2f%% | price %.2f%% | prob %.0f%% | %dm",
                exchange,
                symbol,
                candidate.signal_type,
                changes.oi_change_percent,
                changes.price_change_percent,
                assessment.percent,
                candidate.period_minutes,
            )

        await self.on_signal(signal)

    @staticmethod
    def _effective_pulse_thresholds(
        settings: ScannerSettings,
        thresholds: ExchangeThresholds,
    ) -> tuple[float, float, float, float, float]:
        oi_up = settings.pulse_oi_rise_percent
        price_up = settings.pulse_price_rise_percent
        oi_down = settings.pulse_oi_drop_percent
        price_down = settings.pulse_price_drop_percent
        squeeze_price = settings.short_squeeze_min_price
        if settings.respect_global_floors:
            oi_up = max(oi_up, thresholds.oi_rise_percent)
            price_up = max(price_up, thresholds.price_rise_percent)
            oi_down = max(oi_down, thresholds.oi_drop_percent)
            price_down = max(price_down, thresholds.price_drop_percent)
            squeeze_price = max(squeeze_price, thresholds.price_rise_percent)
        return oi_up, price_up, oi_down, price_down, squeeze_price

    @staticmethod
    def _effective_flash_thresholds(
        settings: ScannerSettings,
        thresholds: ExchangeThresholds,
    ) -> tuple[tuple[float, ...], float, float]:
        tiers = settings.flash_price_tiers
        min_oi_up = settings.flash_min_oi_rise_percent
        min_oi_down = settings.flash_min_oi_drop_percent
        if settings.respect_global_floors:
            tiers = tuple(
                t for t in tiers
                if t >= thresholds.price_rise_percent
            ) or tiers
            min_oi_up = max(min_oi_up, thresholds.oi_rise_percent)
            min_oi_down = max(min_oi_down, thresholds.oi_drop_percent)
        return tiers, min_oi_up, min_oi_down

    def _detect_vertical_breakout(
        self,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> SignalCandidate | None:
        if not settings.breakout_enabled or current.price is None:
            return None

        now = current.timestamp
        spike_seconds = settings.breakout_spike_minutes * 60
        flat_seconds = settings.breakout_consolidation_minutes * 60
        spike_start = now - spike_seconds
        flat_start = now - flat_seconds

        if history[0].timestamp > flat_start:
            return None

        flat_points = [
            p for p in history
            if flat_start <= p.timestamp < spike_start and p.price is not None
        ]
        if len(flat_points) < 8:
            return None

        flat_prices = [p.price for p in flat_points]
        flat_low = min(flat_prices)
        flat_high = max(flat_prices)
        if flat_low <= 0:
            return None
        flat_range_pct = (flat_high - flat_low) / flat_low * 100.0
        if flat_range_pct > settings.breakout_max_flat_percent:
            return None

        spike_earlier = self._point_at_cutoff(history, spike_start)
        if spike_earlier is None or spike_earlier.price is None:
            return None

        spike_pct = self._percent_change(spike_earlier.price, current.price)
        flat_mid = flat_points[len(flat_points) // 2]
        flat_drift_pct = abs(self._percent_change(flat_mid.price, spike_earlier.price))
        flat_minutes = max((spike_start - flat_start) / 60.0, 1.0)
        spike_minutes = max(settings.breakout_spike_minutes, 1)
        speed_spike = abs(spike_pct) / spike_minutes
        speed_flat = flat_drift_pct / flat_minutes
        if speed_flat < 0.04:
            speed_flat = 0.04
        velocity_ratio = speed_spike / speed_flat
        if velocity_ratio < settings.breakout_velocity_multiplier:
            return None

        spike_points = [p for p in history if p.timestamp >= spike_start and p.price is not None]
        if len(spike_points) >= 4:
            ups = sum(
                1 for i in range(1, len(spike_points))
                if spike_points[i].price > spike_points[i - 1].price
            )
            down_ratio = ups / (len(spike_points) - 1)
            if spike_pct > 0 and down_ratio < 0.55:
                return None
            if spike_pct < 0 and down_ratio > 0.45:
                return None

        oi_pct = self._oi_percent_change(spike_earlier, current)
        meta = {
            "flat_range_percent": round(flat_range_pct, 2),
            "spike_percent": round(spike_pct, 2),
            "velocity_ratio": round(velocity_ratio, 2),
            "consolidation_minutes": float(settings.breakout_consolidation_minutes),
        }

        if spike_pct >= tier.breakout_min_spike_percent:
            return SignalCandidate(
                signal_type="vertical_pump",
                period_minutes=settings.breakout_spike_minutes,
                oi_change_percent=oi_pct,
                price_change_percent=spike_pct,
                earlier=spike_earlier,
                urgency=0,
                breakout_meta=meta,
            )

        if spike_pct <= -tier.breakout_min_dump_percent:
            meta["spike_percent"] = round(spike_pct, 2)
            return SignalCandidate(
                signal_type="vertical_dump",
                period_minutes=settings.breakout_spike_minutes,
                oi_change_percent=oi_pct,
                price_change_percent=spike_pct,
                earlier=spike_earlier,
                urgency=0,
                breakout_meta=meta,
            )

        return None

    def _detect_sharp_reversal(
        self,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> SignalCandidate | None:
        """Памп → резкий слив (или дамп → отскок) без обязательного 25м флета."""
        if not settings.reversal_enabled or current.price is None:
            return None

        now = current.timestamp
        window_sec = settings.reversal_window_minutes * 60
        spike_sec = settings.reversal_spike_minutes * 60
        window_start = now - window_sec
        spike_start = now - spike_sec

        if history[0].timestamp > window_start:
            return None

        window_points = [
            p for p in history
            if p.timestamp >= window_start and p.price is not None and p.price > 0
        ]
        if len(window_points) < 12:
            return None

        spike_earlier = self._point_at_cutoff(history, spike_start)
        if spike_earlier is None or spike_earlier.price is None:
            return None

        spike_pct = self._percent_change(spike_earlier.price, current.price)
        min_rev = tier.reversal_min_reversal_pct
        min_prior = tier.reversal_min_prior_move_pct
        max_peak_age = float(
            getattr(tier, "reversal_peak_max_age_minutes", settings.reversal_peak_max_age_minutes)
        ) * 60.0
        oi_pct = self._oi_percent_change(spike_earlier, current)

        peak_point = max(window_points, key=lambda p: p.price or 0.0)
        trough_point = min(window_points, key=lambda p: p.price or float("inf"))
        peak_price = peak_point.price or 0.0
        trough_price = trough_point.price or 0.0
        if peak_price <= 0 or trough_price <= 0:
            return None

        # --- Разворот вниз: был рост → резкий слив с недавнего хая
        peak_age = now - peak_point.timestamp
        if peak_age <= max_peak_age and peak_point.timestamp >= window_start:
            dump_from_peak = self._percent_change(peak_price, current.price)
            if dump_from_peak <= -min_rev and spike_pct <= -min_rev * 0.85:
                prior_low = trough_price
                if trough_point.timestamp > peak_point.timestamp:
                    prior_points = [p for p in window_points if p.timestamp < peak_point.timestamp]
                    if prior_points:
                        prior_low = min(p.price for p in prior_points if p.price)
                    else:
                        prior_low = window_points[0].price or prior_low
                prior_up = self._percent_change(prior_low, peak_price)
                if prior_up >= min_prior:
                    meta = {
                        "reversal_prior_move_pct": round(prior_up, 2),
                        "reversal_leg_pct": round(dump_from_peak, 2),
                        "reversal_peak_age_min": round(peak_age / 60.0, 1),
                        "spike_percent": round(spike_pct, 2),
                    }
                    return SignalCandidate(
                        signal_type="reversal_dump",
                        period_minutes=settings.reversal_spike_minutes,
                        oi_change_percent=oi_pct,
                        price_change_percent=round(spike_pct, 2),
                        earlier=spike_earlier,
                        urgency=0,
                        breakout_meta=meta,
                    )

        # --- Разворот вверх: было падение → резкий отскок с недавнего дна
        trough_age = now - trough_point.timestamp
        if trough_age <= max_peak_age and trough_point.timestamp >= window_start:
            bounce_from_trough = self._percent_change(trough_price, current.price)
            if bounce_from_trough >= min_rev and spike_pct >= min_rev * 0.85:
                prior_high = peak_price
                if peak_point.timestamp > trough_point.timestamp:
                    prior_points = [p for p in window_points if p.timestamp < trough_point.timestamp]
                    if prior_points:
                        prior_high = max(p.price for p in prior_points if p.price)
                    else:
                        prior_high = window_points[0].price or prior_high
                prior_down = self._percent_change(prior_high, trough_price)
                if prior_down <= -min_prior:
                    meta = {
                        "reversal_prior_move_pct": round(prior_down, 2),
                        "reversal_leg_pct": round(bounce_from_trough, 2),
                        "reversal_peak_age_min": round(trough_age / 60.0, 1),
                        "spike_percent": round(spike_pct, 2),
                    }
                    return SignalCandidate(
                        signal_type="reversal_pump",
                        period_minutes=settings.reversal_spike_minutes,
                        oi_change_percent=oi_pct,
                        price_change_percent=round(spike_pct, 2),
                        earlier=spike_earlier,
                        urgency=0,
                        breakout_meta=meta,
                    )

        return None

    def _detect_sustained_impulse(
        self,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> SignalCandidate | None:
        """Кумулятивный импульс за 15–30 мин — ловит затяжные pump/dump без флета."""
        if not settings.impulse_enabled or current.price is None:
            return None

        tiers = tier.impulse_price_tiers
        if not tiers:
            return None

        best: SignalCandidate | None = None
        for window in settings.impulse_window_minutes:
            earlier = self._point_at_cutoff(history, current.timestamp - window * 60)
            if earlier is None or earlier.price is None or earlier.price <= 0:
                continue
            price_pct = self._percent_change(earlier.price, current.price)
            oi_pct = self._oi_percent_change(earlier, current)

            for tier_pct in sorted(tiers, reverse=True):
                if price_pct >= tier_pct:
                    meta = {
                        "impulse_tier_pct": round(tier_pct, 2),
                        "impulse_window_min": float(window),
                        "impulse_move_pct": round(price_pct, 2),
                    }
                    best = SignalCandidate(
                        signal_type="impulse_pump",
                        period_minutes=window,
                        oi_change_percent=oi_pct,
                        price_change_percent=round(price_pct, 2),
                        earlier=earlier,
                        urgency=0,
                        breakout_meta=meta,
                    )
                    break
                if price_pct <= -tier_pct:
                    meta = {
                        "impulse_tier_pct": round(tier_pct, 2),
                        "impulse_window_min": float(window),
                        "impulse_move_pct": round(price_pct, 2),
                    }
                    best = SignalCandidate(
                        signal_type="impulse_dump",
                        period_minutes=window,
                        oi_change_percent=oi_pct,
                        price_change_percent=round(price_pct, 2),
                        earlier=earlier,
                        urgency=0,
                        breakout_meta=meta,
                    )
                    break
        return best

    @staticmethod
    def _max_drop_in_window(
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        window_minutes: int,
    ) -> float:
        """Максимальное падение от локального хая в окне до текущей цены."""
        if current.price is None:
            return 0.0
        cutoff = current.timestamp - window_minutes * 60
        window_points = [
            p for p in history
            if p.timestamp >= cutoff and p.price is not None and p.price > 0
        ]
        if not window_points:
            return 0.0
        peak_price = max(p.price for p in window_points)
        if peak_price <= 0:
            return 0.0
        return (current.price - peak_price) / peak_price * 100.0

    async def _maybe_dispatch_anomaly(
        self,
        exchange: str,
        symbol: str,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> None:
        if not settings.anomaly_enabled or self._anomaly_batcher is None:
            return
        event = detect_anomaly_for_symbol(exchange, symbol, history, current, settings, tier)
        if event is None:
            return
        await self._anomaly_batcher.offer(event, settings)

    async def _maybe_dispatch_trend_risk(
        self,
        exchange: str,
        symbol: str,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> None:
        if not getattr(settings, "trend_exhaustion_risk_enabled", True):
            return
        if not settings.signals_enabled or self.on_trend_risk is None:
            return
        if self._detect_trend_exhaustion_candidate(
            exchange, symbol, history, current, settings, tier,
        ) is not None:
            return

        liq_long = 0.0
        liq_short = 0.0
        window = int(getattr(settings, "trend_exhaustion_spike_minutes", 5))
        stats = self._get_liquidation_stats(exchange, symbol, max(window, 5))
        if stats is not None:
            liq_long = float(stats.long_liq_usd or 0.0)
            liq_short = float(stats.short_liq_usd or 0.0)

        risk = detect_trend_exhaustion_risk(
            history,
            current,
            settings=settings,
            tier=tier,
            liq_long_usd=liq_long,
            liq_short_usd=liq_short,
        )
        if risk is None:
            return

        now = time.time()
        risk_key = f"{exchange}:{symbol}:{risk.kind}"
        cooldown = int(getattr(settings, "trend_exhaustion_risk_cooldown_seconds", 600))
        last = self._last_trend_risk_time.get(risk_key, 0.0)
        if now - last < cooldown:
            return
        self._last_trend_risk_time[risk_key] = now

        try:
            await self.on_trend_risk(risk, exchange, symbol)
        except Exception:
            logger.exception("Trend risk dispatch failed %s %s", exchange, symbol)

    async def run_anomaly_flush_loop(self, interval: float = 15.0) -> None:
        while True:
            await asyncio.sleep(interval)
            batcher = self._anomaly_batcher
            if batcher is None:
                continue
            try:
                await batcher.flush(self.settings.settings)
            except Exception:
                logger.exception("Anomaly batch flush failed")

    def _detect_liq_cascade(
        self,
        exchange: str,
        symbol: str,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> SignalCandidate | None:
        if not settings.liq_cascade_enabled:
            return None

        window = settings.liq_cascade_window_minutes
        earlier = self._point_at_cutoff(history, current.timestamp - window * 60)
        if earlier is None:
            return None

        changes = self._compute_changes(current, earlier, window * 60)
        stats = self._get_liquidation_stats(exchange, symbol, window)
        if stats is None or stats.total_usd <= 0:
            return None

        imbalance_min = settings.liq_cascade_imbalance_min
        meta = {
            "liq_cascade_long_usd": round(stats.long_liq_usd, 2),
            "liq_cascade_short_usd": round(stats.short_liq_usd, 2),
            "liq_cascade_window_min": float(window),
        }

        if (
            stats.long_liq_usd >= tier.liq_cascade_min_usd
            and stats.long_liq_usd / stats.total_usd >= imbalance_min
            and changes.price_change_percent <= -tier.liq_cascade_min_price_percent
        ):
            return SignalCandidate(
                signal_type="liq_cascade_dump",
                period_minutes=window,
                oi_change_percent=changes.oi_change_percent,
                price_change_percent=changes.price_change_percent,
                earlier=earlier,
                urgency=0,
                breakout_meta=meta,
            )

        if (
            stats.short_liq_usd >= tier.liq_cascade_min_usd
            and stats.short_liq_usd / stats.total_usd >= imbalance_min
            and changes.price_change_percent >= tier.liq_cascade_min_price_percent
        ):
            return SignalCandidate(
                signal_type="liq_cascade_pump",
                period_minutes=window,
                oi_change_percent=changes.oi_change_percent,
                price_change_percent=changes.price_change_percent,
                earlier=earlier,
                urgency=0,
                breakout_meta=meta,
            )

        return None

    def _detect_trend_exhaustion_candidate(
        self,
        exchange: str,
        symbol: str,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        tier: TierThresholds,
    ) -> SignalCandidate | None:
        liq_long = 0.0
        liq_short = 0.0
        window = int(getattr(settings, "trend_exhaustion_spike_minutes", 5))
        stats = self._get_liquidation_stats(exchange, symbol, max(window, 5))
        if stats is not None:
            liq_long = float(stats.long_liq_usd or 0.0)
            liq_short = float(stats.short_liq_usd or 0.0)

        hit = detect_trend_exhaustion(
            history,
            current,
            settings=settings,
            tier=tier,
            liq_long_usd=liq_long,
            liq_short_usd=liq_short,
        )
        if hit is None:
            return None
        return SignalCandidate(
            signal_type=hit.signal_type,
            period_minutes=hit.period_minutes,
            oi_change_percent=hit.oi_change_percent,
            price_change_percent=hit.price_change_percent,
            earlier=hit.earlier,
            urgency=0,
            breakout_meta=hit.meta,
        )

    def _pick_best_candidate(
        self,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        thresholds: ExchangeThresholds,
        exchange: str,
        symbol: str,
        tier: TierThresholds,
    ) -> SignalCandidate | None:
        liq_cascade = self._detect_liq_cascade(
            exchange, symbol, history, current, settings, tier
        )
        if liq_cascade is not None:
            return liq_cascade

        trend_ex = self._detect_trend_exhaustion_candidate(
            exchange, symbol, history, current, settings, tier,
        )
        if trend_ex is not None:
            return trend_ex

        impulse = self._detect_sustained_impulse(history, current, settings, tier)
        if impulse is not None:
            return impulse

        breakout = self._detect_vertical_breakout(history, current, settings, tier)
        if breakout is not None:
            return breakout

        reversal = self._detect_sharp_reversal(history, current, settings, tier)
        if reversal is not None:
            return reversal

        candidates: list[SignalCandidate] = []
        pulse_oi_up, pulse_price_up, pulse_oi_down, pulse_price_down, squeeze_min = (
            self._effective_pulse_thresholds(settings, thresholds)
        )
        flash_tiers, flash_min_oi_up, flash_min_oi_down = self._effective_flash_thresholds(
            settings, thresholds
        )

        if settings.flash_enabled and flash_tiers:
            for window in settings.flash_window_minutes:
                earlier = self._point_at_cutoff(history, current.timestamp - window * 60)
                if earlier is None:
                    continue
                changes = self._compute_changes(current, earlier, window * 60)
                for tier in sorted(flash_tiers, reverse=True):
                    bypass_oi = tier >= settings.flash_bypass_oi_tier_pct
                    if changes.price_change_percent >= tier:
                        if bypass_oi or changes.oi_change_percent >= flash_min_oi_up:
                            candidates.append(SignalCandidate(
                                signal_type="mega_pump",
                                period_minutes=window,
                                oi_change_percent=changes.oi_change_percent,
                                price_change_percent=changes.price_change_percent,
                                earlier=earlier,
                                urgency=1,
                                flash_tier=tier,
                            ))
                            break
                    if changes.price_change_percent <= -tier:
                        if bypass_oi or changes.oi_change_percent <= -flash_min_oi_down:
                            candidates.append(SignalCandidate(
                                signal_type="mega_dump",
                                period_minutes=window,
                                oi_change_percent=changes.oi_change_percent,
                                price_change_percent=changes.price_change_percent,
                                earlier=earlier,
                                urgency=1,
                                flash_tier=tier,
                            ))
                            break

        pulse_earlier = self._point_at_cutoff(
            history, current.timestamp - settings.pulse_period_minutes * 60
        )
        if pulse_earlier is not None:
            pulse = self._compute_changes(
                current, pulse_earlier, settings.pulse_period_minutes * 60
            )
            if (
                pulse.oi_change_percent >= pulse_oi_up
                and pulse.price_change_percent >= pulse_price_up
            ):
                candidates.append(SignalCandidate(
                    signal_type="pulse_pump",
                    period_minutes=settings.pulse_period_minutes,
                    oi_change_percent=pulse.oi_change_percent,
                    price_change_percent=pulse.price_change_percent,
                    earlier=pulse_earlier,
                    urgency=2,
                ))
            if (
                pulse.oi_change_percent <= -pulse_oi_down
                and pulse.price_change_percent <= -pulse_price_down
            ):
                candidates.append(SignalCandidate(
                    signal_type="pulse_dump",
                    period_minutes=settings.pulse_period_minutes,
                    oi_change_percent=pulse.oi_change_percent,
                    price_change_percent=pulse.price_change_percent,
                    earlier=pulse_earlier,
                    urgency=2,
                ))
            if (
                pulse.price_change_percent >= squeeze_min
                and pulse.oi_change_percent <= settings.short_squeeze_max_oi_change
            ):
                candidates.append(SignalCandidate(
                    signal_type="short_squeeze",
                    period_minutes=settings.pulse_period_minutes,
                    oi_change_percent=pulse.oi_change_percent,
                    price_change_percent=pulse.price_change_percent,
                    earlier=pulse_earlier,
                    urgency=2,
                ))

        long_earlier = self._point_at_cutoff(
            history, current.timestamp - thresholds.long_period_minutes * 60
        )
        if long_earlier is not None:
            long_chg = self._compute_changes(
                current, long_earlier, thresholds.long_period_minutes * 60
            )
            st = self._classify_long_signal(long_chg, thresholds, settings)
            if st:
                candidates.append(SignalCandidate(
                    signal_type=st,
                    period_minutes=thresholds.long_period_minutes,
                    oi_change_percent=long_chg.oi_change_percent,
                    price_change_percent=long_chg.price_change_percent,
                    earlier=long_earlier,
                    urgency=3,
                ))

        short_earlier = self._point_at_cutoff(
            history, current.timestamp - thresholds.short_period_minutes * 60
        )
        if short_earlier is not None:
            short_chg = self._compute_changes(
                current, short_earlier, thresholds.short_period_minutes * 60
            )
            st = self._classify_short_signal(short_chg, thresholds, settings)
            if st:
                candidates.append(SignalCandidate(
                    signal_type=st,
                    period_minutes=thresholds.short_period_minutes,
                    oi_change_percent=short_chg.oi_change_percent,
                    price_change_percent=short_chg.price_change_percent,
                    earlier=short_earlier,
                    urgency=3,
                ))

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                item.urgency,
                -(item.flash_tier or 0),
                -(abs(item.price_change_percent) + abs(item.oi_change_percent)),
            ),
        )
        return candidates[0]

    @staticmethod
    def _point_at_cutoff(
        history: deque[SnapshotPoint],
        cutoff: float,
    ) -> SnapshotPoint | None:
        if history[0].timestamp > cutoff:
            return None
        return next((point for point in reversed(history) if point.timestamp <= cutoff), None)

    def _compute_changes(
        self,
        current: SnapshotPoint,
        earlier: SnapshotPoint,
        lookback_seconds: int,
    ) -> MarketChanges:
        oi_change_percent = self._oi_percent_change(earlier, current)
        price_change_percent = self._percent_change(earlier.price or 0.0, current.price or 0.0)
        volume_change_percent = self._percent_change(
            earlier.volume_24h or 0.0, current.volume_24h or 0.0
        )
        oi_change_value = (current.open_interest or 0.0) - (earlier.open_interest or 0.0)
        earlier_usd = self._oi_usd_value(
            earlier.open_interest, earlier.price, earlier.additional
        )
        current_usd = self._oi_usd_value(
            current.open_interest, current.price, current.additional
        )
        oi_change_usd = None
        if earlier_usd is not None and current_usd is not None:
            oi_change_usd = current_usd - earlier_usd
        return MarketChanges(
            oi_change_percent=oi_change_percent,
            price_change_percent=price_change_percent,
            volume_change_percent=volume_change_percent,
            oi_change_value=oi_change_value,
            oi_change_usd=oi_change_usd,
            lookback_seconds=lookback_seconds,
        )

    def _oi_percent_change(self, earlier: SnapshotPoint, current: SnapshotPoint) -> float:
        earlier_usd = self._oi_usd_value(
            earlier.open_interest, earlier.price, earlier.additional
        )
        current_usd = self._oi_usd_value(
            current.open_interest, current.price, current.additional
        )
        if earlier_usd is not None and current_usd is not None and earlier_usd > 0:
            return self._percent_change(earlier_usd, current_usd)
        return self._percent_change(
            earlier.open_interest or 0.0, current.open_interest or 0.0
        )

    @staticmethod
    def _classify_long_signal(
        changes: MarketChanges,
        thresholds: ExchangeThresholds,
        settings: ScannerSettings,
    ) -> str | None:
        oi = changes.oi_change_percent
        price = changes.price_change_percent
        if oi >= thresholds.oi_rise_percent and price >= thresholds.price_rise_percent:
            return "pump"
        if not settings.require_both_oi_and_price and oi >= thresholds.oi_rise_percent:
            return "oi_pump"
        if settings.require_oi_for_price_only:
            return None
        if price >= settings.price_only_min_percent:
            return "price_pump"
        return None

    @staticmethod
    def _classify_short_signal(
        changes: MarketChanges,
        thresholds: ExchangeThresholds,
        settings: ScannerSettings,
    ) -> str | None:
        oi = changes.oi_change_percent
        price = changes.price_change_percent
        if oi <= -thresholds.oi_drop_percent and price <= -thresholds.price_drop_percent:
            return "dump"
        if not settings.require_both_oi_and_price and oi <= -thresholds.oi_drop_percent:
            return "oi_dump"
        if settings.require_oi_for_price_only:
            return None
        if price <= -settings.price_only_min_percent:
            return "price_dump"
        return None

    @staticmethod
    def _oi_usd_value(
        open_interest: float | None,
        price: float | None,
        additional: dict[str, object],
    ) -> float | None:
        raw = additional.get("open_interest_value")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        if open_interest is not None and price is not None:
            return open_interest * price
        return None

    @staticmethod
    def _percent_change(previous: float, current: float) -> float:
        if previous == 0.0:
            return 0.0
        return (current - previous) / abs(previous) * 100.0

    @staticmethod
    def _safe_div(value: float, divisor: float) -> float:
        if divisor == 0.0:
            return 0.0
        return value / divisor

    @staticmethod
    def _compute_spread(bid: float | None, ask: float | None) -> float | None:
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None
        return round(ask - bid, 8)

    @staticmethod
    def _extract_funding(additional: dict[str, object]) -> float | None:
        funding = additional.get("funding_rate")
        if funding is None:
            return None
        try:
            return float(funding)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _direction(value: float) -> str:
        if value > 0:
            return "up"
        if value < 0:
            return "down"
        return "flat"

    @staticmethod
    def _determine_side(price_change_percent: float, signal_type: str) -> str:
        if abs(price_change_percent) >= 0.3:
            return "short" if price_change_percent < 0 else "long"
        short_types = {
            "dump", "oi_dump", "price_dump", "mega_dump", "pulse_dump", "vertical_dump",
            "reversal_dump", "liq_cascade_dump", "impulse_dump", "trend_dump",
        }
        if signal_type in short_types:
            return "short"
        return "long"

    @staticmethod
    def _calculate_score(
        oi_change_percent: float,
        price_change_percent: float,
        volume_change_percent: float,
        signal_type: str,
        flash_tier: float | None = None,
    ) -> int:
        if signal_type in {"mega_pump", "mega_dump"}:
            tier = flash_tier or abs(price_change_percent)
            if tier >= 40:
                return 1
            if tier >= 20:
                return 1
            if tier >= 10:
                return 2
            return 3

        if signal_type in {"reversal_pump", "reversal_dump"}:
            return 1

        if signal_type in {"trend_dump", "trend_pump"}:
            leg = flash_tier or abs(price_change_percent)
            if leg >= 10:
                return 1
            if leg >= 5:
                return 2
            return 2

        if signal_type in {"impulse_pump", "impulse_dump"}:
            tier = flash_tier or abs(price_change_percent)
            if tier >= 10:
                return 1
            if tier >= 6:
                return 2
            return 3

        if signal_type in {"pulse_pump", "pulse_dump", "short_squeeze"}:
            magnitude = abs(oi_change_percent) * 0.55 + abs(price_change_percent) * 0.45
            if magnitude < 2:
                return 1
            if magnitude < 4:
                return 2
            return 3

        magnitude = abs(oi_change_percent) * 0.45 + abs(price_change_percent) * 0.4 + abs(volume_change_percent) * 0.15
        if signal_type in {"pump", "dump"}:
            magnitude *= 1.15

        if magnitude < 3:
            return 1
        if magnitude < 6:
            return 2
        if magnitude < 10:
            return 3
        if magnitude < 15:
            return 4
        if magnitude < 22:
            return 5
        if magnitude < 30:
            return 6
        if magnitude < 40:
            return 7
        if magnitude < 55:
            return 8
        if magnitude < 75:
            return 9
        return 10

    def _compute_indicators(self, history: deque[SnapshotPoint], lookback_seconds: float) -> dict[str, float | None]:
        prices = [point.price for point in history if point.price is not None]
        if len(prices) < 2:
            return {
                "atr": None,
                "rsi": None,
                "ema_short": None,
                "ema_long": None,
                "vwap": None,
                "volume_speed": None,
                "liquidation_estimate": None,
            }

        atr = self._calculate_atr(prices)
        rsi = self._calculate_rsi(prices, 14)
        ema_short = self._calculate_ema(prices, 9)
        ema_long = self._calculate_ema(prices, 21)
        vwap = self._calculate_vwap(history)
        volume_speed = self._calculate_volume_speed(history, lookback_seconds)
        liquidation_estimate = self._extract_liquidation(history[-1].additional)

        return {
            "atr": atr,
            "rsi": rsi,
            "ema_short": ema_short,
            "ema_long": ema_long,
            "vwap": vwap,
            "volume_speed": volume_speed,
            "liquidation_estimate": liquidation_estimate,
        }

    @staticmethod
    def _calculate_atr(prices: list[float]) -> float | None:
        if len(prices) < 2:
            return None
        true_ranges = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
        return round(sum(true_ranges) / len(true_ranges), 6)

    @staticmethod
    def _calculate_ema(prices: list[float], period: int) -> float | None:
        if len(prices) < 1:
            return None
        ema = prices[0]
        k = 2.0 / (period + 1)
        for price in prices[1:]:
            ema = price * k + ema * (1.0 - k)
        return round(ema, 6)

    @staticmethod
    def _calculate_rsi(prices: list[float], period: int) -> float | None:
        if len(prices) < period + 1:
            return None
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        last_deltas = deltas[-period:]
        gains = sum(delta for delta in last_deltas if delta > 0)
        losses = sum(abs(delta) for delta in last_deltas if delta < 0)
        if losses == 0:
            return 100.0
        rs = gains / losses
        return round(100.0 - (100.0 / (1.0 + rs)), 2)

    @staticmethod
    def _calculate_vwap(history: deque[SnapshotPoint]) -> float | None:
        total_pv = 0.0
        total_volume = 0.0
        prev = history[0]
        for current in list(history)[1:]:
            if current.price is None or current.volume_24h is None or prev.volume_24h is None:
                prev = current
                continue
            delta_volume = current.volume_24h - prev.volume_24h
            if delta_volume <= 0:
                prev = current
                continue
            total_pv += current.price * delta_volume
            total_volume += delta_volume
            prev = current
        if total_volume <= 0:
            return None
        return round(total_pv / total_volume, 6)

    @staticmethod
    def _calculate_volume_speed(history: deque[SnapshotPoint], lookback_seconds: float) -> float | None:
        if len(history) < 2:
            return None
        current = history[-1]
        earlier = history[0]
        if current.volume_24h is None or earlier.volume_24h is None:
            return None
        minutes = max(lookback_seconds / 60.0, 1.0)
        return round((current.volume_24h - earlier.volume_24h) / minutes, 2)

    @staticmethod
    def _extract_liquidation(additional: dict[str, object]) -> float | None:
        keys = [
            "liquidation",
            "liquidation_estimate",
            "liquidations",
            "liquidation_amount",
        ]
        for key in keys:
            value = additional.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _coinglass_url(symbol: str, exchange: str) -> str:
        normalized = symbol.upper().replace("/", "")
        exchange_key = exchange.lower()
        if "bybit" in exchange_key:
            return f"https://www.coinglass.com/tv/Bybit_{normalized}"
        if "binance" in exchange_key:
            return f"https://www.coinglass.com/tv/Binance_{normalized}"
        return f"https://www.coinglass.com/Futures/{normalized}"
