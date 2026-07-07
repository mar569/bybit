"""Технический анализ OHLC: уровни, тренды, паттерны, линейка %, BTC-контекст."""
from __future__ import annotations

from dataclasses import dataclass, field

from .bybit_klines import KlineBar
from .market_structure import FiveMinOiBar, MarketStructureContext, analyze_market_structure
from .smc_analysis import SmcContext, analyze_smc, format_smc_compact_html, smc_verdict_boost
from .ta_range_trade import RangeTradeSetup, build_factor_context, evaluate_range_trade


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    kind: str


@dataclass(frozen=True)
class HorizontalLevel:
    price: float
    label: str
    kind: str
    touches: int


@dataclass(frozen=True)
class TrendLine:
    start_idx: int
    start_price: float
    end_idx: int
    end_price: float
    label: str
    kind: str


@dataclass(frozen=True)
class ConsolidationZone:
    top: float
    bottom: float
    start_idx: int
    end_idx: int
    label: str


@dataclass(frozen=True)
class CandlePattern:
    index: int
    name: str
    label_ru: str
    bullish: bool | None


@dataclass(frozen=True)
class RulerMeasurement:
    start_idx: int
    end_idx: int
    from_price: float
    to_price: float
    pct: float
    label: str


@dataclass(frozen=True)
class PriceZone:
    top: float
    bottom: float
    kind: str
    label: str
    touches: int


@dataclass(frozen=True)
class PriceChannel:
    upper_start_idx: int
    upper_start_price: float
    upper_end_idx: int
    upper_end_price: float
    lower_start_idx: int
    lower_start_price: float
    lower_end_idx: int
    lower_end_price: float
    kind: str
    label: str


@dataclass(frozen=True)
class TradeSignalMarker:
    index: int
    price: float
    side: str
    label: str


@dataclass(frozen=True)
class TradeScenario:
    direction: str
    trigger_price: float
    trigger_label: str
    stop_price: float
    target_prices: list[float]
    conditions: list[str]


@dataclass(frozen=True)
class KeyLevel:
    price: float
    role: str
    label: str


@dataclass
class TAAnalysisResult:
    swings: list[SwingPoint] = field(default_factory=list)
    levels: list[HorizontalLevel] = field(default_factory=list)
    trend_lines: list[TrendLine] = field(default_factory=list)
    zones: list[PriceZone] = field(default_factory=list)
    channel: PriceChannel | None = None
    signal_markers: list[TradeSignalMarker] = field(default_factory=list)
    consolidation: ConsolidationZone | None = None
    patterns: list[CandlePattern] = field(default_factory=list)
    rulers: list[RulerMeasurement] = field(default_factory=list)
    key_levels: list[KeyLevel] = field(default_factory=list)
    bullish_scenario: TradeScenario | None = None
    bearish_scenario: TradeScenario | None = None
    trader_plan: list[str] = field(default_factory=list)
    structure_label: str = ""
    phase: str = ""
    phase_label: str = ""
    verdict: str = "WAIT"
    verdict_confidence: int = 5
    verdict_reason: str = ""
    btc_context: str = ""
    breakout_level: float | None = None
    breakdown_level: float | None = None
    invalidation_price: float | None = None
    entry_zone: tuple[float, float] | None = None
    target_prices: list[float] = field(default_factory=list)
    current_price: float = 0.0
    oi_narrative_label: str = ""
    range_position: float = 0.5
    phase_detail: str = ""
    drawdown_from_high_pct: float = 0.0
    market_bias: str = ""
    setup_clarity: int = 5
    risk_notes: list[str] = field(default_factory=list)
    professional_summary: str = ""
    momentum_label: str = ""
    momentum_pct: float = 0.0
    btc_alt_spread: float | None = None
    action_priority: str = "neutral"
    range_trade_label: str = ""
    entry_mode: str = "breakout"
    factor_lines: list[str] = field(default_factory=list)
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    dist_to_long_pct: float | None = None
    dist_to_short_pct: float | None = None
    post_pump: bool = False
    primary_scenario: str = ""
    smc: SmcContext | None = None
    smc_score: int = 0
    smc_summary: str = ""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fmt_price(price: float) -> str:
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.5f}"
    return f"{price:.7g}"


def find_swing_points(bars: list[KlineBar], *, window: int = 2) -> list[SwingPoint]:
    if len(bars) < window * 2 + 1:
        return []
    swings: list[SwingPoint] = []
    for i in range(window, len(bars) - window):
        seg_h = [bars[j].high for j in range(i - window, i + window + 1)]
        seg_l = [bars[j].low for j in range(i - window, i + window + 1)]
        if bars[i].high >= max(seg_h):
            swings.append(SwingPoint(i, bars[i].high, "high"))
        elif bars[i].low <= min(seg_l):
            swings.append(SwingPoint(i, bars[i].low, "low"))
    return swings


def _cluster_price_levels(
    prices: list[float],
    ref_price: float,
    *,
    tolerance_pct: float = 0.35,
) -> list[tuple[float, int]]:
    if not prices or ref_price <= 0:
        return []
    tol = ref_price * tolerance_pct / 100.0
    clusters: list[list[float]] = []
    for p in sorted(prices):
        placed = False
        for cluster in clusters:
            if abs(p - cluster[0]) <= tol:
                cluster.append(p)
                placed = True
                break
        if not placed:
            clusters.append([p])
    return [(sum(c) / len(c), len(c)) for c in clusters if len(c) >= 1]


def detect_horizontal_levels(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    *,
    max_levels: int = 4,
) -> list[HorizontalLevel]:
    if not bars:
        return []
    current = bars[-1].close
    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]
    levels: list[HorizontalLevel] = []
    for price, touches in _cluster_price_levels(highs, current):
        if price >= current * 0.998:
            levels.append(HorizontalLevel(price, f"R {price:.5g}", "resistance", touches))
    for price, touches in _cluster_price_levels(lows, current):
        if price <= current * 1.002:
            levels.append(HorizontalLevel(price, f"S {price:.5g}", "support", touches))
    levels.sort(key=lambda lv: abs(lv.price - current))
    seen: set[float] = set()
    unique: list[HorizontalLevel] = []
    for lv in levels:
        key = round(lv.price, 6)
        if key in seen:
            continue
        seen.add(key)
        unique.append(lv)
        if len(unique) >= max_levels:
            break
    return unique


def detect_trend_lines(bars: list[KlineBar], swings: list[SwingPoint]) -> list[TrendLine]:
    if len(bars) < 10:
        return []
    lines: list[TrendLine] = []
    lows = [s for s in swings if s.kind == "low"]
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if b.price >= a.price * 0.995 and b.index > a.index:
            lines.append(TrendLine(a.index, a.price, b.index, b.price, "тренд ↑", "bull"))
    highs = [s for s in swings if s.kind == "high"]
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if b.price <= a.price * 1.005 and b.index > a.index:
            lines.append(TrendLine(a.index, a.price, b.index, b.price, "тренд ↓", "bear"))
    return lines[:2]


def detect_price_zones(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    *,
    max_zones: int = 2,
) -> list[PriceZone]:
    if not bars:
        return []
    current = bars[-1].close
    tol = current * 0.0035
    zones: list[PriceZone] = []

    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]

    for price, touches in _cluster_price_levels(highs, current, tolerance_pct=0.45):
        if price < current * 0.995:
            continue
        zones.append(PriceZone(
            top=price + tol,
            bottom=price - tol,
            kind="resistance",
            label="зона сопр." if touches >= 2 else "лок. сопр.",
            touches=touches,
        ))
    for price, touches in _cluster_price_levels(lows, current, tolerance_pct=0.45):
        if price > current * 1.005:
            continue
        zones.append(PriceZone(
            top=price + tol,
            bottom=price - tol,
            kind="support",
            label="зона подд." if touches >= 2 else "лок. подд.",
            touches=touches,
        ))

    zones.sort(key=lambda z: abs((z.top + z.bottom) / 2 - current))
    unique: list[PriceZone] = []
    seen: set[str] = set()
    for z in zones:
        key = f"{z.kind}:{round((z.top + z.bottom) / 2, 6)}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(z)
        if len(unique) >= max_zones:
            break
    return unique


def detect_channel(bars: list[KlineBar], swings: list[SwingPoint]) -> PriceChannel | None:
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    bear = h2.price < h1.price * 0.998 and l2.price <= l1.price * 1.003
    bull = l2.price > l1.price * 1.002 and h2.price >= h1.price * 0.997
    if not bear and not bull:
        return None

    kind = "bear" if bear else "bull"
    label = "канал ↓" if bear else "канал ↑"
    return PriceChannel(
        upper_start_idx=h1.index,
        upper_start_price=h1.price,
        upper_end_idx=h2.index,
        upper_end_price=h2.price,
        lower_start_idx=l1.index,
        lower_start_price=l1.price,
        lower_end_idx=l2.index,
        lower_end_price=l2.price,
        kind=kind,
        label=label,
    )


def detect_signal_markers(
    bars: list[KlineBar],
    levels: list[HorizontalLevel],
) -> list[TradeSignalMarker]:
    if len(bars) < 3:
        return []
    markers: list[TradeSignalMarker] = []
    lookback = min(12, len(bars) - 1)

    for i in range(len(bars) - lookback, len(bars)):
        bar = bars[i]
        prev = bars[i - 1]
        for lv in levels:
            if lv.kind == "resistance" and prev.close <= lv.price <= prev.high:
                if bar.close > lv.price * 1.0005:
                    markers.append(TradeSignalMarker(i, lv.price, "buy", "B"))
            if lv.kind == "support" and prev.close >= lv.price >= prev.low:
                if bar.close < lv.price * 0.9995:
                    markers.append(TradeSignalMarker(i, lv.price, "sell", "S"))

    deduped: list[TradeSignalMarker] = []
    seen: set[tuple[int, str]] = set()
    for m in markers:
        key = (m.index, m.side)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
    return deduped[-5:]


def _distance_pct(current: float, level: float | None) -> float | None:
    if not level or current <= 0:
        return None
    return abs(level - current) / current * 100.0


def detect_post_pump_phase(bars: list[KlineBar], *, lookback: int = 30) -> bool:
    """Резкий рост в окне + цена держится у хая (консолидация после пампа)."""
    if len(bars) < lookback + 5:
        return False
    seg = bars[-lookback:]
    start = seg[0].open
    peak = max(b.high for b in seg)
    current = bars[-1].close
    if start <= 0 or peak <= 0:
        return False
    pump_pct = (peak - start) / start * 100.0
    off_peak = (peak - current) / peak * 100.0
    return pump_pct >= 6.0 and off_peak <= 12.0 and current >= peak * 0.80


def detect_local_consolidation(
    bars: list[KlineBar],
    *,
    lookback: int = 36,
    max_range_pct: float = 6.5,
) -> ConsolidationZone | None:
    if len(bars) < 10:
        return None
    lb = min(lookback, len(bars))
    segment = bars[-lb:]
    high = max(b.high for b in segment)
    low = min(b.low for b in segment)
    mid = (high + low) / 2.0
    if mid <= 0:
        return None
    range_pct = (high - low) / mid * 100.0
    if range_pct > max_range_pct:
        return None
    return ConsolidationZone(
        top=high,
        bottom=low,
        start_idx=len(bars) - lb,
        end_idx=len(bars) - 1,
        label=f"лок. боковик {range_pct:.1f}%",
    )


def detect_local_swing_levels(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    *,
    lookback: int = 48,
    max_dist_pct: float = 7.0,
) -> tuple[float | None, float | None]:
    if not bars:
        return None, None
    current = bars[-1].close
    cutoff = max(0, len(bars) - lookback)
    recent = [s for s in swings if s.index >= cutoff]
    resistances = sorted(
        {s.price for s in recent if s.kind == "high" and s.price > current * 1.0003},
    )
    supports = sorted(
        {s.price for s in recent if s.kind == "low" and s.price < current * 0.9997},
        reverse=True,
    )
    max_dist = current * max_dist_pct / 100.0

    def _nearest_above(levels: list[float]) -> float | None:
        near = [p for p in levels if 0 < p - current <= max_dist]
        if near:
            return near[0]
        return levels[0] if levels else None

    def _nearest_below(levels: list[float]) -> float | None:
        near = [p for p in levels if 0 < current - p <= max_dist]
        if near:
            return near[0]
        return levels[0] if levels else None

    return _nearest_above(resistances), _nearest_below(supports)


def resolve_trade_triggers(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    levels: list[HorizontalLevel],
    zones: list[PriceZone],
    key_levels: list[KeyLevel],
) -> tuple[float | None, float | None, ConsolidationZone | None, bool]:
    """Локальные триггеры LONG/SHORT + боковик после пампа."""
    if not bars:
        return None, None, None, False

    current = bars[-1].close
    post_pump = detect_post_pump_phase(bars)
    local_cons = detect_local_consolidation(
        bars,
        lookback=32 if post_pump else 42,
        max_range_pct=8.5 if post_pump else 6.5,
    )
    local_r, local_s = detect_local_swing_levels(bars, swings)

    resistances = sorted(
        {lv.price for lv in levels if lv.kind == "resistance" and lv.price > current * 1.001},
    )
    supports = sorted(
        {lv.price for lv in levels if lv.kind == "support" and lv.price < current * 0.999},
        reverse=True,
    )
    for z in zones:
        mid = (z.top + z.bottom) / 2
        if z.kind == "resistance" and mid > current and mid not in resistances:
            resistances.append(mid)
        if z.kind == "support" and mid < current and mid not in supports:
            supports.append(mid)
    resistances.sort()
    supports.sort(reverse=True)

    breakout = next((kl.price for kl in key_levels if kl.role == "breakout"), None)
    if breakout is None and resistances:
        breakout = resistances[0]
    breakdown = next((kl.price for kl in key_levels if kl.role == "breakdown"), None)
    if breakdown is None and supports:
        breakdown = supports[0]

    max_dist = 7.0 if post_pump else 11.0
    if breakout and (_distance_pct(current, breakout) or 999) > max_dist:
        breakout = local_r or (local_cons.top if local_cons else breakout)
    if breakdown and (_distance_pct(current, breakdown) or 999) > max_dist:
        breakdown = local_s or (local_cons.bottom if local_cons else breakdown)

    if post_pump and local_cons:
        breakout = local_cons.top
        breakdown = local_cons.bottom
    elif local_cons and not breakout and not breakdown:
        breakout = local_cons.top
        breakdown = local_cons.bottom

    if not breakout and local_r:
        breakout = local_r
    if not breakdown and local_s:
        breakdown = local_s

    cons = local_cons if post_pump and local_cons else None
    return breakout, breakdown, cons, post_pump


def build_key_levels(
    bars: list[KlineBar],
    levels: list[HorizontalLevel],
    zones: list[PriceZone],
) -> list[KeyLevel]:
    if not bars:
        return []
    current = bars[-1].close
    result: list[KeyLevel] = []

    resistances = sorted(
        [lv for lv in levels if lv.kind == "resistance"],
        key=lambda lv: lv.price,
    )
    supports = sorted(
        [lv for lv in levels if lv.kind == "support"],
        key=lambda lv: lv.price,
        reverse=True,
    )

    breakout = next((lv for lv in resistances if lv.price > current * 1.001), None)
    if breakout:
        result.append(KeyLevel(breakout.price, "breakout", "пробой ↑"))
    breakdown = next((lv for lv in supports if lv.price < current * 0.999), None)
    if breakdown:
        result.append(KeyLevel(breakdown.price, "breakdown", "пробой ↓"))

    for z in zones:
        mid = (z.top + z.bottom) / 2
        role = "strong_resistance" if z.kind == "resistance" else "strong_support"
        result.append(KeyLevel(mid, role, z.label))

    if supports:
        nearest_s = next((lv for lv in supports if lv.price < current * 0.999), supports[0] if supports else None)
        if nearest_s:
            result.append(KeyLevel(nearest_s.price, "nearest_support", "поддержка"))
    if resistances:
        nearest_r = next((lv for lv in resistances if lv.price > current * 1.001), resistances[-1] if resistances else None)
        if nearest_r:
            result.append(KeyLevel(nearest_r.price, "nearest_resistance", "сопротивление"))

    seen: set[float] = set()
    unique: list[KeyLevel] = []
    for kl in result:
        key = round(kl.price, 8)
        if key in seen:
            continue
        seen.add(key)
        unique.append(kl)
    return unique[:6]


def build_scenarios(
    bars: list[KlineBar],
    levels: list[HorizontalLevel],
    zones: list[PriceZone],
    key_levels: list[KeyLevel],
    swings: list[SwingPoint] | None = None,
) -> tuple[TradeScenario | None, TradeScenario | None, float | None, float | None]:
    if not bars:
        return None, None, None, None

    swings = swings or []
    current = bars[-1].close
    breakout, breakdown, _, _ = resolve_trade_triggers(bars, swings, levels, zones, key_levels)

    resistances = sorted(
        {lv.price for lv in levels if lv.kind == "resistance" and lv.price > current * 1.001},
    )
    supports = sorted(
        {lv.price for lv in levels if lv.kind == "support" and lv.price < current * 0.999},
        reverse=True,
    )
    for z in zones:
        mid = (z.top + z.bottom) / 2
        if z.kind == "resistance" and mid > current and mid not in resistances:
            resistances.append(mid)
        if z.kind == "support" and mid < current and mid not in supports:
            supports.append(mid)
    resistances.sort()
    supports.sort(reverse=True)

    bull_targets = [p for p in resistances if breakout and p > breakout * 1.0005][:4]
    if not bull_targets and breakout:
        bull_targets = [breakout * 1.01, breakout * 1.02, breakout * 1.035]
    if not bull_targets:
        bull_targets = [current * 1.015, current * 1.03, current * 1.045, current * 1.06]

    bear_targets = [p for p in supports if breakdown and p < breakdown * 0.9995][:4]
    if not bear_targets and breakdown:
        bear_targets = [breakdown * 0.99, breakdown * 0.98, breakdown * 0.965]
    if not bear_targets:
        bear_targets = [current * 0.985, current * 0.97, current * 0.955, current * 0.94]

    stop_long = (breakdown or current * 0.98) * 0.995
    stop_short = (breakout or current * 1.02) * 1.005

    bullish: TradeScenario | None = None
    bearish: TradeScenario | None = None

    if breakout:
        bullish = TradeScenario(
            direction="long",
            trigger_price=breakout,
            trigger_label="пробой и закрепление выше",
            stop_price=stop_long,
            target_prices=bull_targets,
            conditions=[
                f"закрепление 5m/15m выше {fmt_price(breakout)}",
                "объём выше среднего на пробое",
                "ретест уровня снизу без слома",
                "OI не падает на росте",
            ],
        )
    if breakdown:
        bearish = TradeScenario(
            direction="short",
            trigger_price=breakdown,
            trigger_label="пробой ниже поддержки",
            stop_price=stop_short,
            target_prices=bear_targets,
            conditions=[
                f"закрытие свечи ниже {fmt_price(breakdown)}",
                "отбой от сопротивления / слабый рост",
                "CVD не подтверждает покупки",
                "OI растёт при падении (новые шорты)",
            ],
        )

    return bullish, bearish, breakout, breakdown


def build_trader_plan(
    *,
    verdict: str,
    bullish: TradeScenario | None,
    bearish: TradeScenario | None,
    breakout: float | None,
    breakdown: float | None,
    entry_zone: tuple[float, float] | None,
    consolidation: ConsolidationZone | None = None,
    supports: list[float] | None = None,
    action_priority: str = "neutral",
) -> list[str]:
    plan: list[str] = []
    trigger = breakout or (bullish.trigger_price if bullish else None)
    inv = bearish.trigger_price if bearish else breakdown
    supports = supports or []

    if verdict == "WAIT":
        plan.append("Не входить в середине движения — ждать край range")
    elif verdict == "LONG":
        plan.append("Приоритет LONG — вход только по триггеру")
    elif verdict == "SHORT":
        plan.append("Приоритет SHORT — вход только по триггеру")

    if trigger:
        plan.append(f"LONG: покупка при пробое {fmt_price(trigger)} вверх")
        plan.append("Вход после ретеста уровня (не в пик импульса)")
        if bullish:
            tps = " / ".join(fmt_price(t) for t in bullish.target_prices[:3])
            plan.append(f"Цели LONG: {tps}")
            plan.append(f"Стоп LONG: ниже {fmt_price(bullish.stop_price)}")

    if consolidation and supports:
        lo = min(supports[0], consolidation.bottom) if supports else consolidation.bottom
        hi = consolidation.bottom + (consolidation.top - consolidation.bottom) * 0.35
        plan.append(f"Альт. LONG: откат в зону {fmt_price(lo)}–{fmt_price(hi)}")
    elif entry_zone and verdict != "SHORT":
        plan.append(f"Зона набора: {fmt_price(entry_zone[0])}–{fmt_price(entry_zone[1])}")

    if inv:
        plan.append(f"SHORT: если пробой {fmt_price(inv)} вниз")
        if bearish:
            stps = " / ".join(fmt_price(t) for t in bearish.target_prices[:3])
            plan.append(f"Цели SHORT: {stps}")
            plan.append(f"Стоп SHORT: выше {fmt_price(bearish.stop_price)}")

    if action_priority == "short":
        short_steps = [s for s in plan if "SHORT" in s or "short" in s.lower()]
        long_steps = [s for s in plan if s not in short_steps]
        plan = short_steps + long_steps
    elif action_priority == "long":
        long_steps = [s for s in plan if "LONG" in s or "long" in s.lower() or "Зона" in s]
        short_steps = [s for s in plan if s not in long_steps]
        plan = long_steps + short_steps

    return plan[:8]


def detect_consolidation(bars: list[KlineBar], *, lookback: int = 18) -> ConsolidationZone | None:
    if len(bars) < lookback:
        return None
    segment = bars[-lookback:]
    high = max(b.high for b in segment)
    low = min(b.low for b in segment)
    mid = (high + low) / 2.0
    if mid <= 0:
        return None
    range_pct = (high - low) / mid * 100.0
    if range_pct > 3.2:
        return None
    return ConsolidationZone(
        top=high,
        bottom=low,
        start_idx=len(bars) - lookback,
        end_idx=len(bars) - 1,
        label=f"боковик {range_pct:.1f}%",
    )


def _body_size(bar: KlineBar) -> float:
    return abs(bar.close - bar.open)


def _range_size(bar: KlineBar) -> float:
    return max(bar.high - bar.low, 1e-12)


def _is_bull(bar: KlineBar) -> bool:
    return bar.close >= bar.open


def detect_candle_patterns(bars: list[KlineBar], *, lookback: int = 5) -> list[CandlePattern]:
    if len(bars) < 2:
        return []
    patterns: list[CandlePattern] = []
    start = max(1, len(bars) - lookback)
    for i in range(start, len(bars)):
        bar = bars[i]
        body = _body_size(bar)
        rng = _range_size(bar)
        upper = bar.high - max(bar.open, bar.close)
        lower = min(bar.open, bar.close) - bar.low
        if rng > 0 and body / rng < 0.12:
            patterns.append(CandlePattern(i, "doji", "доджи", None))
            continue
        if body > 0 and lower >= body * 2.0 and upper <= body * 0.5:
            patterns.append(CandlePattern(i, "hammer", "молот", True if not _is_bull(bar) else None))
        elif body > 0 and upper >= body * 2.0 and lower <= body * 0.5:
            patterns.append(CandlePattern(i, "inverted_hammer", "перев. молот", True))
        elif body > 0 and upper >= body * 2.5 and lower <= body * 0.3:
            patterns.append(CandlePattern(i, "pin_bar", "пин-бар", False))
        if i >= 1:
            prev = bars[i - 1]
            prev_body = _body_size(prev)
            if prev_body > 0 and body > prev_body * 1.1:
                if not _is_bull(prev) and _is_bull(bar) and bar.close > prev.open and bar.open < prev.close:
                    patterns.append(CandlePattern(i, "bull_engulf", "быч. поглощение", True))
                elif _is_bull(prev) and not _is_bull(bar) and bar.close < prev.open and bar.open > prev.close:
                    patterns.append(CandlePattern(i, "bear_engulf", "медв. поглощение", False))
    return patterns[-3:]


def compute_rulers(bars: list[KlineBar], swings: list[SwingPoint]) -> list[RulerMeasurement]:
    if len(bars) < 3:
        return []
    rulers: list[RulerMeasurement] = []
    current = bars[-1].close
    peak = max(b.high for b in bars)
    trough = min(b.low for b in bars)
    last = bars[-1]
    if peak > 0:
        dd = (peak - current) / peak * 100.0
        peak_idx = next(i for i, b in enumerate(bars) if b.high == peak)
        rulers.append(RulerMeasurement(peak_idx, len(bars) - 1, peak, current, -dd, f"−{dd:.1f}% от хая"))
    candle_drop = (last.open - last.close) / last.open * 100.0 if last.open > 0 else 0.0
    if abs(candle_drop) >= 0.15:
        rulers.append(
            RulerMeasurement(len(bars) - 1, len(bars) - 1, last.open, last.close, candle_drop, f"свеча {candle_drop:+.1f}%")
        )
    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]
    if lows and highs:
        imp_low = lows[-1]
        imp_high = max((s for s in highs if s.index >= imp_low.index), key=lambda s: s.price, default=None)
        if imp_high and imp_high.price > imp_low.price:
            imp_pct = (imp_high.price - imp_low.price) / imp_low.price * 100.0
            rulers.append(
                RulerMeasurement(imp_low.index, imp_high.index, imp_low.price, imp_high.price, imp_pct, f"импульс +{imp_pct:.1f}%")
            )
    return rulers[:4]


def classify_structure(swings: list[SwingPoint]) -> str:
    if len(swings) < 4:
        return "недостаточно swing"
    recent = swings[-4:]
    highs = [s for s in recent if s.kind == "high"]
    lows = [s for s in recent if s.kind == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            return "HH + HL (бычья)"
        if lh and ll:
            return "LH + LL (медв.)"
        if hh and ll:
            return "расширение / нестабильно"
        if lh and hl:
            return "сужение (клин?)"
    return "боковая структура"


def btc_correlation_label(btc_bars: list[KlineBar] | None, alt_bars: list[KlineBar]) -> str:
    if not btc_bars or len(btc_bars) < 13 or len(alt_bars) < 13:
        return ""
    btc_chg = (btc_bars[-1].close - btc_bars[-13].close) / btc_bars[-13].close * 100.0
    alt_chg = (alt_bars[-1].close - alt_bars[-13].close) / alt_bars[-13].close * 100.0
    if abs(btc_chg) < 0.4:
        return f"BTC flat {btc_chg:+.1f}% · альт {alt_chg:+.1f}%"
    same = (btc_chg > 0 and alt_chg > 0) or (btc_chg < 0 and alt_chg < 0)
    if same:
        return f"BTC {btc_chg:+.1f}% · альт в фазе ({alt_chg:+.1f}%)"
    return f"⚠ альт против BTC (BTC {btc_chg:+.1f}%, альт {alt_chg:+.1f}%)"


def btc_alt_spread_pct(btc_bars: list[KlineBar] | None, alt_bars: list[KlineBar]) -> float | None:
    if not btc_bars or len(btc_bars) < 13 or len(alt_bars) < 13:
        return None
    btc_chg = (btc_bars[-1].close - btc_bars[-13].close) / btc_bars[-13].close * 100.0
    alt_chg = (alt_bars[-1].close - alt_bars[-13].close) / alt_bars[-13].close * 100.0
    return alt_chg - btc_chg


def detect_recent_momentum(bars: list[KlineBar], *, lookback: int = 8) -> tuple[str, float]:
    """Краткосрочный импульс по последним свечам: up / down / flat."""
    if len(bars) < 3:
        return "flat", 0.0
    n = min(lookback, len(bars))
    seg = bars[-n:]
    start = seg[0].open
    if start <= 0:
        return "flat", 0.0
    chg = (seg[-1].close - start) / start * 100.0
    red = sum(1 for b in seg if b.close < b.open)
    if chg <= -1.0 or red >= max(3, int(n * 0.62)):
        return "down", chg
    if chg >= 1.0 or red <= max(1, int(n * 0.38)):
        return "up", chg
    return "flat", chg


def _momentum_label_ru(momentum: str, pct: float) -> str:
    if momentum == "down":
        return f"импульс вниз {pct:+.1f}%"
    if momentum == "up":
        return f"импульс вверх {pct:+.1f}%"
    return "боковое движение"


def _resolve_verdict(
    *,
    is_long: bool,
    ms: MarketStructureContext,
    structure: str,
    patterns: list[CandlePattern],
    btc_ctx: str,
    momentum: str = "flat",
    btc_spread: float | None = None,
    smc: SmcContext | None = None,
) -> tuple[str, int, str]:
    score = 5
    reasons: list[str] = []
    if is_long:
        if ms.phase in {"consolidation", "breakout_setup"}:
            score += 2
            reasons.append(ms.phase_label)
        elif ms.phase == "correction_down":
            if momentum == "down" or ms.drawdown_from_high_pct > 10:
                score -= 2
                reasons.append("коррекция вниз после роста")
            else:
                score += 1
                reasons.append("откат для long")
        elif ms.phase in {"impulse_up"}:
            score -= 1
            reasons.append("уже в импульсе")
        elif ms.phase in {"impulse_down", "post_crash_weak"}:
            score -= 2
            reasons.append(ms.phase_label)
        if "бычья" in structure:
            score += 1
        elif "медв." in structure:
            score -= 2
    else:
        if ms.phase in {"correction_down", "impulse_down", "post_crash_weak"}:
            score += 2
            reasons.append(ms.phase_label)
        elif ms.phase in {"correction_up", "consolidation", "breakout_setup"}:
            score += 1
        elif ms.phase in {"impulse_up"}:
            score -= 2
            reasons.append("уже в импульсе вверх")
        if "медв." in structure:
            score += 1
        elif "бычья" in structure:
            score -= 2
    if momentum == "down" and not is_long:
        score += 2
        if "импульс" not in " · ".join(reasons):
            reasons.append("давление продавцов")
    elif momentum == "down" and is_long:
        score -= 2
    elif momentum == "up" and is_long:
        score += 1
    elif momentum == "up" and not is_long:
        score -= 1
    if btc_spread is not None:
        if btc_spread <= -8 and not is_long:
            score += 2
            reasons.append("альт слабее BTC")
        elif btc_spread <= -8 and is_long:
            score -= 2
        elif btc_spread >= 8 and is_long:
            score += 1
        elif btc_spread >= 8 and not is_long:
            score -= 1
    if ms.oi_narrative in {"aligned_long", "accumulation"} and is_long:
        score += 1
    elif ms.oi_narrative in {"aligned_short", "shorts_building"} and not is_long:
        score += 1
    elif ms.oi_narrative in {"aligned_short", "shorts_building"} and is_long:
        score -= 1
    elif ms.oi_narrative in {"aligned_long"} and not is_long:
        score -= 1
    last_pat = patterns[-1] if patterns else None
    if last_pat:
        if last_pat.bullish is True and is_long:
            score += 1
            reasons.append(last_pat.label_ru)
        elif last_pat.bullish is False and not is_long:
            score += 1
            reasons.append(last_pat.label_ru)
        elif last_pat.bullish is True and not is_long:
            score -= 1
        elif last_pat.bullish is False and is_long:
            score -= 1
    if "против BTC" in btc_ctx:
        if not is_long:
            score += 1
        else:
            score -= 1
        reasons.append("дивергенция с BTC")
    if ms.drawdown_from_high_pct > 15 and not is_long:
        score += 1
        reasons.append(f"−{ms.drawdown_from_high_pct:.0f}% от хая")
    elif ms.drawdown_from_high_pct > 15 and is_long:
        score -= 1
    if ms.range_position > 0.78:
        score -= 1 if is_long else 1
        reasons.append("у верха range")
    elif ms.range_position < 0.22:
        score -= 1 if is_long else 1
        reasons.append("у дна range")
    if smc is not None:
        boost = smc_verdict_boost(smc, is_long=is_long)
        score += boost
        if boost >= 2 and smc.reversal_ready:
            reasons.append("паттерн разворота")
        elif boost >= 1 and smc.structure_break:
            reasons.append("слом структуры")
        elif boost <= -2:
            reasons.append("против HTF/SMC")
    score = int(_clamp(score, 1, 9))
    if score >= 7:
        verdict = "LONG" if is_long else "SHORT"
    elif score <= 4:
        verdict = "WAIT"
    else:
        verdict = "WAIT"
    reason = " · ".join(dict.fromkeys(reasons[:4])) if reasons else ms.phase_detail
    return verdict, score, reason


def _apply_signal_context_verdict(
    *,
    verdict: str,
    conf: int,
    reason: str,
    is_long: bool,
    long_s: int,
    short_s: int,
    action_priority: str,
    momentum: str,
    structure: str,
    range_trade: RangeTradeSetup | None,
) -> tuple[str, int, str]:
    """Контекст сигнала: сканер уже дал сторону — учитываем range-сетап и мягче порог."""
    want = "long" if is_long else "short"
    side_s = long_s if is_long else short_s
    opp_s = short_s if is_long else long_s

    if range_trade is not None and range_trade.direction == want:
        boosted = min(9, side_s + range_trade.score_boost)
        if boosted >= 6:
            v = "LONG" if is_long else "SHORT"
            return v, boosted, f"{range_trade.label} · {range_trade.reason}"

    if verdict in {"LONG", "SHORT"}:
        return verdict, conf, reason

    if side_s < 6 or side_s < opp_s:
        return verdict, conf, reason

    if is_long:
        if action_priority == "short" or momentum not in {"up", "flat"}:
            return verdict, conf, reason
        if side_s >= 7 or (side_s >= 6 and momentum == "up" and "бычья" in structure):
            return "LONG", side_s, reason or "сигнал LONG · структура поддерживает"
    else:
        if action_priority == "long" or momentum not in {"down", "flat"}:
            return verdict, conf, reason
        if side_s >= 7 or (side_s >= 6 and momentum == "down" and "медв." in structure):
            return "SHORT", side_s, reason or "сигнал SHORT · структура поддерживает"

    return verdict, conf, reason


def _market_bias(
    structure: str,
    ms: MarketStructureContext,
    *,
    momentum: str,
    btc_spread: float | None,
) -> str:
    if momentum == "down" and (ms.drawdown_from_high_pct > 8 or (btc_spread is not None and btc_spread < -5)):
        return "медвежий"
    if momentum == "up" and ms.drawdown_from_high_pct < 6:
        return "бычий"
    if "медв." in structure:
        return "медвежий"
    if "бычья" in structure:
        return "бычий"
    if ms.phase in {"impulse_down", "correction_up", "post_crash_weak"}:
        return "медвежий"
    if ms.phase == "correction_down":
        if momentum == "down" or ms.drawdown_from_high_pct > 10:
            return "медвежий"
        return "нейтральный"
    if ms.phase == "impulse_up":
        return "бычий"
    return "нейтральный"


def _action_priority(
    *,
    current: float,
    breakout: float | None,
    breakdown: float | None,
    momentum: str,
    market_bias: str,
    long_score: int,
    short_score: int,
) -> str:
    if long_score >= short_score + 2 and market_bias == "бычий":
        return "long"
    if short_score >= long_score + 2 and market_bias == "медвежий":
        return "short"
    if breakout and breakdown and current > 0:
        dist_up = (breakout - current) / current
        dist_down = (current - breakdown) / current
        if momentum == "down" and dist_down < dist_up:
            return "short"
        if momentum == "up" and dist_up < dist_down:
            return "long"
    if market_bias == "медвежий":
        return "short"
    if market_bias == "бычий":
        return "long"
    return "neutral"


def _compute_setup_clarity(
    *,
    ms: MarketStructureContext,
    structure: str,
    levels: list[HorizontalLevel],
    zones: list[PriceZone],
    consolidation: ConsolidationZone | None,
    channel: PriceChannel | None,
    bullish: TradeScenario | None,
    bearish: TradeScenario | None,
    key_levels: list[KeyLevel],
) -> tuple[int, list[str]]:
    score = 4
    notes: list[str] = []
    if bullish and bearish:
        score += 2
        notes.append("два сценария с уровнями")
    elif bullish or bearish:
        score += 1
    if consolidation:
        score += 1
        notes.append("чёткий боковик")
    if channel:
        score += 1
        notes.append("канал виден")
    if len(levels) >= 3:
        score += 1
        notes.append("несколько уровней")
    if len(zones) >= 1:
        score += 1
    if len(key_levels) >= 4:
        score += 1
    if "бычья" in structure or "медв." in structure:
        score += 1
        notes.append("структура читается")
    if 0.25 <= ms.range_position <= 0.75:
        score += 1
        notes.append("цена в середине range")
    elif ms.range_position > 0.75:
        notes.append("у верха range")
    elif ms.range_position < 0.25:
        notes.append("у дна range")
    return int(_clamp(score, 3, 10)), notes[:4]


def _build_risk_notes(
    *,
    ms: MarketStructureContext,
    structure: str,
    btc_ctx: str,
    verdict_reason: str,
    channel: PriceChannel | None,
) -> list[str]:
    risks: list[str] = []
    if ms.phase in {"impulse_up", "impulse_down"}:
        risks.append("уже в импульсе — поздний вход")
    if ms.range_position > 0.78:
        risks.append("цена у верха диапазона")
    elif ms.range_position < 0.22:
        risks.append("цена у дна диапазона")
    if "против BTC" in btc_ctx:
        risks.append("расхождение с BTC")
    if channel and channel.kind == "bear":
        risks.append("нисходящий канал — long только через пробой")
    if channel and channel.kind == "bull":
        risks.append("восходящий канал — short рискован без слома")
    if "нестабильно" in structure:
        risks.append("структура нестабильна")
    if verdict_reason and verdict_reason not in risks:
        for part in verdict_reason.split(" · "):
            if part and part not in risks:
                risks.append(part)
    return risks[:5]


def _build_professional_summary(
    *,
    verdict: str,
    market_bias: str,
    ms: MarketStructureContext,
    structure: str,
    bullish: TradeScenario | None,
    bearish: TradeScenario | None,
    setup_clarity: int,
    momentum_label: str = "",
    action_priority: str = "neutral",
) -> str:
    bias_ru = {"бычий": "бычий", "медвежий": "медвежий", "нейтральный": "нейтральный"}.get(market_bias, market_bias)
    parts: list[str] = []
    parts.append(f"Краткосрочный bias: {bias_ru}.")
    if momentum_label:
        parts.append(f"Импульс: {momentum_label}.")
    if ms.phase_label and ms.phase_label != "Без явной фазы":
        parts.append(f"Фаза: {ms.phase_label.lower()}.")
    if ms.drawdown_from_high_pct > 8:
        parts.append(f"Откат от хая −{ms.drawdown_from_high_pct:.1f}%.")
    if verdict == "WAIT":
        if action_priority == "short" and bearish:
            parts.append(
                f"Давление вниз — приоритет SHORT при пробое {fmt_price(bearish.trigger_price)} "
                f"(ясность {setup_clarity}/10)."
            )
        elif action_priority == "long" and bullish:
            parts.append(
                f"Давление вверх — приоритет LONG при пробое {fmt_price(bullish.trigger_price)} "
                f"(ясность {setup_clarity}/10)."
            )
        elif bullish and bearish:
            parts.append(
                f"Ясность {setup_clarity}/10 — ждать пробой "
                f"{fmt_price(bullish.trigger_price)} (long) или "
                f"{fmt_price(bearish.trigger_price)} (short)."
            )
        else:
            parts.append(f"Ясность сетапа {setup_clarity}/10 — дождаться подтверждения.")
    elif verdict == "LONG" and bullish:
        parts.append(f"Приоритет long при закреплении выше {fmt_price(bullish.trigger_price)}.")
    elif verdict == "SHORT" and bearish:
        parts.append(f"Приоритет short при пробое ниже {fmt_price(bearish.trigger_price)}.")
    return " ".join(parts)


def _resolve_neutral_verdict(
    *,
    ms: MarketStructureContext,
    structure: str,
    patterns: list[CandlePattern],
    btc_ctx: str,
    setup_clarity: int,
    clarity_notes: list[str],
    momentum: str,
    btc_spread: float | None,
    current: float,
    breakout: float | None,
    breakdown: float | None,
    range_trade: RangeTradeSetup | None = None,
    factor_long_boost: int = 0,
    factor_short_boost: int = 0,
    smc: SmcContext | None = None,
) -> tuple[str, int, str]:
    long_v, long_s, long_r = _resolve_verdict(
        is_long=True,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
        smc=smc,
    )
    short_v, short_s, short_r = _resolve_verdict(
        is_long=False,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
        smc=smc,
    )

    long_s = min(9, long_s + factor_long_boost)
    short_s = min(9, short_s + factor_short_boost)

    if range_trade is not None:
        if range_trade.direction == "long":
            score = min(9, long_s + range_trade.score_boost)
            if score >= 6:
                return "LONG", score, f"{range_trade.label} · {range_trade.reason}"
        else:
            score = min(9, short_s + range_trade.score_boost)
            if score >= 6:
                return "SHORT", score, f"{range_trade.label} · {range_trade.reason}"

    if long_v == "LONG" and long_s >= 7 and long_s >= short_s + 1:
        return long_v, long_s, long_r
    if short_v == "SHORT" and short_s >= 7 and short_s >= long_s + 1:
        return short_v, short_s, short_r

    # Сильное давление вниз: short даже без классических 7/10
    if (
        short_s >= 6
        and short_s >= long_s
        and momentum == "down"
        and (ms.drawdown_from_high_pct > 8 or (btc_spread is not None and btc_spread < -5))
    ):
        reason = short_r or "давление вниз · коррекция после роста"
        return "SHORT", short_s, reason

    if (
        long_s >= 6
        and long_s >= short_s + 1
        and momentum == "up"
        and ms.drawdown_from_high_pct < 6
    ):
        reason = long_r or "импульс вверх"
        return "LONG", long_s, reason

    reasons = clarity_notes[:2]
    if momentum == "down" and breakdown and current > 0:
        dist_down = (current - breakdown) / current * 100
        reasons.append(f"ближе к short-триггеру (~{dist_down:.1f}%)")
    elif momentum == "up" and breakout and current > 0:
        dist_up = (breakout - current) / current * 100
        reasons.append(f"ближе к long-триггеру (~{dist_up:.1f}%)")
    if short_r and momentum == "down":
        reasons.append(short_r)
    elif long_r:
        reasons.append(long_r)
    elif short_r and short_r not in reasons:
        reasons.append(short_r)
    return "WAIT", setup_clarity, " · ".join(dict.fromkeys(reasons[:4]))


def _estimate_rr(
    trigger: float | None,
    stop: float | None,
    targets: list[float],
    *,
    is_long: bool,
) -> str | None:
    if not trigger or not stop or not targets:
        return None
    if is_long:
        risk = trigger - stop
        reward = targets[0] - trigger
    else:
        risk = stop - trigger
        reward = trigger - targets[0]
    if risk <= 0 or reward <= 0:
        return None
    ratio = reward / risk
    if ratio < 0.5:
        return None
    return f"~1:{ratio:.1f}"


def _trade_levels(
    bars: list[KlineBar],
    levels: list[HorizontalLevel],
    *,
    is_long: bool,
    invalidation: float | None = None,
    bullish: TradeScenario | None = None,
    bearish: TradeScenario | None = None,
) -> tuple[float | None, tuple[float, float] | None, list[float]]:
    if not bars:
        return None, None, []
    current = bars[-1].close
    supports = sorted([lv.price for lv in levels if lv.kind == "support"], reverse=True)
    resistances = sorted([lv.price for lv in levels if lv.kind == "resistance"])
    inv = invalidation
    targets: list[float] = []
    entry: tuple[float, float] | None = None

    if is_long:
        scenario = bullish
        inv = inv or (scenario.stop_price if scenario else None) or (min(supports) * 0.995 if supports else current * 0.992)
        if scenario and scenario.trigger_price > current * 0.999:
            entry = (scenario.trigger_price * 0.999, scenario.trigger_price * 1.003)
        else:
            entry = (current * 0.999, current * 1.002)
        targets = list(scenario.target_prices[:4]) if scenario else resistances[:4]
        if not targets:
            targets = [current * 1.015, current * 1.03, current * 1.045, current * 1.06]
    else:
        scenario = bearish
        inv = inv or (scenario.stop_price if scenario else None) or (max(resistances) * 1.005 if resistances else current * 1.008)
        if scenario and scenario.trigger_price < current * 1.001:
            entry = (scenario.trigger_price * 0.997, scenario.trigger_price * 1.001)
        else:
            entry = (current * 0.998, current * 1.001)
        targets = list(scenario.target_prices[:4]) if scenario else supports[:4]
        if not targets:
            targets = [current * 0.985, current * 0.97, current * 0.955, current * 0.94]

    return inv, entry, targets


def run_ta_analysis(
    bars: list[KlineBar],
    *,
    is_long: bool = True,
    oi_bars: list[FiveMinOiBar] | None = None,
    btc_bars: list[KlineBar] | None = None,
    htf_bars: list[KlineBar] | None = None,
    symbol: str = "",
    hours: int = 5,
    invalidation_price: float | None = None,
    neutral: bool = False,
    liq_context: dict | None = None,
    interval_minutes: int = 5,
) -> TAAnalysisResult:
    oi_bars = oi_bars or []
    swings = find_swing_points(bars)
    levels = detect_horizontal_levels(bars, swings)
    zones = detect_price_zones(bars, swings)
    channel = detect_channel(bars, swings)
    trend_lines = detect_trend_lines(bars, swings)
    consolidation = detect_consolidation(bars)
    patterns = detect_candle_patterns(bars)
    rulers = compute_rulers(bars, swings)
    structure = classify_structure(swings)
    key_levels = build_key_levels(bars, levels, zones)
    bullish, bearish, breakout, breakdown = build_scenarios(
        bars, levels, zones, key_levels, swings,
    )
    _, _, cons_override, post_pump = resolve_trade_triggers(
        bars, swings, levels, zones, key_levels,
    )
    if cons_override:
        consolidation = cons_override
    elif consolidation is None:
        local = detect_local_consolidation(bars, lookback=40, max_range_pct=6.5)
        if local:
            consolidation = local
    nearest_resistance, nearest_support = detect_local_swing_levels(
        bars, swings, max_dist_pct=12.0,
    )
    signal_markers = detect_signal_markers(bars, levels)
    smc = analyze_smc(
        bars, htf_bars=htf_bars, swings=swings, interval_minutes=interval_minutes,
    )
    ms = analyze_market_structure(bars, oi_bars, is_long=is_long, hours=hours)
    btc_ctx = ""
    btc_spread: float | None = None
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_ctx = btc_correlation_label(btc_bars, bars)
        btc_spread = btc_alt_spread_pct(btc_bars, bars)

    momentum, momentum_pct = detect_recent_momentum(bars)
    momentum_label = _momentum_label_ru(momentum, momentum_pct)
    current = bars[-1].close if bars else 0.0

    setup_clarity, clarity_notes = _compute_setup_clarity(
        ms=ms,
        structure=structure,
        levels=levels,
        zones=zones,
        consolidation=consolidation,
        channel=channel,
        bullish=bullish,
        bearish=bearish,
        key_levels=key_levels,
    )
    market_bias = _market_bias(
        structure, ms, momentum=momentum, btc_spread=btc_spread,
    )

    long_v, long_s, _ = _resolve_verdict(
        is_long=True,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
        smc=smc,
    )
    short_v, short_s, _ = _resolve_verdict(
        is_long=False,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
        smc=smc,
    )
    action_priority = _action_priority(
        current=current,
        breakout=breakout,
        breakdown=breakdown,
        momentum=momentum,
        market_bias=market_bias,
        long_score=long_s,
        short_score=short_s,
    )

    factors = build_factor_context(bars, liq_context)
    range_trade = evaluate_range_trade(
        bars,
        consolidation=consolidation,
        breakout=breakout,
        breakdown=breakdown,
        ms=ms,
        patterns=patterns,
        momentum=momentum,
        channel=channel,
        factors=factors,
    )

    if neutral:
        verdict, conf, reason = _resolve_neutral_verdict(
            ms=ms,
            structure=structure,
            patterns=patterns,
            btc_ctx=btc_ctx,
            setup_clarity=setup_clarity,
            clarity_notes=clarity_notes,
            momentum=momentum,
            btc_spread=btc_spread,
            current=current,
            breakout=breakout,
            breakdown=breakdown,
            range_trade=range_trade,
            factor_long_boost=factors.liq_long_boost + (1 if factors.cvd_ratio >= 0.62 else 0),
            factor_short_boost=factors.liq_short_boost + (1 if factors.cvd_ratio <= 0.38 else 0),
            smc=smc,
        )
    else:
        verdict, conf, reason = _resolve_verdict(
            is_long=is_long,
            ms=ms,
            structure=structure,
            patterns=patterns,
            btc_ctx=btc_ctx,
            momentum=momentum,
            btc_spread=btc_spread,
            smc=smc,
        )
        verdict, conf, reason = _apply_signal_context_verdict(
            verdict=verdict,
            conf=conf,
            reason=reason,
            is_long=is_long,
            long_s=long_s,
            short_s=short_s,
            action_priority=action_priority,
            momentum=momentum,
            structure=structure,
            range_trade=range_trade,
        )
    downside_pressure = (
        momentum == "down"
        and (
            factors.liq_short_boost > 0
            or factors.cvd_ratio <= 0.40
            or ms.oi_narrative in {"aligned_short", "shorts_building"}
        )
    )
    if breakdown and current > 0 and downside_pressure:
        if current <= breakdown * 1.003:
            verdict = "SHORT"
            conf = max(conf, 7)
            reason = (
                "подтверждён пробой вниз · OI/CVD/ликвидации поддерживают слив"
                if not reason
                else f"{reason} · подтверждён пробой вниз"
            )
            action_priority = "short"
        elif verdict == "WAIT" and current <= breakdown * 1.012:
            reason = (
                "риск слива: цена у short-триггера + OI/CVD/ликвидации"
                if not reason
                else f"{reason} · риск слива по OI/CVD/ликвидациям"
            )
            action_priority = "short"
    if channel and channel.kind == "bear" and verdict == "LONG" and conf < 7 and range_trade is None:
        verdict = "WAIT"
        reason = (reason + " · канал ↓") if reason else "канал ↓ — ждать пробой"

    trade_is_long = is_long
    if neutral:
        if verdict == "SHORT" or action_priority == "short":
            trade_is_long = False
        elif verdict == "LONG" or action_priority == "long":
            trade_is_long = True

    entry_mode = "range_edge" if range_trade else "breakout"
    if range_trade and verdict in {"LONG", "SHORT"}:
        inv = range_trade.stop_price
        entry = (range_trade.entry_price * 0.999, range_trade.entry_price * 1.001)
        targets = list(range_trade.targets)
    else:
        inv, entry, targets = _trade_levels(
            bars, levels, is_long=trade_is_long, invalidation=invalidation_price,
            bullish=bullish, bearish=bearish,
        )
    supports = sorted([lv.price for lv in levels if lv.kind == "support"], reverse=True)
    trader_plan = build_trader_plan(
        verdict=verdict,
        bullish=bullish,
        bearish=bearish,
        breakout=breakout,
        breakdown=breakdown,
        entry_zone=entry,
        consolidation=consolidation,
        supports=supports,
        action_priority=action_priority,
    )
    risk_notes = _build_risk_notes(
        ms=ms,
        structure=structure,
        btc_ctx=btc_ctx,
        verdict_reason=reason,
        channel=channel,
    )
    if factors.factor_lines:
        risk_notes.extend([f for f in factors.factor_lines if f and f not in risk_notes])
    risk_notes = risk_notes[:6]
    professional_summary = _build_professional_summary(
        verdict=verdict,
        market_bias=market_bias,
        ms=ms,
        structure=structure,
        bullish=bullish,
        bearish=bearish,
        setup_clarity=setup_clarity if verdict == "WAIT" else conf,
        momentum_label=momentum_label,
        action_priority=action_priority,
    )
    dist_to_long_pct: float | None = None
    dist_to_short_pct: float | None = None
    if current > 0 and breakout and breakout > current * 1.0001:
        dist_to_long_pct = (breakout - current) / current * 100.0
    if current > 0 and breakdown and breakdown < current * 0.9999:
        dist_to_short_pct = (current - breakdown) / current * 100.0

    primary_scenario = ""
    if post_pump:
        primary_scenario = "консолидация после пампа — пробой границ локального range"
    elif verdict == "LONG" and targets:
        primary_scenario = f"рост к {fmt_price(targets[0])}"
    elif verdict == "SHORT" and targets:
        primary_scenario = f"снижение к {fmt_price(targets[0])}"
    elif action_priority == "long" and breakout:
        primary_scenario = f"приоритет вверх — пробой {fmt_price(breakout)}"
    elif action_priority == "short" and breakdown:
        primary_scenario = f"приоритет вниз — пробой {fmt_price(breakdown)}"

    if post_pump:
        risk_notes = ["после пампа — не гнаться, ждать пробой range"] + risk_notes

    return TAAnalysisResult(
        swings=swings,
        levels=levels,
        trend_lines=trend_lines,
        zones=zones,
        channel=channel,
        signal_markers=signal_markers,
        consolidation=consolidation,
        patterns=patterns,
        rulers=rulers,
        key_levels=key_levels,
        bullish_scenario=bullish,
        bearish_scenario=bearish,
        trader_plan=trader_plan,
        structure_label=structure,
        phase=ms.phase,
        phase_label=ms.phase_label,
        verdict=verdict,
        verdict_confidence=conf,
        verdict_reason=reason,
        btc_context=btc_ctx,
        breakout_level=breakout,
        breakdown_level=breakdown,
        invalidation_price=inv,
        entry_zone=entry,
        target_prices=targets,
        current_price=bars[-1].close if bars else 0.0,
        oi_narrative_label=ms.oi_narrative_label,
        range_position=ms.range_position,
        phase_detail=ms.phase_detail,
        drawdown_from_high_pct=ms.drawdown_from_high_pct,
        market_bias=market_bias,
        setup_clarity=setup_clarity,
        risk_notes=risk_notes,
        professional_summary=professional_summary,
        momentum_label=momentum_label,
        momentum_pct=momentum_pct,
        btc_alt_spread=btc_spread,
        action_priority=action_priority,
        range_trade_label=range_trade.label if range_trade else "",
        entry_mode=entry_mode,
        factor_lines=factors.factor_lines,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        dist_to_long_pct=dist_to_long_pct,
        dist_to_short_pct=dist_to_short_pct,
        post_pump=post_pump,
        primary_scenario=primary_scenario,
        smc=smc,
        smc_score=smc.smc_score,
        smc_summary=smc.summary,
    )


def ta_summary_compact(ta: TAAnalysisResult) -> str:
    parts = [f"{ta.verdict} {ta.verdict_confidence}/10"]
    if ta.phase_label:
        parts.append(ta.phase_label[:40])
    if ta.structure_label:
        parts.append(ta.structure_label)
    if ta.breakout_level:
        parts.append(f"ключ {fmt_price(ta.breakout_level)}")
    if ta.patterns:
        parts.append(ta.patterns[-1].label_ru)
    return " · ".join(parts)


def _structure_plain(label: str) -> str:
    mapping = {
        "HH + HL (бычья)": "восходящий тренд (хай выше, лой выше)",
        "LH + LL (медв.)": "нисходящий тренд (хай ниже, лой ниже)",
        "боковая структура": "боковик — цена ходит в диапазоне",
        "сужение (клин?)": "сжатие — готовится пробой",
        "расширение / нестабильно": "рынок нестабилен",
        "недостаточно swing": "мало данных для структуры",
    }
    return mapping.get(label, label)


def _verdict_plain(ta: TAAnalysisResult) -> str:
    if ta.verdict == "LONG":
        return "склонность к покупкам (LONG)"
    if ta.verdict == "SHORT":
        return "склонность к продажам (SHORT)"
    return "лучше подождать (WAIT)"


def _situation_plain(ta: TAAnalysisResult) -> str:
    parts: list[str] = []
    price = fmt_price(ta.current_price) if ta.current_price else "—"
    parts.append(f"цена {price}")
    if ta.structure_label:
        parts.append(_structure_plain(ta.structure_label))
    if ta.channel:
        ch = "нисходящий канал" if ta.channel.kind == "bear" else "восходящий канал"
        parts.append(ch)
    if ta.momentum_label:
        parts.append(ta.momentum_label)
    if ta.phase_label and ta.phase_label != "Без явной фазы":
        parts.append(ta.phase_label.lower())
    if ta.rulers and ta.momentum_label not in (ta.rulers[0].label if ta.rulers else ""):
        parts.append(ta.rulers[0].label)
    return " · ".join(parts[:5])


def ta_display_score(ta: TAAnalysisResult) -> int:
    if ta.verdict == "WAIT" and ta.setup_clarity > ta.verdict_confidence:
        return ta.setup_clarity
    return ta.verdict_confidence


def primary_forecast_direction(ta: TAAnalysisResult) -> str:
    """Какой сценарий рисовать на графике: long / short / neutral."""
    if ta.verdict == "LONG":
        return "long"
    if ta.verdict == "SHORT":
        return "short"
    if ta.action_priority == "long":
        return "long"
    if ta.action_priority == "short":
        return "short"
    return "neutral"


def _manual_now_action_html(ta: TAAnalysisResult) -> str:
    """Оперативная подсказка для ручного TA: что делать прямо сейчас."""
    current = ta.current_price
    breakdown = ta.breakdown_level
    breakout = ta.breakout_level
    downside_factors = any("давление вниз" in line or "CVD↓" in line for line in ta.factor_lines)

    if ta.verdict == "SHORT" and breakdown and current > 0 and current <= breakdown * 1.003:
        return (
            "🚨 <b>Прямо сейчас:</b> short-план активен. "
            "Не ловить лонг, входить только после подтверждения и лучше от ретеста уровня."
        )
    if ta.verdict == "SHORT" and breakdown and current > 0:
        dist = max(0.0, (current - breakdown) / current * 100.0)
        return (
            f"🚨 <b>Прямо сейчас:</b> short в приоритете, но не с рынка. "
            f"Ждать 5m закрытие ниже <b>{fmt_price(breakdown)}</b> (до уровня ~<b>{dist:.1f}%</b>)."
        )
    if ta.verdict == "WAIT" and ta.action_priority == "short" and breakdown and current > 0 and current <= breakdown * 1.012:
        return (
            f"🚨 <b>Прямо сейчас:</b> риск слива высокий. "
            f"Лонг пропустить, ждать закрытие 5m ниже <b>{fmt_price(breakdown)}</b> "
            "и потом рассматривать short."
        )
    if ta.verdict == "WAIT" and ta.action_priority == "short" and downside_factors:
        return (
            "🚨 <b>Прямо сейчас:</b> давление вниз по OI/CVD/ликвидациям — "
            "не входить против движения, ждать подтверждение уровня."
        )
    if ta.verdict == "LONG" and breakout and current > 0 and current >= breakout * 0.998:
        return (
            f"🚨 <b>Прямо сейчас:</b> long-сетап активируется у <b>{fmt_price(breakout)}</b>. "
            "Не входить в середине свечи — лучше ретест/закрепление."
        )
    return "🚨 <b>Прямо сейчас:</b> вне сделки. Ждать подтверждение уровня и не входить в середине движения."


def _simple_manual_plan_line(ta: TAAnalysisResult) -> str:
    """Одна простая строка: что делать без терминов."""
    long_lvl = fmt_price(ta.breakout_level) if ta.breakout_level else None
    short_lvl = fmt_price(ta.breakdown_level) if ta.breakdown_level else None

    if ta.verdict == "SHORT":
        if short_lvl:
            return (
                f"🧩 <b>План просто:</b> сейчас не прыгать в сделку; "
                f"ждать закрытие 5m ниже <b>{short_lvl}</b>, затем вход в short от ретеста."
            )
        return "🧩 <b>План просто:</b> short по тренду, но только после нового подтверждения вниз."
    if ta.verdict == "LONG":
        if long_lvl:
            return (
                f"🧩 <b>План просто:</b> ждать закрытие 5m выше <b>{long_lvl}</b>, "
                "входить в long от отката к уровню."
            )
        return "🧩 <b>План просто:</b> long только после явного подтверждения роста."
    if long_lvl and short_lvl:
        return (
            f"🧩 <b>План просто:</b> вне сделки. "
            f"Long только выше <b>{long_lvl}</b>, short только ниже <b>{short_lvl}</b>."
        )
    return "🧩 <b>План просто:</b> вне сделки, ждать понятный сигнал и не входить на эмоциях."


def ta_action_summary_html(ta: TAAnalysisResult) -> str:
    """Короткий итог (3–5 строк)."""
    score = ta_display_score(ta)
    price = fmt_price(ta.current_price) if ta.current_price else "—"

    if ta.verdict == "LONG":
        lines = [f"✅ <b>LONG</b> {score}/10 · {price}"]
        if ta.breakout_level:
            lines.append(f"Вход: выше <b>{fmt_price(ta.breakout_level)}</b>")
    elif ta.verdict == "SHORT":
        lines = [f"✅ <b>SHORT</b> {score}/10 · {price}"]
        if ta.breakdown_level:
            lines.append(f"Вход: ниже <b>{fmt_price(ta.breakdown_level)}</b>")
    else:
        extra = ""
        if ta.action_priority == "long":
            extra = " · ближе LONG"
        elif ta.action_priority == "short":
            extra = " · ближе SHORT"
        lines = [f"✋ <b>WAIT</b> {score}/10 · {price}{extra}"]

    parts: list[str] = []
    if ta.breakout_level and ta.verdict != "LONG":
        parts.append(f"L≥{fmt_price(ta.breakout_level)}")
    if ta.breakdown_level and ta.verdict != "SHORT":
        parts.append(f"S≤{fmt_price(ta.breakdown_level)}")
    if parts:
        lines.append("Ждать: " + " · ".join(parts))

    if ta.verdict in {"LONG", "SHORT"}:
        if ta.invalidation_price:
            lines.append(f"Стоп <b>{fmt_price(ta.invalidation_price)}</b>")
        if ta.target_prices:
            tps = "→".join(fmt_price(t) for t in ta.target_prices[:2])
            lines.append(f"TP {tps}")

    return "\n".join(lines)


def ta_manual_compact_html(ta: TAAnalysisResult) -> str:
    """Ручной TA: только суть, без дублей."""
    return ta_action_summary_html(ta)


def ta_signal_scenario_line_html(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
) -> str:
    """Одна строка сценария для обычных сигналов."""
    sig = (signal_side or "").lower()

    if ta.verdict == "LONG":
        head = "▶️ <b>Открывать LONG</b>"
        if ta.entry_mode == "range_edge" and ta.range_trade_label:
            head = f"▶️ <b>LONG</b> · {ta.range_trade_label}"
        parts = [head]
        if ta.breakout_level:
            parts.append(f"вход ≥<b>{fmt_price(ta.breakout_level)}</b>")
        if ta.invalidation_price:
            parts.append(f"стоп <b>{fmt_price(ta.invalidation_price)}</b>")
        if ta.target_prices:
            parts.append(f"TP <b>{fmt_price(ta.target_prices[0])}</b>")
        return " · ".join(parts)

    if ta.verdict == "SHORT":
        head = "▶️ <b>Открывать SHORT</b>"
        if ta.entry_mode == "range_edge" and ta.range_trade_label:
            head = f"▶️ <b>SHORT</b> · {ta.range_trade_label}"
        parts = [head]
        if ta.breakdown_level:
            parts.append(f"вход ≤<b>{fmt_price(ta.breakdown_level)}</b>")
        if ta.invalidation_price:
            parts.append(f"стоп <b>{fmt_price(ta.invalidation_price)}</b>")
        if ta.target_prices:
            parts.append(f"TP <b>{fmt_price(ta.target_prices[0])}</b>")
        return " · ".join(parts)

    if sig in {"long", "short"}:
        is_short = sig == "short"
        trigger = ta.breakdown_level if is_short else ta.breakout_level
        op = "≤" if is_short else "≥"
        label = "SHORT" if is_short else "LONG"
        if trigger:
            line = (
                f"▶️ Сигнал <b>{label}</b> · <b>не маркет</b> · "
                f"вход {op}<b>{fmt_price(trigger)}</b>"
            )
            if is_short and ta.action_priority == "long" and ta.breakout_level:
                line += (
                    f" · <i>цена ближе LONG ≥{fmt_price(ta.breakout_level)}</i>"
                )
            elif not is_short and ta.action_priority == "short" and ta.breakdown_level:
                line += (
                    f" · <i>цена ближе SHORT ≤{fmt_price(ta.breakdown_level)}</i>"
                )
            return line

    if ta.action_priority == "long" and ta.breakout_level:
        return (
            f"▶️ <b>Не входить</b> · приоритет LONG при ≥"
            f"<b>{fmt_price(ta.breakout_level)}</b>"
        )
    if ta.action_priority == "short" and ta.breakdown_level:
        return (
            f"▶️ <b>Не входить</b> · приоритет SHORT при ≤"
            f"<b>{fmt_price(ta.breakdown_level)}</b>"
        )

    triggers: list[str] = []
    if ta.breakout_level:
        triggers.append(f"LONG ≥<b>{fmt_price(ta.breakout_level)}</b>")
    if ta.breakdown_level:
        triggers.append(f"SHORT ≤<b>{fmt_price(ta.breakdown_level)}</b>")
    if triggers:
        return f"▶️ <b>Не входить</b> · ждать {' / '.join(triggers)}"
    return "▶️ <b>Не входить</b> · дождаться пробоя уровня"


def ta_signal_caption_html(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
) -> str:
    """Сигналы: TA + одна строка сценария (согласовано с направлением сигнала)."""
    score = ta_display_score(ta)
    sig = (signal_side or "").lower()

    if ta.verdict == "LONG":
        line = f"📐 TA · <b>LONG</b> {score}/10"
        if ta.breakout_level:
            line += f" · ≥{fmt_price(ta.breakout_level)}"
    elif ta.verdict == "SHORT":
        line = f"📐 TA · <b>SHORT</b> {score}/10"
        if ta.breakdown_level:
            line += f" · ≤{fmt_price(ta.breakdown_level)}"
    else:
        if sig == "short":
            extra = " · сигнал SHORT, TA ждёт уровень"
        elif sig == "long":
            extra = " · сигнал LONG, TA ждёт уровень"
        elif ta.action_priority == "long":
            extra = " · ближе LONG"
        elif ta.action_priority == "short":
            extra = " · ближе SHORT"
        else:
            extra = ""
        line = f"📐 TA · WAIT {score}/10{extra}"
    smc_line = format_smc_compact_html(ta.smc) if ta.smc and ta.smc.smc_score >= 4 else ""
    base = f"{line}\n{ta_signal_scenario_line_html(ta, signal_side=signal_side)}"
    if smc_line:
        return f"{base}\n{smc_line}"
    return base


def ta_manual_detailed_html(ta: TAAnalysisResult) -> str:
    """Ручной TA: полный разбор для новичка."""
    score = ta_display_score(ta)
    lines = [
        f"📐 <b>TA</b> · <b>{ta.verdict}</b> {score}/10",
        f"📍 <b>Сейчас:</b> {_situation_plain(ta)}",
        f"💡 <b>Смысл:</b> {_verdict_plain(ta)}",
    ]

    dist_bits: list[str] = []
    if ta.dist_to_long_pct is not None:
        dist_bits.append(f"до LONG <b>{ta.dist_to_long_pct:.1f}%</b>")
    if ta.dist_to_short_pct is not None:
        dist_bits.append(f"до SHORT <b>{ta.dist_to_short_pct:.1f}%</b>")
    if dist_bits:
        lines.append("📏 " + " · ".join(dist_bits))
    if ta.range_position:
        lines.append(f"📊 В range: <b>{ta.range_position * 100:.0f}%</b> (0=дно, 100=верх)")
    if ta.primary_scenario:
        lines.append(f"🧭 <b>Главный сценарий:</b> {ta.primary_scenario}")
    if ta.post_pump:
        lines.append("⚡ <b>Фаза:</b> консолидация после пампа")

    if ta.breakout_level:
        lines.append(
            f"🟢 <b>LONG</b> — если цена закрепится <b>выше {fmt_price(ta.breakout_level)}</b> "
            f"(лучше вход на откате к уровню)"
        )
    if ta.breakdown_level:
        lines.append(
            f"🔴 <b>SHORT</b> — если цена уйдёт <b>ниже {fmt_price(ta.breakdown_level)}</b> "
            f"(закрытие свечи под уровнем)"
        )
    if ta.invalidation_price:
        lines.append(f"🛑 <b>Стоп</b> (если пошли в сделку): <b>{fmt_price(ta.invalidation_price)}</b>")
    if ta.target_prices:
        tps = " → ".join(fmt_price(t) for t in ta.target_prices[:3])
        side_word = "SHORT" if ta.action_priority == "short" or ta.verdict == "SHORT" else (
            "LONG" if ta.verdict == "LONG" or ta.action_priority == "long" else ""
        )
        label = f" ({side_word})" if side_word else ""
        lines.append(f"🎯 <b>Цели</b>{label}: {tps}")

    lines.append(_simple_manual_plan_line(ta))

    # Сверхкороткий практический блок в начале действий.
    if ta.verdict == "SHORT":
        trig = (
            f"<b>{fmt_price(ta.breakdown_level)}</b>"
            if ta.breakdown_level
            else (f"<b>{fmt_price(ta.target_prices[0])}</b>" if ta.target_prices else "подтверждения вниз")
        )
        stop = f"<b>{fmt_price(ta.invalidation_price)}</b>" if ta.invalidation_price else "по инвалидации"
        tp = f"<b>{fmt_price(ta.target_prices[0])}</b>" if ta.target_prices else "ближайшей поддержки"
        lines.append(f"🎯 <b>Конкретика сейчас:</b> SHORT только после 5m close ниже {trig} · стоп {stop} · TP1 {tp}")
    elif ta.verdict == "LONG":
        trig = f"<b>{fmt_price(ta.breakout_level)}</b>" if ta.breakout_level else "триггера вверх"
        stop = f"<b>{fmt_price(ta.invalidation_price)}</b>" if ta.invalidation_price else "по инвалидации"
        tp = f"<b>{fmt_price(ta.target_prices[0])}</b>" if ta.target_prices else "ближайшего сопротивления"
        lines.append(f"🎯 <b>Конкретика сейчас:</b> LONG только после 5m close выше {trig} · стоп {stop} · TP1 {tp}")
    else:
        long_lvl = f"<b>{fmt_price(ta.breakout_level)}</b>" if ta.breakout_level else "уровня LONG"
        short_lvl = f"<b>{fmt_price(ta.breakdown_level)}</b>" if ta.breakdown_level else "уровня SHORT"
        lines.append(f"🎯 <b>Конкретика сейчас:</b> вне сделки; ждать 5m close выше {long_lvl} или ниже {short_lvl}")

    lines.append(_manual_now_action_html(ta))

    if ta.verdict == "LONG":
        if ta.breakout_level:
            lines.append(f"👉 <b>Действие:</b> LONG при закреплении выше <b>{fmt_price(ta.breakout_level)}</b>")
        else:
            lines.append("👉 <b>Действие:</b> LONG — ждать подтверждения роста")
    elif ta.verdict == "SHORT":
        if ta.breakdown_level:
            lines.append(f"👉 <b>Действие:</b> SHORT при пробое ниже <b>{fmt_price(ta.breakdown_level)}</b>")
        else:
            lines.append("👉 <b>Действие:</b> SHORT — ждать подтверждения падения")
    elif ta.action_priority == "short" and ta.breakdown_level:
        lines.append(
            f"👉 <b>Действие:</b> WAIT, но <b>приоритет SHORT</b> — "
            f"смотреть пробой <b>{fmt_price(ta.breakdown_level)}</b>"
        )
    elif ta.action_priority == "long" and ta.breakout_level:
        lines.append(
            f"👉 <b>Действие:</b> WAIT, но <b>приоритет LONG</b> — "
            f"смотреть пробой <b>{fmt_price(ta.breakout_level)}</b>"
        )
    else:
        long_lvl = fmt_price(ta.breakout_level) if ta.breakout_level else "сопротивления"
        short_lvl = fmt_price(ta.breakdown_level) if ta.breakdown_level else "поддержки"
        lines.append(
            f"👉 <b>Действие:</b> WAIT — не входить сейчас. "
            f"Смотреть пробой {long_lvl} (long) или {short_lvl} (short)"
        )

    if ta.verdict_reason:
        lines.append(f"<i>Примечание: {ta.verdict_reason[:120]}</i>")
    if ta.smc and ta.smc.checklist:
        lines.append(format_smc_compact_html(ta.smc))
    return "\n".join(lines)


def ta_telegram_caption_html(ta: TAAnalysisResult) -> str:
    """Подпись к графику сигналов (компактно)."""
    lines = [ta_action_summary_html(ta)]
    if ta.verdict_reason:
        note = ta.verdict_reason.split(" · ")[0][:60]
        lines.append(f"<i>{note}</i>")
    return "\n".join(lines)


def _key_level_role_ru(role: str) -> str:
    mapping = {
        "breakout": "уровень пробоя вверх",
        "breakdown": "уровень пробоя вниз",
        "strong_resistance": "сильное сопротивление",
        "strong_support": "сильная поддержка",
        "nearest_support": "ближайшая поддержка",
        "nearest_resistance": "ближайшее сопротивление",
    }
    return mapping.get(role, role)


def _scenario_block_html(title: str, scenario: TradeScenario | None, *, emoji: str) -> list[str]:
    if scenario is None:
        return []
    lines = [f"{emoji} <b>{title}</b>"]
    lines.append(f"Триггер: закрепление {'выше' if scenario.direction == 'long' else 'ниже'} <b>{fmt_price(scenario.trigger_price)}</b>")
    for i, cond in enumerate(scenario.conditions[:3], 1):
        lines.append(f"  {i}. {cond}")
    tps = " → ".join(fmt_price(t) for t in scenario.target_prices[:3])
    lines.append(f"Цели: {tps}")
    lines.append(f"Стоп: <b>{fmt_price(scenario.stop_price)}</b>")
    rr = _estimate_rr(
        scenario.trigger_price,
        scenario.stop_price,
        scenario.target_prices,
        is_long=scenario.direction == "long",
    )
    if rr:
        lines.append(f"R:R {rr}")
    return lines


def ta_telegram_breakdown_html(ta: TAAnalysisResult, *, symbol: str = "", interval: str = "") -> str:
    """Доп. детали — коротко (опционально)."""
    score = ta_display_score(ta)
    lines = [ta_action_summary_html(ta)]

    ctx: list[str] = []
    if ta.market_bias:
        ctx.append(ta.market_bias)
    if ta.momentum_label:
        ctx.append(ta.momentum_label.split()[0] + " " + ta.momentum_label.split()[-1] if ta.momentum_label else "")
    if ta.factor_lines:
        ctx.append(ta.factor_lines[0][:40])
    if ctx:
        lines.append(" · ".join(ctx[:3]))

    if ta.risk_notes:
        lines.append(f"⚠ {ta.risk_notes[0]}")

    lines.append(f"<i>{symbol} {interval} · {score}/10</i>")
    return "\n".join(lines)


def ta_chart_key_levels_text(ta: TAAnalysisResult) -> str:
    if not ta.key_levels:
        return ""
    lines = ["КЛЮЧЕВЫЕ УРОВНИ"]
    for kl in ta.key_levels[:6]:
        role = _key_level_role_ru(kl.role)
        lines.append(f"{fmt_price(kl.price)} — {kl.label}")
        lines.append(f"  ({role})")
    return "\n".join(lines)


def ta_chart_scenario_text(scenario: TradeScenario | None, *, title: str) -> str:
    if scenario is None:
        return ""
    lines = [title, f"триггер {fmt_price(scenario.trigger_price)}"]
    for cond in scenario.conditions[:4]:
        lines.append(f"• {cond}")
    tps = " → ".join(fmt_price(t) for t in scenario.target_prices[:3])
    lines.append(f"цели: {tps}")
    lines.append(f"стоп {fmt_price(scenario.stop_price)}")
    rr = _estimate_rr(
        scenario.trigger_price,
        scenario.stop_price,
        scenario.target_prices,
        is_long=scenario.direction == "long",
    )
    if rr:
        lines.append(f"R:R {rr}")
    return "\n".join(lines)


def ta_chart_context_text(ta: TAAnalysisResult) -> str:
    lines = ["КОНТЕКСТ"]
    if ta.structure_label:
        lines.append(_structure_plain(ta.structure_label))
    if ta.momentum_label:
        lines.append(ta.momentum_label)
    if ta.phase_label and ta.phase_label != "Без явной фазы":
        lines.append(ta.phase_label)
    if ta.market_bias:
        lines.append(f"bias: {ta.market_bias}")
    if ta.oi_narrative_label:
        lines.append(f"OI: {ta.oi_narrative_label}")
    if ta.range_position:
        lines.append(f"в range: {ta.range_position * 100:.0f}%")
    if ta.post_pump:
        lines.append("фаза: после пампа")
    for fl in ta.factor_lines[:3]:
        lines.append(fl[:48])
    return "\n".join(lines[:10])


def ta_chart_plan_text(ta: TAAnalysisResult) -> str:
    if not ta.trader_plan:
        return ""
    lines = ["ПЛАН ДЕЙСТВИЙ"]
    for i, step in enumerate(ta.trader_plan[:8], 1):
        lines.append(f"{i}. {step}")
    return "\n".join(lines)


def ta_chart_summary_text(ta: TAAnalysisResult) -> str:
    score = ta_display_score(ta)
    lines = [f"ИТОГ: {ta.verdict} {score}/10"]
    if ta.professional_summary:
        lines.append(ta.professional_summary[:200])
    if ta.verdict_reason:
        lines.append(ta.verdict_reason[:100])
    return "\n".join(lines)


def ta_chart_panel_text(ta: TAAnalysisResult) -> str:
    score = ta_display_score(ta)
    lines = [
        f"ИТОГ: {ta.verdict} {score}/10",
        _verdict_plain(ta),
    ]
    if ta.current_price:
        lines.append(f"цена: {fmt_price(ta.current_price)}")
    if ta.breakout_level:
        extra = f" ({ta.dist_to_long_pct:.1f}%)" if ta.dist_to_long_pct is not None else ""
        lines.append(f"LONG от: {fmt_price(ta.breakout_level)}{extra}")
    if ta.breakdown_level:
        extra = f" ({ta.dist_to_short_pct:.1f}%)" if ta.dist_to_short_pct is not None else ""
        lines.append(f"SHORT от: {fmt_price(ta.breakdown_level)}{extra}")
    if ta.invalidation_price:
        lines.append(f"стоп: {fmt_price(ta.invalidation_price)}")
    if ta.target_prices:
        tps = " → ".join(fmt_price(t) for t in ta.target_prices[:3])
        lines.append(f"цели: {tps}")
    if ta.primary_scenario:
        lines.append(ta.primary_scenario[:70])
    if ta.range_position:
        lines.append(f"range: {ta.range_position * 100:.0f}%")
    return "\n".join(lines[:10])


def ta_chart_legend_text() -> str:
    return (
        "█ зелёная зона = поддержка | █ красная = сопротивление | "
        "фиолет. = канал | STOP = стоп | TP = цель"
    )
