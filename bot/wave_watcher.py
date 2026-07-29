"""Wave Level Watcher — после алерта следим за зоной входа / инвалидацией.

Похож на ScenarioWatcher, но завязан на elliott_entry / Fib-зону волны 2/4/C.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .ta_analysis import fmt_price
from .wave_alerts import WaveEvent

logger = logging.getLogger(__name__)


@dataclass
class WaveWatch:
    exchange: str
    symbol: str
    side: str
    setup_kind: str
    enroll_price: float
    entry_price: float | None = None
    stop_price: float | None = None
    invalidation: float | None = None
    tp_prices: tuple[float, ...] = ()
    expect_ru: str = ""
    started_at: float = 0.0
    expires_at: float = 0.0
    fired: bool = False
    chat_id: int | None = None


@dataclass(frozen=True)
class WaveWatchUpdate:
    watch: WaveWatch
    kind: str  # entry_hit | invalidation | expired | approaching
    price: float
    detail: str = ""


class WaveLevelWatcher:
    def __init__(self) -> None:
        self._watches: dict[tuple[str, str], WaveWatch] = {}
        self._last_enroll_at: dict[tuple[str, str], float] = {}

    @property
    def active_count(self) -> int:
        return len(self._watches)

    def clear_all(self) -> None:
        self._watches.clear()

    def try_enroll(self, event: WaveEvent, settings: Any, *, chat_id: int | None = None) -> bool:
        if not getattr(settings, "wave_watch_enabled", True):
            return False
        # ENTRY уже готов — нечего ждать
        if event.setup_kind == "entry_ready" and event.entry_ready:
            return False
        if event.entry_price is None and event.invalidation is None:
            return False

        key = (event.exchange.lower(), event.symbol.upper())
        now = time.time()
        enroll_cd = int(getattr(settings, "wave_watch_enroll_cooldown_seconds", 900))
        last = self._last_enroll_at.get(key, 0.0)
        if now - last < enroll_cd and key not in self._watches:
            return False

        minutes = int(getattr(settings, "wave_watch_minutes", 90))
        watch = WaveWatch(
            exchange=event.exchange,
            symbol=event.symbol,
            side=event.side,
            setup_kind=event.setup_kind,
            enroll_price=event.price,
            entry_price=event.entry_price,
            stop_price=event.stop_price,
            invalidation=event.invalidation,
            tp_prices=event.tp_prices,
            expect_ru=event.expect_ru,
            started_at=now,
            expires_at=now + minutes * 60,
            chat_id=chat_id,
        )
        self._watches[key] = watch
        self._last_enroll_at[key] = now
        logger.info(
            "Wave watch %s %s kind=%s entry=%s inv=%s",
            event.exchange,
            event.symbol,
            event.setup_kind,
            fmt_price(event.entry_price) if event.entry_price else "-",
            fmt_price(event.invalidation) if event.invalidation else "-",
        )
        return True

    def tick(self, scanner: Any, settings: Any) -> list[WaveWatchUpdate]:
        now = time.time()
        near_pct = float(getattr(settings, "wave_watch_near_pct", 0.35))
        updates: list[WaveWatchUpdate] = []
        done_keys: list[tuple[str, str]] = []

        for key, watch in list(self._watches.items()):
            if now >= watch.expires_at:
                updates.append(
                    WaveWatchUpdate(
                        watch=watch,
                        kind="expired",
                        price=watch.enroll_price,
                        detail="таймаут наблюдения",
                    )
                )
                done_keys.append(key)
                continue

            hist = None
            try:
                hist = scanner.history.get(f"{watch.exchange}:{watch.symbol}")
                if hist is None:
                    # try Bybit key casing
                    hist = scanner.history.get(f"Bybit:{watch.symbol}")
            except Exception:
                hist = None
            if not hist:
                continue
            price = hist[-1].price
            if price is None or price <= 0:
                continue

            # Инвалидация
            inv = watch.invalidation or watch.stop_price
            if inv is not None:
                if watch.side == "long" and price < inv:
                    updates.append(
                        WaveWatchUpdate(
                            watch=watch,
                            kind="invalidation",
                            price=price,
                            detail=f"пробой inv {fmt_price(inv)}",
                        )
                    )
                    done_keys.append(key)
                    continue
                if watch.side == "short" and price > inv:
                    updates.append(
                        WaveWatchUpdate(
                            watch=watch,
                            kind="invalidation",
                            price=price,
                            detail=f"пробой inv {fmt_price(inv)}",
                        )
                    )
                    done_keys.append(key)
                    continue

            entry = watch.entry_price
            if entry is None or watch.fired:
                continue

            dist_pct = abs(price - entry) / entry * 100.0
            hit = False
            if watch.side == "long" and price <= entry * (1.0 + near_pct / 100.0):
                hit = dist_pct <= near_pct * 1.5 or price <= entry
            elif watch.side == "short" and price >= entry * (1.0 - near_pct / 100.0):
                hit = dist_pct <= near_pct * 1.5 or price >= entry

            if hit:
                watch.fired = True
                updates.append(
                    WaveWatchUpdate(
                        watch=watch,
                        kind="entry_hit",
                        price=price,
                        detail=f"цена у зоны входа {fmt_price(entry)}",
                    )
                )
                done_keys.append(key)
            elif dist_pct <= near_pct * 2.5 and not getattr(watch, "_approached", False):
                setattr(watch, "_approached", True)
                updates.append(
                    WaveWatchUpdate(
                        watch=watch,
                        kind="approaching",
                        price=price,
                        detail=f"приближение к {fmt_price(entry)} ({dist_pct:.2f}%)",
                    )
                )

        for key in done_keys:
            self._watches.pop(key, None)
        return updates


def format_wave_watch_update(upd: WaveWatchUpdate) -> str:
    w = upd.watch
    side = (w.side or "").upper()
    if upd.kind == "entry_hit":
        title = "🎯 WAVE · ЗОНА ВХОДА"
    elif upd.kind == "invalidation":
        title = "❌ WAVE · ИНВАЛИДАЦИЯ"
    elif upd.kind == "approaching":
        title = "👀 WAVE · ПРИБЛИЖЕНИЕ"
    else:
        title = "⌛ WAVE · ИСТЕКЛО"

    lines = [
        f"<b>{title}</b> · <b>{w.symbol}</b> · {side}",
        f"Цена ${upd.price:.6g} · {upd.detail}",
    ]
    if w.expect_ru:
        lines.append(f"➡️ {w.expect_ru}")
    if w.entry_price:
        lines.append(f"вход {fmt_price(w.entry_price)}")
    if w.stop_price:
        lines.append(f"стоп {fmt_price(w.stop_price)}")
    if w.tp_prices:
        lines.append("TP " + " / ".join(fmt_price(t) for t in w.tp_prices[:3]))
    return "\n".join(lines)
