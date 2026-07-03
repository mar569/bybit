from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .models import Signal, SnapshotPoint
from .settings import ExchangeThresholds, ScannerSettings, SettingsManager

HISTORY_MAX_POINTS = 3600

logger = logging.getLogger(__name__)


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
    def __init__(self, settings: SettingsManager, on_signal: Callable[[Signal], Awaitable[None]]) -> None:
        self.settings = settings
        self.on_signal = on_signal
        self.history: dict[str, deque[SnapshotPoint]] = {}
        self.last_signal_time: dict[str, float] = {}
        self.daily_signal_counts: dict[str, int] = {}
        self._daily_reset_key: str | None = None
        self._volumes: dict[str, float] = {}
        self._top_symbols: dict[str, set[str]] = {}
        self._last_top_refresh = 0.0
        self._dirty_keys: set[str] = set()
        self.lock = asyncio.Lock()

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
        settings = self.settings.settings
        in_top = self._is_in_top_n(exchange, symbol)
        if not in_top and not (settings.breakout_enabled and settings.breakout_bypass_top_n):
            return

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

        self._dirty_keys.add(key)

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
        if not self.settings.settings.signals_enabled:
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

            candidate = self._pick_best_candidate(history, current, settings, thresholds)
            if candidate is None:
                return

            earlier = candidate.earlier
            changes = self._compute_changes(current, earlier, candidate.period_minutes * 60)

            oi_usd_now = self._oi_usd_value(current.open_interest, current.price, current.additional)
            is_breakout = candidate.signal_type in {"vertical_pump", "vertical_dump"}
            min_liquidity = (
                settings.breakout_min_liquidity_oi_usd
                if is_breakout
                else settings.min_open_interest
            )
            if oi_usd_now is None or oi_usd_now < min_liquidity:
                return

            if settings.min_volume > 0 and (current.volume_24h or 0.0) < settings.min_volume:
                return

            is_mega = candidate.signal_type in {"mega_pump", "mega_dump"}
            if not is_mega and not is_breakout and changes.oi_change_usd is not None:
                if abs(changes.oi_change_usd) < settings.min_oi_change_usd:
                    if candidate.signal_type not in {"price_pump", "price_dump"}:
                        return

            now = time.time()
            if is_breakout:
                cooldown_key = f"{key}:breakout"
                cooldown = settings.breakout_cooldown_seconds
            elif is_mega:
                cooldown_key = f"{key}:mega"
                cooldown = settings.mega_cooldown_seconds
            else:
                cooldown_key = key
                cooldown = settings.signal_cooldown_seconds
            last_time = self.last_signal_time.get(cooldown_key, 0.0)
            if now - last_time < cooldown:
                return

            if is_breakout:
                score = 1
            else:
                score = self._calculate_score(
                    changes.oi_change_percent,
                    changes.price_change_percent,
                    changes.volume_change_percent,
                    candidate.signal_type,
                    flash_tier=candidate.flash_tier,
                )

            if not is_breakout and settings.min_signal_score and score < settings.min_signal_score:
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
            side = self._determine_side(changes.price_change_percent, candidate.signal_type)

            logger.info(
                "Signal %s %s %s | OI %.2f%% | price %.2f%% | score %d | %dm",
                exchange,
                symbol,
                candidate.signal_type,
                changes.oi_change_percent,
                changes.price_change_percent,
                score,
                candidate.period_minutes,
            )

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
                liquidation_estimate=indicators.get("liquidation_estimate"),
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
                    **(candidate.breakout_meta or {}),
                },
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

        if spike_pct >= settings.breakout_min_spike_percent:
            return SignalCandidate(
                signal_type="vertical_pump",
                period_minutes=settings.breakout_spike_minutes,
                oi_change_percent=oi_pct,
                price_change_percent=spike_pct,
                earlier=spike_earlier,
                urgency=0,
                breakout_meta=meta,
            )

        if spike_pct <= -settings.breakout_min_dump_percent:
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

    def _pick_best_candidate(
        self,
        history: deque[SnapshotPoint],
        current: SnapshotPoint,
        settings: ScannerSettings,
        thresholds: ExchangeThresholds,
    ) -> SignalCandidate | None:
        breakout = self._detect_vertical_breakout(history, current, settings)
        if breakout is not None:
            return breakout

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
        if oi >= thresholds.oi_rise_percent:
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
        if oi <= -thresholds.oi_drop_percent:
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
        short_types = {
            "dump", "oi_dump", "price_dump", "mega_dump", "pulse_dump", "vertical_dump",
        }
        if price_change_percent < 0 or signal_type in short_types:
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
