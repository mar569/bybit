from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .models import Signal, SnapshotPoint
from .settings import ExchangeThresholds, SettingsManager

HISTORY_MAX_POINTS = 3600


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

        now = timestamp or time.time()
        key = f"{exchange}:{symbol}"
        if volume_24h is not None:
            self._volumes[key] = volume_24h
        self._refresh_top_symbols_if_needed()
        if not self._is_in_top_n(exchange, symbol):
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

        await self._evaluate_signals(key, exchange, symbol)

    async def _evaluate_signals(self, key: str, exchange: str, symbol: str) -> None:
        async with self.lock:
            history = self.history.get(key)
            if not history or len(history) < 2:
                return
            current = history[-1]
            settings = self.settings.settings
            thresholds = settings.for_exchange(exchange)
            if current.open_interest is None or current.price is None:
                return

            lookback_seconds = thresholds.oi_period_minutes * 60
            cutoff = current.timestamp - lookback_seconds
            if history[0].timestamp > cutoff:
                return
            earlier = next((point for point in reversed(history) if point.timestamp <= cutoff), None)
            if earlier is None or earlier.open_interest is None or earlier.price is None:
                return

            oi_change_value = current.open_interest - earlier.open_interest
            oi_change_percent = self._percent_change(earlier.open_interest, current.open_interest)
            price_change_value = current.price - earlier.price
            price_change_percent = self._percent_change(earlier.price, current.price)
            volume_change_percent = self._percent_change(earlier.volume_24h or 0.0, current.volume_24h or 0.0)

            interval_volume = None
            try:
                prev_vol = history[-2].volume_24h if len(history) >= 2 else None
                if current.volume_24h is not None and prev_vol is not None:
                    interval_volume = max(0.0, current.volume_24h - prev_vol)
            except Exception:
                interval_volume = None

            spread = self._compute_spread(current.bid_price, current.ask_price)
            funding_rate = self._extract_funding(current.additional)
            price_speed = self._safe_div(price_change_percent, max(lookback_seconds / 60.0, 1.0))
            oi_direction = self._direction(oi_change_percent)
            price_direction = self._direction(price_change_percent)
            signal_type = self._determine_signal_type(
                oi_change_percent,
                price_change_percent,
                thresholds,
            )

            if signal_type is None:
                return

            if current.open_interest < settings.min_open_interest:
                return

            if settings.min_volume > 0 and (current.volume_24h or 0.0) < settings.min_volume:
                return

            now = time.time()
            last_time = self.last_signal_time.get(key, 0.0)
            if now - last_time < settings.signal_cooldown_seconds:
                return

            current_oi_usd = self._oi_usd_value(current.open_interest, current.price, current.additional)
            earlier_oi_usd = self._oi_usd_value(earlier.open_interest, earlier.price, earlier.additional)
            oi_change_usd = None
            if current_oi_usd is not None and earlier_oi_usd is not None:
                oi_change_usd = current_oi_usd - earlier_oi_usd

            score = self._calculate_score(
                oi_change_percent,
                price_change_percent,
                volume_change_percent,
                signal_type,
            )

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

            price_pump = False
            try:
                window_seconds = settings.price_pump_window_minutes * 60
                cutoff2 = current.timestamp - window_seconds
                earlier2 = next((point for point in reversed(history) if point.timestamp <= cutoff2), history[0])
                if earlier2 and earlier2.price is not None:
                    price_window_pct = self._percent_change(earlier2.price, current.price)
                    if price_window_pct >= settings.price_pump_threshold_pct:
                        price_pump = True
            except Exception:
                price_pump = False

            cvd_div = False
            try:
                cvd_value = None
                if current.additional:
                    for k in ("cvd", "CVD", "taker_buy_base_volume", "taker_buy_quote_volume"):
                        if k in current.additional:
                            try:
                                cvd_value = float(current.additional.get(k))
                                break
                            except Exception:
                                continue
                if cvd_value is not None:
                    if cvd_value <= settings.cvd_divergence_threshold and price_change_percent > 0:
                        cvd_div = True
                elif price_change_percent > 0 and current.open_interest <= earlier.open_interest and vol_spike:
                    cvd_div = True
            except Exception:
                cvd_div = False

            if settings.min_signal_score and score < settings.min_signal_score:
                return

            self._reset_daily_counts_if_needed()
            self.daily_signal_counts[key] = self.daily_signal_counts.get(key, 0) + 1
            self.last_signal_time[key] = now
            side = self._determine_side(price_change_percent, signal_type)

            signal = Signal(
                exchange=exchange,
                symbol=symbol,
                signal_type=signal_type,
                oi_period_minutes=thresholds.oi_period_minutes,
                oi_change_percent=round(oi_change_percent, 2),
                oi_change_value=round(oi_change_value, 2),
                oi_change_usd=round(oi_change_usd, 2) if oi_change_usd is not None else None,
                oi_direction=oi_direction,
                price_change_percent=round(price_change_percent, 2),
                price_change_value=round(price_change_value, 6),
                price_direction=price_direction,
                volume_change_percent=round(volume_change_percent, 2),
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
                    "price_pump_window": price_pump,
                    "cvd_divergence": cvd_div,
                    "oi_usd_formatted": format_oi_usd(oi_change_usd),
                },
            )

        await self.on_signal(signal)

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
        if price_change_percent < 0 or signal_type in {"dump", "oi_dump", "price_dump"}:
            return "short"
        return "long"

    @staticmethod
    def _determine_signal_type(
        oi_change_percent: float,
        price_change_percent: float,
        thresholds: ExchangeThresholds,
    ) -> str | None:
        if oi_change_percent >= thresholds.oi_rise_percent and price_change_percent >= thresholds.price_rise_percent:
            return "pump"
        if oi_change_percent <= -thresholds.oi_drop_percent and price_change_percent <= -thresholds.price_drop_percent:
            return "dump"
        if oi_change_percent >= thresholds.oi_rise_percent:
            return "oi_pump"
        if oi_change_percent <= -thresholds.oi_drop_percent:
            return "oi_dump"
        if price_change_percent >= thresholds.price_rise_percent:
            return "price_pump"
        if price_change_percent <= -thresholds.price_drop_percent:
            return "price_dump"
        return None

    @staticmethod
    def _calculate_score(
        oi_change_percent: float,
        price_change_percent: float,
        volume_change_percent: float,
        signal_type: str,
    ) -> int:
        # 1 = ранний вход, 10 = поздно. Чем сильнее уже прошло движение, тем выше score.
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
