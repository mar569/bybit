"""Классы зон PDF: S/R, спрос/предложение, OB, breaker + валидация."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .bybit_klines import KlineBar

ZoneKind = Literal[
    "sr_support",
    "sr_resistance",
    "demand",
    "supply",
    "ob_bull",
    "ob_bear",
    "breaker_bull",
    "breaker_bear",
]

Freshness = Literal["fresh", "tested", "stale"]

# Макс. высота зоны (% от mid) — чтобы не «натягивать» весь диапазон свечей.
_MAX_SPAN_PCT: dict[str, float] = {
    "demand": 4.2,
    "supply": 4.2,
    "ob_bull": 3.0,
    "ob_bear": 3.0,
    "breaker_bull": 3.5,
    "breaker_bear": 3.5,
    "sr_support": 1.8,
    "sr_resistance": 1.8,
}


def _atr_pct(bars: Sequence[KlineBar], period: int = 14) -> float:
    if len(bars) < period + 2:
        return 1.0
    trs: list[float] = []
    for i in range(-period, 0):
        b = bars[i]
        prev = bars[i - 1]
        tr = max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close))
        trs.append(tr)
    ref = float(bars[-1].close) or 1.0
    return (sum(trs) / len(trs) / ref) * 100.0


def _consolidation_base_bounds(window: Sequence[KlineBar]) -> tuple[float, float] | None:
    """Узкая база по телам свечей, не полный high/low окна."""
    if len(window) < 5:
        return None
    ranges = [
        (b.high - b.low) / b.close * 100.0 for b in window if b.close > 0
    ]
    if not ranges:
        return None
    avg_r = sum(ranges) / len(ranges)
    tight = [
        b
        for b in window
        if b.close > 0 and (b.high - b.low) / b.close * 100.0 <= avg_r * 1.35
    ]
    if len(tight) < 4:
        tight = list(window)
    body_top = max(max(b.open, b.close) for b in tight)
    body_bot = min(min(b.open, b.close) for b in tight)
    wick_top = max(b.high for b in tight)
    wick_bot = min(b.low for b in tight)
    body_span = body_top - body_bot
    if body_span <= 0:
        mid = (wick_top + wick_bot) / 2.0
        body_span = max((wick_top - wick_bot) * 0.35, mid * 0.001)
        body_top, body_bot = mid + body_span / 2, mid - body_span / 2
    pad = body_span * 0.18
    top = min(wick_top, body_top + pad)
    bot = max(wick_bot, body_bot - pad)
    if top <= bot:
        return None
    return top, bot


def calibrate_zone_span(
    zone: TradingZone,
    *,
    current: float,
    atr_pct: float,
) -> TradingZone | None:
    """Сжимает слишком широкие зоны; отбрасывает далёкие от цены."""
    if current <= 0 or zone.top <= zone.bottom:
        return None
    mid = zone.mid
    dist_pct = abs(mid - current) / current * 100.0
    if dist_pct > 14.0:
        return None
    span = zone.top - zone.bottom
    cap = _MAX_SPAN_PCT.get(zone.kind, 4.0)
    atr_cap = max(atr_pct * 2.8, 0.35)
    max_span_abs = mid * min(cap, atr_cap * 1.15) / 100.0
    top, bot = zone.top, zone.bottom
    if span > max_span_abs:
        half = max_span_abs / 2.0
        top, bot = mid + half, mid - half
    if (top - bot) / mid * 100.0 > cap * 1.05:
        return None
    return TradingZone(
        kind=zone.kind,
        top=top,
        bottom=bot,
        tf_label=zone.tf_label,
        freshness=zone.freshness,
        valid=zone.valid,
        touch_count=zone.touch_count,
        label_ru=zone.label_ru,
        start_idx=zone.start_idx,
    )


def zone_chart_priority(zone: TradingZone, *, current: float) -> float:
    if current <= 0:
        return 0.0
    score = 0.0
    if zone.valid:
        score += 55.0
    tf = (zone.tf_label or "").upper()
    score += {"W1": 28, "H4": 22, "H1": 16, "LTF": 8}.get(tf, 6)
    score += min(zone.touch_count, 4) * 4.0
    dist = abs(zone.mid - current) / current * 100.0
    score += max(0.0, 22.0 - dist * 2.2)
    if zone.kind in {"demand", "ob_bull", "breaker_bull"} and zone.mid <= current * 1.02:
        score += 6.0
    if zone.kind in {"supply", "ob_bear", "breaker_bear"} and zone.mid >= current * 0.98:
        score += 6.0
    if zone.freshness == "stale":
        score -= 12.0
    if not zone.valid and zone.kind in {"demand", "supply", "ob_bull", "ob_bear"}:
        score -= 40.0
    return score


def select_zones_for_chart(
    zones: Sequence[TradingZone],
    current: float,
    *,
    max_zones: int = 4,
) -> list[TradingZone]:
    """Только зоны для отрисовки: валидные или S/R с касаниями, близко к цене."""
    if current <= 0:
        return []
    candidates: list[TradingZone] = []
    for z in zones:
        if z.kind in {"demand", "supply", "ob_bull", "ob_bear", "breaker_bull", "breaker_bear"}:
            if not z.valid:
                continue
        elif z.kind in {"sr_support", "sr_resistance"}:
            if not z.valid or z.touch_count < 2:
                continue
        else:
            if not z.valid:
                continue
        if abs(z.mid - current) / current * 100.0 > 12.0:
            continue
        candidates.append(z)
    ranked = sorted(
        candidates,
        key=lambda z: zone_chart_priority(z, current=current),
        reverse=True,
    )
    picked: list[TradingZone] = []
    for z in ranked:
        if any(abs(z.mid - p.mid) / max(z.mid, 1e-9) < 0.0035 for p in picked):
            continue
        picked.append(z)
        if len(picked) >= max_zones:
            break
    return picked


@dataclass(frozen=True)
class TradingZone:
    kind: ZoneKind
    top: float
    bottom: float
    tf_label: str
    freshness: Freshness
    valid: bool
    touch_count: int = 0
    label_ru: str = ""
    start_idx: int = 0

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


def _freshness(age_bars: int, *, tested: bool) -> Freshness:
    if age_bars <= 8 and not tested:
        return "fresh"
    if age_bars <= 40:
        return "tested"
    return "stale"


def _base_before_impulse(
    bars: Sequence[KlineBar],
    *,
    min_impulse_pct: float = 0.35,
) -> tuple[TradingZone | None, TradingZone | None]:
    """База консолидации перед импульсом — последний сильный импульс в lookback."""
    if len(bars) < 20:
        return None, None
    demand: TradingZone | None = None
    supply: TradingZone | None = None
    best_up = best_dn = 0.0
    start_i = max(14, len(bars) - 48)
    for idx in range(start_i, len(bars) - 2):
        window = bars[idx - 8 : idx]
        bounds = _consolidation_base_bounds(window)
        if bounds is None:
            continue
        base_top, base_bot = bounds
        imp = bars[idx]
        move_up = (imp.close - base_top) / base_top * 100.0 if base_top else 0.0
        move_dn = (base_bot - imp.close) / base_bot * 100.0 if base_bot else 0.0
        if move_up >= min_impulse_pct and imp.close > imp.open and move_up >= best_up:
            best_up = move_up
            demand = TradingZone(
                kind="demand",
                top=base_top,
                bottom=base_bot,
                tf_label="LTF",
                freshness=_freshness(len(bars) - idx, tested=False),
                valid=False,
                label_ru="спрос (база перед импульсом↑)",
                start_idx=idx - 8,
            )
        if move_dn >= min_impulse_pct and imp.close < imp.open and move_dn >= best_dn:
            best_dn = move_dn
            supply = TradingZone(
                kind="supply",
                top=base_top,
                bottom=base_bot,
                tf_label="LTF",
                freshness=_freshness(len(bars) - idx, tested=False),
                valid=False,
                label_ru="предложение (база перед импульсом↓)",
                start_idx=idx - 8,
            )
    return demand, supply


def _validate_demand_supply(
    bars: Sequence[KlineBar],
    zone: TradingZone,
    *,
    swings: Sequence,
) -> bool:
    """Ликвидность + пробой + откат (упрощённо на OHLC)."""
    if len(bars) < zone.start_idx + 12:
        return False
    after = bars[zone.start_idx :]
    if len(after) < 8:
        return False
    liq = False
    if zone.kind == "demand":
        liq = any(b.low < zone.bottom * 1.002 for b in after[:6])
        brk = any(b.close > zone.top * 1.001 for b in after[3:10])
        pull = any(
            zone.bottom <= b.close <= zone.top * 1.01 for b in after[5:]
        )
    else:
        liq = any(b.high > zone.top * 0.998 for b in after[:6])
        brk = any(b.close < zone.bottom * 0.999 for b in after[3:10])
        pull = any(
            zone.bottom * 0.99 <= b.close <= zone.top for b in after[5:]
        )
    if not liq:
        highs = [s for s in swings if getattr(s, "kind", "") == "high"]
        lows = [s for s in swings if getattr(s, "kind", "") == "low"]
        if zone.kind == "demand" and lows:
            liq = abs(lows[-1].price - zone.bottom) / zone.bottom < 0.015
        if zone.kind == "supply" and highs:
            liq = abs(highs[-1].price - zone.top) / zone.top < 0.015
    return bool(liq and brk and pull)


def _sr_from_swings(
    swings: Sequence,
    *,
    tf_label: str,
    current: float,
) -> list[TradingZone]:
    out: list[TradingZone] = []
    highs = [s for s in swings if getattr(s, "kind", "") == "high"][-4:]
    lows = [s for s in swings if getattr(s, "kind", "") == "low"][-4:]
    for cluster in (lows, highs):
        for s in cluster:
            p = float(s.price)
            tol = p * 0.002
            kind: ZoneKind = "sr_support" if getattr(s, "kind", "") == "low" else "sr_resistance"
            touches = sum(
                1
                for x in cluster
                if abs(float(x.price) - p) <= tol
            )
            if touches < 2:
                continue
            out.append(
                TradingZone(
                    kind=kind,
                    top=p + tol,
                    bottom=p - tol,
                    tf_label=tf_label,
                    freshness="tested" if touches >= 3 else "fresh",
                    valid=touches >= 2,
                    touch_count=touches,
                    label_ru=f"S/R {'поддержка' if kind == 'sr_support' else 'сопр.'} ({touches} кас.)",
                    start_idx=int(getattr(s, "index", 0)),
                )
            )
    return out[:4]


def _htf_demand_supply_bars(
    bars: Sequence[KlineBar] | None,
    tf_label: str,
) -> list[TradingZone]:
    if not bars or len(bars) < 12:
        return []
    d, s = _base_before_impulse(bars, min_impulse_pct=0.8)
    out: list[TradingZone] = []
    for z in (d, s):
        if z is None:
            continue
        out.append(
            TradingZone(
                kind=z.kind,
                top=z.top,
                bottom=z.bottom,
                tf_label=tf_label,
                freshness=z.freshness,
                valid=z.valid,
                label_ru=z.label_ru.replace("LTF", tf_label),
                start_idx=z.start_idx,
            )
        )
    return out


def detect_breaker_zones(
    bars: Sequence[KlineBar],
    order_blocks: Sequence,
) -> list[TradingZone]:
    """Breaker: OB был mitigated, затем close пробил блок — зона для ретеста."""
    out: list[TradingZone] = []
    if len(bars) < 20:
        return out
    for ob in order_blocks:
        if not getattr(ob, "mitigated", False):
            continue
        top, bot = float(ob.top), float(ob.bottom)
        direction = getattr(ob, "direction", "")
        break_idx = int(getattr(ob, "break_idx", 0))
        later = bars[break_idx + 1 :]
        if len(later) < 4:
            continue
        if direction == "bullish":
            if not any(b.close < bot for b in later[:12]):
                continue
            kind: ZoneKind = "breaker_bear"
            label = "breaker (бывший bull OB)"
        else:
            if not any(b.close > top for b in later[:12]):
                continue
            kind = "breaker_bull"
            label = "breaker (бывший bear OB)"
        out.append(
            TradingZone(
                kind=kind,
                top=top,
                bottom=bot,
                tf_label="LTF",
                freshness="fresh",
                valid=True,
                label_ru=label,
                start_idx=break_idx,
            )
        )
    return out[:3]


def build_trading_zones(
    bars: Sequence[KlineBar],
    swings: Sequence,
    *,
    smc: object | None = None,
    weekly_bars: Sequence[KlineBar] | None = None,
    macro_bars: Sequence[KlineBar] | None = None,
    htf_bars: Sequence[KlineBar] | None = None,
    weekly_swings: Sequence | None = None,
    macro_swings: Sequence | None = None,
    htf_swings: Sequence | None = None,
    current: float | None = None,
) -> list[TradingZone]:
    cur = float(current or (bars[-1].close if bars else 0) or 0)
    atr_pct = _atr_pct(list(bars)) if bars else 1.0
    zones: list[TradingZone] = []

    if weekly_swings and cur:
        zones.extend(_sr_from_swings(weekly_swings, tf_label="W1", current=cur))
    zones.extend(_htf_demand_supply_bars(weekly_bars, "W1"))
    if macro_swings and cur:
        zones.extend(_sr_from_swings(macro_swings, tf_label="H4", current=cur))
    zones.extend(_htf_demand_supply_bars(macro_bars, "H4"))
    if htf_swings and cur:
        zones.extend(_sr_from_swings(htf_swings, tf_label="H1", current=cur))
    zones.extend(_htf_demand_supply_bars(htf_bars, "H1"))

    d, s = _base_before_impulse(bars)
    for raw in (d, s):
        if raw is None:
            continue
        valid = _validate_demand_supply(bars, raw, swings=swings)
        zones.append(
            TradingZone(
                kind=raw.kind,
                top=raw.top,
                bottom=raw.bottom,
                tf_label="LTF",
                freshness=raw.freshness,
                valid=valid,
                label_ru=raw.label_ru + (" ✓" if valid else " (не валид.)"),
                start_idx=raw.start_idx,
            )
        )

    if smc is not None:
        for ob in getattr(smc, "order_blocks", []) or []:
            if getattr(ob, "mitigated", False):
                continue
            kind: ZoneKind = "ob_bull" if ob.direction == "bullish" else "ob_bear"
            top, bot = float(ob.top), float(ob.bottom)
            mid = (top + bot) / 2.0
            if mid <= 0:
                continue
            span_pct = (top - bot) / mid * 100.0
            if span_pct > 5.5:
                continue
            ob_valid = span_pct <= 3.2 and cur > 0 and abs(mid - cur) / cur * 100.0 <= 8.0
            zones.append(
                TradingZone(
                    kind=kind,
                    top=top,
                    bottom=bot,
                    tf_label="LTF",
                    freshness="fresh",
                    valid=ob_valid,
                    label_ru="OB " + ("спрос" if kind == "ob_bull" else "предложение")
                    + ("" if ob_valid else " (слаб.)"),
                    start_idx=int(ob.start_idx),
                )
            )
        zones.extend(
            detect_breaker_zones(bars, getattr(smc, "order_blocks", []) or [])
        )

    if swings and cur:
        zones.extend(_sr_from_swings(swings, tf_label="LTF", current=cur))

    calibrated: list[TradingZone] = []
    for z in zones:
        cz = calibrate_zone_span(z, current=cur, atr_pct=atr_pct)
        if cz is not None:
            calibrated.append(cz)

    uniq: list[TradingZone] = []
    for z in calibrated:
        if any(abs(z.mid - u.mid) / max(z.mid, 1e-9) < 0.004 and z.kind == u.kind for u in uniq):
            continue
        uniq.append(z)
    return uniq[:14]


def zones_near_price(zones: Sequence[TradingZone], price: float, *, pct: float = 2.5) -> list[TradingZone]:
    if price <= 0:
        return []
    return [
        z
        for z in zones
        if abs(z.mid - price) / price * 100.0 <= pct
    ]
