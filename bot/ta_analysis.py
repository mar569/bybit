"""Технический анализ OHLC: уровни, тренды, паттерны, линейка %, BTC-контекст."""
from __future__ import annotations

from dataclasses import dataclass, field

from .bybit_klines import KlineBar
from .market_structure import FiveMinOiBar, MarketStructureContext, analyze_market_structure


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
        result.append(KeyLevel(supports[0].price, "nearest_support", "поддержка"))
    if resistances:
        result.append(KeyLevel(resistances[-1].price, "nearest_resistance", "сопротивление"))

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
) -> tuple[TradeScenario | None, TradeScenario | None, float | None, float | None]:
    if not bars:
        return None, None, None, None

    current = bars[-1].close
    resistances = sorted({lv.price for lv in levels if lv.kind == "resistance" and lv.price > current})
    supports = sorted({lv.price for lv in levels if lv.kind == "support" and lv.price < current}, reverse=True)

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

    bull_targets = resistances[:4]
    if not bull_targets:
        bull_targets = [current * 1.015, current * 1.03, current * 1.045, current * 1.06]

    bear_targets = supports[:4]
    if not bear_targets:
        bear_targets = [current * 0.985, current * 0.97, current * 0.955, current * 0.94]

    stop_long = (breakdown or current * 0.98) * 0.995
    stop_short = (breakout or current * 1.02) * 1.005

    bullish: TradeScenario | None = None
    bearish: TradeScenario | None = None

    if bullish:
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
    score = int(_clamp(score, 1, 9))
    if score >= 7:
        verdict = "LONG" if is_long else "SHORT"
    elif score <= 4:
        verdict = "WAIT"
    else:
        verdict = "WAIT"
    reason = " · ".join(dict.fromkeys(reasons[:4])) if reasons else ms.phase_detail
    return verdict, score, reason


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
) -> tuple[str, int, str]:
    long_v, long_s, long_r = _resolve_verdict(
        is_long=True,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
    )
    short_v, short_s, short_r = _resolve_verdict(
        is_long=False,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
    )

    if long_v == "LONG" and long_s >= 7 and long_s >= short_s + 1:
        return long_v, long_s, long_r
    if short_v == "SHORT" and short_s >= 7 and short_s >= long_s + 1:
        return short_v, short_s, short_r

    # Сильное давление вниз: short даже без классических 7/10
    if (
        short_s >= 6
        and short_s >= long_s + 1
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
    symbol: str = "",
    hours: int = 5,
    invalidation_price: float | None = None,
    neutral: bool = False,
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
    bullish, bearish, breakout, breakdown = build_scenarios(bars, levels, zones, key_levels)
    signal_markers = detect_signal_markers(bars, levels)
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
    )
    short_v, short_s, _ = _resolve_verdict(
        is_long=False,
        ms=ms,
        structure=structure,
        patterns=patterns,
        btc_ctx=btc_ctx,
        momentum=momentum,
        btc_spread=btc_spread,
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
        )
    if channel and channel.kind == "bear" and verdict == "LONG" and conf < 7:
        verdict = "WAIT"
        reason = (reason + " · канал ↓") if reason else "канал ↓ — ждать пробой"

    trade_is_long = is_long
    if neutral:
        if verdict == "SHORT" or action_priority == "short":
            trade_is_long = False
        elif verdict == "LONG" or action_priority == "long":
            trade_is_long = True

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


def ta_telegram_caption_html(ta: TAAnalysisResult) -> str:
    score_label = ta_display_score(ta)
    lines = [
        f"📐 <b>TA</b> · <b>{ta.verdict}</b> {score_label}/10",
        f"📍 <b>Сейчас:</b> {_situation_plain(ta)}",
        f"💡 <b>Смысл:</b> {_verdict_plain(ta)}",
    ]

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
            f"👉 <b>Действие:</b> WAIT, но <b>приоритет SHORT</b> — давление вниз, "
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
        lines.append(f"<i>Примечание: {ta.verdict_reason[:100]}</i>")
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
    header = f"📊 <b>Профессиональный разбор"
    if symbol:
        header += f" · {symbol}"
    if interval:
        header += f" · {interval}"
    header += "</b>"

    score = ta_display_score(ta)
    score_word = "ясность сетапа" if ta.verdict == "WAIT" else "уверенность"

    lines = [
        header,
        "",
        f"🎯 <b>Вердикт:</b> {ta.verdict} · {score_word} <b>{score}/10</b>",
    ]
    if ta.professional_summary:
        lines.append(f"💬 {ta.professional_summary}")
    if ta.verdict == "WAIT" and ta.action_priority == "short":
        lines.append("⚡ <b>Сейчас приоритет SHORT</b> — цена под давлением, long только через верхний пробой")
    elif ta.verdict == "WAIT" and ta.action_priority == "long":
        lines.append("⚡ <b>Сейчас приоритет LONG</b> — short только через нижний пробой")

    lines.append("")
    lines.append("📈 <b>Контекст рынка</b>")
    if ta.market_bias:
        lines.append(f"• Bias: <b>{ta.market_bias}</b>")
    if ta.momentum_label:
        lines.append(f"• Импульс: <b>{ta.momentum_label}</b>")
    if ta.structure_label:
        lines.append(f"• Структура: {_structure_plain(ta.structure_label)}")
    if ta.phase_label:
        lines.append(f"• Фаза: {ta.phase_label}")
    if ta.oi_narrative_label:
        lines.append(f"• OI: {ta.oi_narrative_label}")
    if ta.range_position:
        lines.append(f"• Позиция в range: <b>{ta.range_position * 100:.0f}%</b>")
    if ta.drawdown_from_high_pct:
        lines.append(f"• Откат от хая: <b>−{ta.drawdown_from_high_pct:.1f}%</b>")
    if ta.btc_context:
        lines.append(f"• BTC: {ta.btc_context}")

    if ta.key_levels:
        lines.append("")
        lines.append("🔑 <b>Ключевые уровни</b>")
        for i, kl in enumerate(ta.key_levels[:5], 1):
            lines.append(
                f"{i}. <b>{fmt_price(kl.price)}</b> — {kl.label} ({_key_level_role_ru(kl.role)})"
            )

    bull_lines = _scenario_block_html("Бычий сценарий", ta.bullish_scenario, emoji="📗")
    bear_lines = _scenario_block_html("Медвежий сценарий", ta.bearish_scenario, emoji="📕")
    if bull_lines or bear_lines:
        lines.append("")
    if ta.action_priority == "short":
        lines.extend(bear_lines)
        if bull_lines and bear_lines:
            lines.append("")
        lines.extend(bull_lines)
    else:
        lines.extend(bull_lines)
        if bull_lines and bear_lines:
            lines.append("")
        lines.extend(bear_lines)

    if ta.trader_plan:
        lines.append("")
        lines.append("📋 <b>План действий</b>")
        for i, step in enumerate(ta.trader_plan[:7], 1):
            lines.append(f"{i}. {step}")

    if ta.risk_notes:
        lines.append("")
        lines.append("⚠️ <b>Риски</b>")
        for risk in ta.risk_notes[:4]:
            lines.append(f"• {risk}")

    if ta.invalidation_price:
        lines.append("")
        lines.append(
            f"🛑 <b>Инвалидация:</b> сценарий отменяется при уходе за "
            f"<b>{fmt_price(ta.invalidation_price)}</b>"
        )

    lines.append("")
    lines.append("<i>Аналитика, не финансовая рекомендация.</i>")
    return "\n".join(lines)


def ta_chart_key_levels_text(ta: TAAnalysisResult) -> str:
    if not ta.key_levels:
        return ""
    lines = ["КЛЮЧЕВЫЕ УРОВНИ"]
    for kl in ta.key_levels[:4]:
        lines.append(f"{fmt_price(kl.price)} — {kl.label}")
    return "\n".join(lines)


def ta_chart_scenario_text(scenario: TradeScenario | None, *, title: str) -> str:
    if scenario is None:
        return ""
    lines = [title, f"триггер {fmt_price(scenario.trigger_price)}"]
    for cond in scenario.conditions[:2]:
        lines.append(f"• {cond}")
    tps = " → ".join(fmt_price(t) for t in scenario.target_prices[:2])
    lines.append(f"цели: {tps}")
    lines.append(f"стоп {fmt_price(scenario.stop_price)}")
    return "\n".join(lines)


def ta_chart_summary_text(ta: TAAnalysisResult) -> str:
    score = ta_display_score(ta)
    lines = [f"ИТОГ: {ta.verdict} {score}/10"]
    if ta.professional_summary:
        summary = ta.professional_summary
        if len(summary) > 120:
            summary = summary[:117] + "..."
        lines.append(summary)
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
        lines.append(f"LONG от: {fmt_price(ta.breakout_level)}")
    if ta.breakdown_level:
        lines.append(f"SHORT от: {fmt_price(ta.breakdown_level)}")
    if ta.invalidation_price:
        lines.append(f"стоп: {fmt_price(ta.invalidation_price)}")
    return "\n".join(lines[:6])


def ta_chart_legend_text() -> str:
    return (
        "█ зелёная зона = поддержка | █ красная = сопротивление | "
        "фиолет. = канал | STOP = стоп | TP = цель"
    )
