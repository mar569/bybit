"""Free liquidation-magnet heuristic (CoinGlass heatmap proxy).

Uses swing/equal-high-low clusters + recent live liq stats as stop magnets:
price often travels toward dense stop pools before continuing or reversing.
Not a paid heatmap API — approximate levels for factors / AI context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bybit_klines import KlineBar

if False:  # TYPE_CHECKING
    from .ta_analysis import SwingPoint


@dataclass(frozen=True)
class LiqMagnetZone:
    price: float
    side: str  # above | below
    kind: str  # equal_highs | equal_lows | swing_high | swing_low | day_high | day_low | session
    strength: float  # 0..1 relative density
    dist_pct: float
    label: str


@dataclass
class LiqMagnetContext:
    bias: str = "neutral"  # hunt_longs_below | hunt_shorts_above | both | neutral
    bias_label: str = ""
    nearest_above: float | None = None
    nearest_below: float | None = None
    strength: float = 0.0
    note: str = ""
    zones: list[LiqMagnetZone] = field(default_factory=list)
    factor_line: str = ""
    plan_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias,
            "bias_label": self.bias_label,
            "nearest_above": self.nearest_above,
            "nearest_below": self.nearest_below,
            "strength": round(self.strength, 3),
            "note": self.note,
            "factor_line": self.factor_line,
            "plan_hint": self.plan_hint,
            "zones": [
                {
                    "price": z.price,
                    "side": z.side,
                    "kind": z.kind,
                    "strength": round(z.strength, 3),
                    "dist_pct": round(z.dist_pct, 3),
                    "label": z.label,
                }
                for z in self.zones[:8]
            ],
        }


def _atr_pct(bars: list[KlineBar], period: int = 14) -> float:
    if len(bars) < 2:
        return 1.0
    n = min(period, len(bars) - 1)
    trs: list[float] = []
    for i in range(-n, 0):
        b = bars[i]
        prev = bars[i - 1]
        tr = max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close))
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else 0.0
    px = bars[-1].close
    if px <= 0 or atr <= 0:
        return 1.0
    return max(0.15, atr / px * 100.0)


def _cluster_swings(
    swings: list,
    *,
    kind: str,
    tol_pct: float,
) -> list[tuple[float, int]]:
    pts = [s for s in swings if getattr(s, "kind", "") == kind]
    if not pts:
        return []
    clusters: list[list[float]] = []
    for p in sorted(pts, key=lambda s: s.price):
        price = float(p.price)
        if clusters and abs(price - clusters[-1][-1]) / max(price, 1e-12) * 100 <= tol_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    out: list[tuple[float, int]] = []
    for c in clusters:
        avg = sum(c) / len(c)
        out.append((avg, len(c)))
    return out


def analyze_liq_magnet(
    bars: list[KlineBar],
    swings: list | None = None,
    *,
    liq_context: dict[str, Any] | None = None,
    smc_liquidity: list | None = None,
    max_dist_pct: float | None = None,
) -> LiqMagnetContext:
    """Estimate stop-hunt magnets above/below current price."""
    if not bars:
        return LiqMagnetContext(note="нет свечей")

    current = float(bars[-1].close)
    if current <= 0:
        return LiqMagnetContext(note="нет цены")

    atr_p = _atr_pct(bars)
    reach = max_dist_pct if max_dist_pct is not None else min(8.0, max(2.5, atr_p * 3.5))
    tol = max(0.12, atr_p * 0.35)

    from .ta_analysis import find_swing_points

    sw = list(swings) if swings is not None else find_swing_points(bars)
    zones: list[LiqMagnetZone] = []

    for avg, count in _cluster_swings(sw, kind="high", tol_pct=tol):
        if avg <= current:
            continue
        dist = (avg - current) / current * 100.0
        if dist > reach:
            continue
        strength = min(1.0, 0.35 + 0.2 * (count - 1) + max(0.0, 1.2 - dist / max(reach, 0.1)))
        label = f"equal highs×{count}" if count >= 2 else "swing high"
        kind = "equal_highs" if count >= 2 else "swing_high"
        zones.append(LiqMagnetZone(avg, "above", kind, strength, dist, label))

    for avg, count in _cluster_swings(sw, kind="low", tol_pct=tol):
        if avg >= current:
            continue
        dist = (current - avg) / current * 100.0
        if dist > reach:
            continue
        strength = min(1.0, 0.35 + 0.2 * (count - 1) + max(0.0, 1.2 - dist / max(reach, 0.1)))
        label = f"equal lows×{count}" if count >= 2 else "swing low"
        kind = "equal_lows" if count >= 2 else "swing_low"
        zones.append(LiqMagnetZone(avg, "below", kind, strength, dist, label))

    day_n = min(len(bars), 288)  # ~24h on 5m
    day = bars[-day_n:]
    day_high = max(b.high for b in day)
    day_low = min(b.low for b in day)
    if day_high > current:
        dist = (day_high - current) / current * 100.0
        if dist <= reach:
            zones.append(
                LiqMagnetZone(
                    day_high, "above", "day_high",
                    min(1.0, 0.55 + max(0.0, 0.4 - dist / reach * 0.4)),
                    dist, "макс. сессии",
                )
            )
    if day_low < current:
        dist = (current - day_low) / current * 100.0
        if dist <= reach:
            zones.append(
                LiqMagnetZone(
                    day_low, "below", "day_low",
                    min(1.0, 0.55 + max(0.0, 0.4 - dist / reach * 0.4)),
                    dist, "мин. сессии",
                )
            )

    if smc_liquidity:
        for lv in smc_liquidity:
            price = float(getattr(lv, "price", 0) or 0)
            if price <= 0:
                continue
            kind = str(getattr(lv, "kind", "") or "")
            label = str(getattr(lv, "label", "") or kind)
            if price > current:
                dist = (price - current) / current * 100.0
                if dist <= reach:
                    boost = 0.15 if "equal" in kind else 0.05
                    zones.append(
                        LiqMagnetZone(
                            price, "above", kind or "smc_high",
                            min(1.0, 0.45 + boost), dist, label or "SMC↑",
                        )
                    )
            elif price < current:
                dist = (current - price) / current * 100.0
                if dist <= reach:
                    boost = 0.15 if "equal" in kind else 0.05
                    zones.append(
                        LiqMagnetZone(
                            price, "below", kind or "smc_low",
                            min(1.0, 0.45 + boost), dist, label or "SMC↓",
                        )
                    )

    # Deduplicate near-identical prices
    zones = _dedupe_zones(zones, tol_pct=tol)
    above = sorted([z for z in zones if z.side == "above"], key=lambda z: z.dist_pct)
    below = sorted([z for z in zones if z.side == "below"], key=lambda z: z.dist_pct)

    score_above = _side_score(above)
    score_below = _side_score(below)

    # Live liq bias: heavy long liq → longs already flushed below; shorts above become next magnet
    # heavy short liq → shorts flushed above; longs below become next magnet
    long_liq = float((liq_context or {}).get("long_liq_usd") or 0)
    short_liq = float((liq_context or {}).get("short_liq_usd") or 0)
    total_liq = long_liq + short_liq
    liq_note = ""
    if total_liq >= 25_000:
        if long_liq >= short_liq * 1.35 and long_liq >= 20_000:
            score_above += 0.18
            liq_note = f"live long-liq ${long_liq/1000:.0f}K → следующий магнит часто шорты сверху"
        elif short_liq >= long_liq * 1.35 and short_liq >= 20_000:
            score_below += 0.18
            liq_note = f"live short-liq ${short_liq/1000:.0f}K → следующий магнит часто лонги снизу"

    nearest_above = above[0].price if above else None
    nearest_below = below[0].price if below else None

    bias = "neutral"
    bias_label = "магниты сбалансированы / далеко"
    if score_above >= 0.45 or score_below >= 0.45:
        if score_above > score_below * 1.15 and nearest_above is not None:
            bias = "hunt_shorts_above"
            bias_label = "магнит: снять шорты сверху"
        elif score_below > score_above * 1.15 and nearest_below is not None:
            bias = "hunt_longs_below"
            bias_label = "магнит: снять лонги снизу"
        elif nearest_above is not None and nearest_below is not None:
            bias = "both"
            bias_label = "магниты с обеих сторон"
        elif nearest_above is not None:
            bias = "hunt_shorts_above"
            bias_label = "магнит сверху"
        elif nearest_below is not None:
            bias = "hunt_longs_below"
            bias_label = "магнит снизу"

    strength = max(score_above, score_below, (score_above + score_below) / 2)
    parts: list[str] = []
    if nearest_above is not None and above:
        parts.append(f"↑{above[0].label} {_fmt(nearest_above)} ({above[0].dist_pct:.2f}%)")
    if nearest_below is not None and below:
        parts.append(f"↓{below[0].label} {_fmt(nearest_below)} ({below[0].dist_pct:.2f}%)")
    factor_line = ""
    if parts:
        factor_line = f"🧲 Liq magnet: {bias_label} · " + " · ".join(parts)

    plan_hint = ""
    if bias == "hunt_shorts_above" and nearest_above is not None:
        plan_hint = (
            f"Сценарий 1–3ч: возможен sweep шортов к {_fmt(nearest_above)}, "
            "после съёма — либо продолжение вверх, либо разворот вниз. Не ловить середину wick."
        )
    elif bias == "hunt_longs_below" and nearest_below is not None:
        plan_hint = (
            f"Сценарий 1–3ч: возможен sweep лонгов к {_fmt(nearest_below)}, "
            "после съёма — либо продолжение вниз, либо отскок. Стоп за зоной магнита."
        )
    elif bias == "both" and nearest_above is not None and nearest_below is not None:
        plan_hint = (
            f"Диапазон магнитов {_fmt(nearest_below)}–{_fmt(nearest_above)}: "
            "сначала вероятен hunt ближайшей стороны, вход после подтверждения съёма."
        )

    note = " · ".join(x for x in (bias_label, liq_note, "; ".join(parts[:2])) if x)
    ranked = sorted(zones, key=lambda z: (-z.strength, z.dist_pct))[:8]
    return LiqMagnetContext(
        bias=bias,
        bias_label=bias_label,
        nearest_above=nearest_above,
        nearest_below=nearest_below,
        strength=min(1.0, strength),
        note=note[:280],
        zones=ranked,
        factor_line=factor_line[:320],
        plan_hint=plan_hint[:320],
    )


def _side_score(zones: list[LiqMagnetZone]) -> float:
    if not zones:
        return 0.0
    best = 0.0
    for i, z in enumerate(zones[:4]):
        # closer + denser = higher; farther zones decay
        decay = 1.0 / (1.0 + z.dist_pct / 2.5)
        score = z.strength * decay * (1.0 - 0.08 * i)
        best = max(best, score)
    return best


def _dedupe_zones(zones: list[LiqMagnetZone], *, tol_pct: float) -> list[LiqMagnetZone]:
    if not zones:
        return []
    ordered = sorted(zones, key=lambda z: (-z.strength, z.dist_pct))
    kept: list[LiqMagnetZone] = []
    for z in ordered:
        if any(abs(z.price - k.price) / max(z.price, 1e-12) * 100 <= tol_pct for k in kept):
            continue
        kept.append(z)
    return kept


def _fmt(price: float) -> str:
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.5f}"
    return f"{price:.7g}"
