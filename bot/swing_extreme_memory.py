"""Память прошлого HH/LL: объём, OI, CVD — сравнение с новым экстремумом (BNB/MON кейс)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar
from .market_reading import (
    WEAK_VOLUME_RATIO,
    ParticipationDivergence,
    _local_volume,
    _oi_near_time,
)


@dataclass(frozen=True)
class StoredExtreme:
    kind: str  # high | low
    price: float
    open_time: float
    volume: float
    oi: float | None = None
    cvd_buy_ratio: float | None = None
    updated_at: float = 0.0


@dataclass
class _SymbolMemory:
    prior_high: StoredExtreme | None = None
    last_high: StoredExtreme | None = None
    prior_low: StoredExtreme | None = None
    last_low: StoredExtreme | None = None


class SwingExtremeMemory:
    def __init__(self, *, ttl_seconds: float = 6 * 3600.0) -> None:
        self._ttl = float(ttl_seconds)
        self._by_key: dict[tuple[str, int], _SymbolMemory] = {}

    def _mem(self, symbol: str, interval_minutes: int) -> _SymbolMemory:
        key = (symbol.upper(), int(interval_minutes))
        if key not in self._by_key:
            self._by_key[key] = _SymbolMemory()
        return self._by_key[key]

    def _snap(
        self,
        kind: str,
        swing: object,
        bars: Sequence[KlineBar],
        oi_bars: Sequence | None,
        cvd_ratio: float | None,
    ) -> StoredExtreme | None:
        idx = int(getattr(swing, "index", -1))
        if idx < 0 or idx >= len(bars):
            return None
        price = float(getattr(swing, "price", 0) or 0)
        if price <= 0:
            return None
        vol = float(getattr(bars[idx], "volume", 0) or 0) or _local_volume(bars, idx)
        oi = _oi_near_time(oi_bars, float(bars[idx].open_time))
        return StoredExtreme(
            kind=kind,
            price=price,
            open_time=float(getattr(bars[idx], "open_time", 0) or 0),
            volume=vol,
            oi=oi,
            cvd_buy_ratio=float(cvd_ratio) if cvd_ratio is not None else None,
            updated_at=time.time(),
        )

    def _prune(self, mem: _SymbolMemory) -> None:
        now = time.time()

        def fresh(s: StoredExtreme | None) -> StoredExtreme | None:
            if s is None:
                return None
            if now - s.updated_at > self._ttl:
                return None
            return s

        mem.prior_high = fresh(mem.prior_high)
        mem.last_high = fresh(mem.last_high)
        mem.prior_low = fresh(mem.prior_low)
        mem.last_low = fresh(mem.last_low)

    def update(
        self,
        symbol: str,
        interval_minutes: int,
        bars: Sequence[KlineBar],
        swings: Sequence,
        *,
        oi_bars: Sequence | None = None,
        cvd_ratio: float | None = None,
    ) -> ParticipationDivergence | None:
        if not symbol or len(bars) < 12 or not swings:
            return None
        mem = self._mem(symbol, interval_minutes)
        self._prune(mem)

        def _sync(kind: str, prior_attr: str, last_attr: str) -> ParticipationDivergence | None:
            points = [s for s in swings if getattr(s, "kind", "") == kind]
            if not points:
                return None
            latest = points[-1]
            snap = self._snap(kind, latest, bars, oi_bars, cvd_ratio)
            if snap is None:
                return None
            prior: StoredExtreme | None = getattr(mem, prior_attr)
            last: StoredExtreme | None = getattr(mem, last_attr)

            if last is None and len(points) >= 2:
                prev_swing = points[-2]
                prev_snap = self._snap(kind, prev_swing, bars, oi_bars, cvd_ratio)
                if prev_snap is not None:
                    prior = prev_snap
                    last = snap
                    setattr(mem, prior_attr, prior)
                    setattr(mem, last_attr, last)

            if last is None or abs(last.open_time - snap.open_time) > 1:
                if last is not None and snap.open_time > last.open_time:
                    setattr(mem, prior_attr, last)
                    setattr(mem, last_attr, snap)
                elif last is None or snap.price >= last.price * 0.999:
                    setattr(mem, last_attr, snap)
            prior = getattr(mem, prior_attr)
            last = getattr(mem, last_attr)
            if prior is None or last is None or prior.open_time == last.open_time:
                return None

            if kind == "high":
                if last.price < prior.price * 1.001:
                    return None
                vol_ratio = last.volume / prior.volume if prior.volume > 0 else 1.0
                oi_ratio = (
                    (last.oi / prior.oi)
                    if last.oi and prior.oi and prior.oi > 0
                    else None
                )
                if vol_ratio >= WEAK_VOLUME_RATIO and (
                    oi_ratio is None or oi_ratio >= WEAK_VOLUME_RATIO
                ):
                    return None
                oi_bit = ""
                if oi_ratio is not None and oi_ratio < WEAK_VOLUME_RATIO:
                    oi_bit = f", OI {oi_ratio:.0%} от прошлого хая"
                cvd_bit = ""
                if (
                    last.cvd_buy_ratio is not None
                    and prior.cvd_buy_ratio is not None
                    and last.cvd_buy_ratio < prior.cvd_buy_ratio * 0.92
                ):
                    cvd_bit = f", CVD buy {last.cvd_buy_ratio:.0%} слабее прошлого пика"
                return ParticipationDivergence(
                    kind="weak_high",
                    idx_a=0,
                    idx_b=0,
                    price_a=prior.price,
                    price_b=last.price,
                    volume_a=prior.volume,
                    volume_b=last.volume,
                    volume_ratio=vol_ratio,
                    oi_ratio=oi_ratio,
                    label_ru=(
                        f"[память swing] хай {last.price:g} vs {prior.price:g}, "
                        f"объём {vol_ratio:.0%} от прошлого пика{oi_bit}{cvd_bit} "
                        "— участие не подтверждает рост"
                    ),
                )
            if last.price > prior.price * 0.999:
                return None
            vol_ratio = last.volume / prior.volume if prior.volume > 0 else 1.0
            oi_ratio = (
                (last.oi / prior.oi) if last.oi and prior.oi and prior.oi > 0 else None
            )
            if vol_ratio >= WEAK_VOLUME_RATIO and (
                oi_ratio is None or oi_ratio >= WEAK_VOLUME_RATIO
            ):
                return None
            return ParticipationDivergence(
                kind="weak_low",
                idx_a=0,
                idx_b=0,
                price_a=prior.price,
                price_b=last.price,
                volume_a=prior.volume,
                volume_b=last.volume,
                volume_ratio=vol_ratio,
                oi_ratio=oi_ratio,
                label_ru=(
                    f"[память swing] лой {last.price:g} vs {prior.price:g}, "
                    f"объём {vol_ratio:.0%} от прошлого дна — продажи без усиления"
                ),
            )

        div_high = _sync("high", "prior_high", "last_high")
        if div_high is not None:
            return div_high
        return _sync("low", "prior_low", "last_low")


_STORE = SwingExtremeMemory()


def get_swing_extreme_memory() -> SwingExtremeMemory:
    return _STORE


def detect_participation_with_memory(
    symbol: str,
    interval_minutes: int,
    bars: Sequence[KlineBar],
    swings: Sequence,
    *,
    oi_bars: Sequence | None = None,
    cvd_ratio: float | None = None,
) -> ParticipationDivergence | None:
    from .market_reading import detect_participation_divergence

    local = detect_participation_divergence(bars, swings, oi_bars=oi_bars)
    mem = get_swing_extreme_memory().update(
        symbol,
        interval_minutes,
        bars,
        swings,
        oi_bars=oi_bars,
        cvd_ratio=cvd_ratio,
    )
    if local is not None and mem is not None:
        if local.kind == mem.kind:
            return local
        if local.kind == "weak_high":
            return local
        return mem
    return local or mem
