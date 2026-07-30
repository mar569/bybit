"""Urals (российская нефть) — спот для мини-окошка на oil-графиках.

Источник: TradingEconomics (публичная страница, без платного API).
Спарклайн ≈ Brent − скидка Urals/Brent (форма движения как у Brent, уровень Urals).
"""
from __future__ import annotations

import logging
import re
import time
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CACHE: tuple[float, "UralsSnapshot"] | None = None
_CACHE_TTL = 1800.0  # 30 мин


@dataclass(frozen=True)
class UralsSnapshot:
    price: float
    change_pct: float | None = None
    source: str = "TradingEconomics"
    as_of_ts: float = 0.0
    brent_ref: float | None = None

    @property
    def discount_vs_brent(self) -> float | None:
        if self.brent_ref is None or self.brent_ref <= 0:
            return None
        return self.price - self.brent_ref


def _parse_urals_from_html(html: str) -> tuple[float, float | None]:
    """Достаёт last price и дневной % из HTML TE."""
    price: float | None = None
    chg: float | None = None

    m = re.search(
        r"Urals Oil (?:rose|fell|was) to ([0-9]+(?:\.[0-9]+)?) USD",
        html,
        re.I,
    )
    if m:
        price = float(m.group(1))

    if price is None:
        m = re.search(r'"Last"\s*:\s*([0-9]+(?:\.[0-9]+)?)', html)
        if m:
            price = float(m.group(1))

    m = re.search(
        r"up\s+([0-9]+(?:\.[0-9]+)?)%\s+from the previous day",
        html,
        re.I,
    )
    if m:
        chg = float(m.group(1))
    else:
        m = re.search(
            r"down\s+([0-9]+(?:\.[0-9]+)?)%\s+from the previous day",
            html,
            re.I,
        )
        if m:
            chg = -float(m.group(1))
        else:
            m = re.search(r'"DailyPercentualChange"\s*:\s*([-0-9.]+)', html)
            if m:
                chg = float(m.group(1))

    if price is None or price < 20 or price > 200:
        raise ValueError("Urals price not found / out of range")
    return price, chg


def fetch_urals_snapshot(*, brent_ref: float | None = None, force: bool = False) -> UralsSnapshot | None:
    """Текущая цена Urals; кэш 30 мин."""
    global _CACHE
    now = time.time()
    if not force and _CACHE is not None and now - _CACHE[0] < _CACHE_TTL:
        snap = _CACHE[1]
        if brent_ref and snap.brent_ref != brent_ref:
            return UralsSnapshot(
                price=snap.price,
                change_pct=snap.change_pct,
                source=snap.source,
                as_of_ts=snap.as_of_ts,
                brent_ref=brent_ref,
            )
        return snap

    url = "https://tradingeconomics.com/commodity/urals-oil"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; BybitBot/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
        price, chg = _parse_urals_from_html(html)
    except Exception:
        logger.debug("Urals fetch failed", exc_info=True)
        return _CACHE[1] if _CACHE else None

    snap = UralsSnapshot(
        price=price,
        change_pct=chg,
        source="TradingEconomics",
        as_of_ts=now,
        brent_ref=brent_ref,
    )
    _CACHE = (now, snap)
    return snap


def urals_sparkline_from_brent(
    brent_closes: list[float],
    *,
    urals_price: float,
    brent_last: float,
) -> list[float]:
    """Мини-серия Urals ≈ Brent − скидка (тот же путь, другой уровень)."""
    if not brent_closes or brent_last <= 0:
        return []
    discount = brent_last - urals_price
    return [max(1.0, float(c) - discount) for c in brent_closes]
