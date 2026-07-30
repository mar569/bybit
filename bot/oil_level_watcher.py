"""Алерты пробоя ключевых уровней Brent / WTI в oil-чат."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .ta_analysis import fmt_price

logger = logging.getLogger(__name__)


@dataclass
class OilInstrumentWatch:
    label: str
    breakout: float | None = None
    breakdown: float | None = None
    initialized: bool = False
    was_above_breakout: bool = False
    was_below_breakdown: bool = False
    last_price: float = 0.0


@dataclass(frozen=True)
class OilLevelAlert:
    label: str
    direction: str  # up | down
    level: float
    price: float
    message: str


class OilLevelWatcher:
    def __init__(self) -> None:
        self._watches: dict[str, OilInstrumentWatch] = {}
        self._last_alert_ts: dict[tuple[str, str], float] = {}

    def clear_all(self) -> None:
        self._watches.clear()
        self._last_alert_ts.clear()

    def update_levels(
        self,
        label: str,
        *,
        price: float,
        breakout: float | None,
        breakdown: float | None,
    ) -> None:
        key = label.upper()
        watch = self._watches.get(key)
        if watch is None:
            watch = OilInstrumentWatch(label=label)
            self._watches[key] = watch
        watch.breakout = breakout if breakout and breakout > 0 else None
        watch.breakdown = breakdown if breakdown and breakdown > 0 else None
        watch.last_price = price

    def check_prices(
        self,
        prices: dict[str, float],
        settings: Any,
    ) -> list[OilLevelAlert]:
        if not getattr(settings, "oil_level_alerts_enabled", True):
            return []
        cooldown = int(getattr(settings, "oil_level_alert_cooldown_seconds", 1800))
        now = time.time()
        alerts: list[OilLevelAlert] = []

        for label, price in prices.items():
            key = label.upper()
            watch = self._watches.get(key)
            if watch is None or price <= 0:
                continue
            watch.last_price = price

            if watch.breakout and price >= watch.breakout:
                if watch.initialized and not watch.was_above_breakout:
                    cd_key = (key, "up")
                    if now - self._last_alert_ts.get(cd_key, 0.0) >= cooldown:
                        msg = (
                            f"🛢 <b>{watch.label} · пробой вверх</b>\n"
                            f"Закрытие выше <b>{fmt_price(watch.breakout)}</b>\n"
                            f"Цена: <b>${price:.2f}</b>\n"
                            f"<i>Bybit TradFi BZUSDT/CLUSDT · intraday от уровней</i>"
                        )
                        alerts.append(
                            OilLevelAlert(
                                label=watch.label,
                                direction="up",
                                level=watch.breakout,
                                price=price,
                                message=msg,
                            )
                        )
                        self._last_alert_ts[cd_key] = now
                watch.was_above_breakout = True
            else:
                watch.was_above_breakout = False

            if watch.breakdown and price <= watch.breakdown:
                if watch.initialized and not watch.was_below_breakdown:
                    cd_key = (key, "down")
                    if now - self._last_alert_ts.get(cd_key, 0.0) >= cooldown:
                        msg = (
                            f"🛢 <b>{watch.label} · пробой вниз</b>\n"
                            f"Закрытие ниже <b>{fmt_price(watch.breakdown)}</b>\n"
                            f"Цена: <b>${price:.2f}</b>\n"
                            f"<i>Стоп выше resistance / отмена при возврате в базу</i>"
                        )
                        alerts.append(
                            OilLevelAlert(
                                label=watch.label,
                                direction="down",
                                level=watch.breakdown,
                                price=price,
                                message=msg,
                            )
                        )
                        self._last_alert_ts[cd_key] = now
                watch.was_below_breakdown = True
            else:
                watch.was_below_breakdown = False

            watch.initialized = True

        return alerts
