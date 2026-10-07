"""Типы ретеста и качество входа: идеальный / хороший / слабый."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .bybit_klines import KlineBar
from .zone_model import TradingZone, zones_near_price

RetestKind = Literal[
    "level",
    "ob",
    "breaker",
    "trendline",
    "false_break",
    "none",
]

EntryQuality = Literal["ideal", "good", "weak", "none"]


@dataclass(frozen=True)
class RetestSignal:
    kind: RetestKind
    price: float
    label_ru: str
    quality: EntryQuality
    zone_kind: str = ""


def _in_zone(price: float, top: float, bot: float, *, pct: float = 0.35) -> bool:
    if top <= bot:
        return False
    mid = (top + bot) / 2.0
    return abs(price - mid) / mid * 100.0 <= pct or (bot <= price <= top)


def detect_retests(
    bars: Sequence[KlineBar],
    zones: Sequence[TradingZone],
    *,
    smc: object | None = None,
    channel: object | None = None,
    breakout_level: float | None = None,
    breakdown_level: float | None = None,
) -> list[RetestSignal]:
    if not bars:
        return []
    cur = float(bars[-1].close)
    out: list[RetestSignal] = []
    near = zones_near_price(zones, cur, pct=3.0)

    for z in near:
        if not _in_zone(cur, z.top, z.bottom):
            continue
        if z.kind.startswith("ob_"):
            out.append(
                RetestSignal(
                    kind="ob",
                    price=z.mid,
                    label_ru=f"ретест {z.label_ru} ({z.tf_label})",
                    quality="good" if z.valid else "weak",
                    zone_kind=z.kind,
                )
            )
        elif z.kind.startswith("breaker_"):
            out.append(
                RetestSignal(
                    kind="breaker",
                    price=z.mid,
                    label_ru=f"ретест breaker ({z.tf_label})",
                    quality="good",
                    zone_kind=z.kind,
                )
            )
        elif z.kind in {"demand", "supply"}:
            q: EntryQuality = "good" if z.valid else "weak"
            out.append(
                RetestSignal(
                    kind="level",
                    price=z.mid,
                    label_ru=f"ретест {z.label_ru}",
                    quality=q,
                    zone_kind=z.kind,
                )
            )
        elif z.kind.startswith("sr_"):
            out.append(
                RetestSignal(
                    kind="level",
                    price=z.mid,
                    label_ru=z.label_ru,
                    quality="good" if z.touch_count >= 2 else "weak",
                    zone_kind=z.kind,
                )
            )

    if smc and getattr(smc, "liquidity_sweep", False):
        for z in near:
            if z.valid and z.kind in {"demand", "supply", "ob_bull", "ob_bear"}:
                out.append(
                    RetestSignal(
                        kind="false_break",
                        price=cur,
                        label_ru="ложный пробой + возврат (свип)",
                        quality="ideal",
                        zone_kind=z.kind,
                    )
                )
                break

    if channel is not None and hasattr(channel, "kind"):
        sup = getattr(channel, "support", None) or getattr(channel, "lower", None)
        res = getattr(channel, "resistance", None) or getattr(channel, "upper", None)
        for edge, name in ((sup, "поддержка канала"), (res, "сопр. канала")):
            if edge is None:
                continue
            e = float(edge)
            if abs(cur - e) / cur * 100.0 <= 0.8:
                out.append(
                    RetestSignal(
                        kind="trendline",
                        price=e,
                        label_ru=f"ретест {name}",
                        quality="good",
                    )
                )

    for lvl, name in ((breakout_level, "пробой"), (breakdown_level, "пробой вниз")):
        if lvl is None:
            continue
        lv = float(lvl)
        if abs(cur - lv) / cur * 100.0 <= 1.0:
            out.append(
                RetestSignal(
                    kind="level",
                    price=lv,
                    label_ru=f"ретест уровня {name}",
                    quality="good",
                )
            )

    return out[:6]


def best_entry_quality(
    retests: Sequence[RetestSignal],
    *,
    htf_aligned: bool,
    smc_checklist_ready: bool,
) -> tuple[EntryQuality, str]:
    if not retests:
        return "none", "нет ретеста — только наблюдение"
    order = {"ideal": 3, "good": 2, "weak": 1, "none": 0}
    best = max(retests, key=lambda r: order.get(r.quality, 0))
    q = best.quality
    if q == "ideal" and htf_aligned and smc_checklist_ready:
        return "ideal", best.label_ru
    if q in {"ideal", "good"} and htf_aligned:
        return "good", best.label_ru
    if q == "weak":
        return "weak", best.label_ru + " · без HTF/confluence"
    return q, best.label_ru
