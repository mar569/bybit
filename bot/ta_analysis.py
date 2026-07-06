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

    if breakout:
        bullish = TradeScenario(
            direction="long",
            trigger_price=breakout,
            trigger_label="пробой и закрепление выше",
            stop_price=stop_long,
            target_prices=bull_targets,
            conditions=["пробой уровня", "объём растёт", "OI не против"],
        )
    if breakdown:
        bearish = TradeScenario(
            direction="short",
            trigger_price=breakdown,
            trigger_label="пробой ниже поддержки",
            stop_price=stop_short,
            target_prices=bear_targets,
            conditions=["отбой от сопр.", "CVD↓", "OI↑ при падении"],
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
) -> list[str]:
    plan: list[str] = []
    trigger = breakout or (bullish.trigger_price if bullish else None)
    inv = bearish.trigger_price if bearish else breakdown

    if trigger:
        plan.append(f"Ждать пробой {fmt_price(trigger)}")
        plan.append("Вход на ретесте уровня")
    elif entry_zone:
        plan.append(f"Вход в зоне {fmt_price(entry_zone[0])}–{fmt_price(entry_zone[1])}")
    else:
        plan.append("Ждать подтверждения структуры")

    if bullish:
        plan.append(f"Стоп ниже {fmt_price(bullish.stop_price)}")
        tps = " → ".join(fmt_price(t) for t in bullish.target_prices[:3])
        plan.append(f"Тейки long: {tps}")
    if inv:
        plan.append(f"Альтернатива: пробой {fmt_price(inv)} → short")

    if verdict == "WAIT" and trigger:
        plan.insert(0, f"Итог: WAIT — ключ {fmt_price(trigger)}")
    elif verdict in {"LONG", "SHORT"}:
        plan.insert(0, f"Итог: {verdict}")

    return plan[:6]


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


def _resolve_verdict(
    *,
    is_long: bool,
    ms: MarketStructureContext,
    structure: str,
    patterns: list[CandlePattern],
    btc_ctx: str,
) -> tuple[str, int, str]:
    score = 5
    reasons: list[str] = []
    if is_long:
        if ms.phase in {"correction_down", "consolidation", "breakout_setup"}:
            score += 2
            reasons.append(ms.phase_label)
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
        if ms.phase in {"correction_up", "consolidation", "breakout_setup", "post_crash_weak"}:
            score += 2
            reasons.append(ms.phase_label)
        elif ms.phase in {"impulse_down"}:
            score -= 1
        elif ms.phase in {"impulse_up"}:
            score -= 2
        if "медв." in structure:
            score += 1
        elif "бычья" in structure:
            score -= 2
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
        score -= 1
        reasons.append("дивергенция с BTC")
    if ms.range_position > 0.78:
        score -= 1
        reasons.append("у верха range")
    elif ms.range_position < 0.22:
        score -= 1
        reasons.append("у дна range")
    score = int(_clamp(score, 1, 9))
    if score >= 7:
        verdict = "LONG" if is_long else "SHORT"
    elif score <= 4:
        verdict = "WAIT"
    else:
        verdict = "WAIT"
    reason = " · ".join(reasons[:3]) if reasons else ms.phase_detail
    return verdict, score, reason


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
    if symbol.upper() not in {"BTCUSDT", "BTCUSD", "BTCUSDC"}:
        btc_ctx = btc_correlation_label(btc_bars, bars)
    verdict, conf, reason = _resolve_verdict(
        is_long=is_long, ms=ms, structure=structure, patterns=patterns, btc_ctx=btc_ctx,
    )
    if channel and channel.kind == "bear" and verdict == "LONG" and conf < 7:
        verdict = "WAIT"
        reason = (reason + " · канал ↓") if reason else "канал ↓ — ждать пробой"
    inv, entry, targets = _trade_levels(
        bars, levels, is_long=is_long, invalidation=invalidation_price,
        bullish=bullish, bearish=bearish,
    )
    trader_plan = build_trader_plan(
        verdict=verdict,
        bullish=bullish,
        bearish=bearish,
        breakout=breakout,
        breakdown=breakdown,
        entry_zone=entry,
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


def ta_telegram_caption_html(ta: TAAnalysisResult) -> str:
    lines = [
        f"📐 <b>TA</b> · <b>{ta.verdict}</b> {ta.verdict_confidence}/10 · {ta.phase_label}",
        f"структура: {ta.structure_label}",
    ]
    if ta.breakout_level:
        lines.append(f"🔑 пробой <b>{fmt_price(ta.breakout_level)}</b> → long")
    if ta.breakdown_level:
        lines.append(f"🔻 пробой <b>{fmt_price(ta.breakdown_level)}</b> → short")
    if ta.invalidation_price:
        lines.append(f"🛑 стоп <b>{fmt_price(ta.invalidation_price)}</b>")
    if ta.target_prices:
        tps = " / ".join(fmt_price(t) for t in ta.target_prices[:4])
        lines.append(f"🎯 цели: {tps}")
    if ta.verdict_reason:
        lines.append(f"<i>{ta.verdict_reason[:120]}</i>")
    if ta.verdict == "LONG":
        if ta.breakout_level:
            lines.append(f"👉 Позиция: <b>LONG</b> при закреплении выше <b>{fmt_price(ta.breakout_level)}</b>")
        else:
            lines.append("👉 Позиция: <b>LONG</b> (вход по подтверждению импульса)")
    elif ta.verdict == "SHORT":
        if ta.breakdown_level:
            lines.append(f"👉 Позиция: <b>SHORT</b> при пробое ниже <b>{fmt_price(ta.breakdown_level)}</b>")
        else:
            lines.append("👉 Позиция: <b>SHORT</b> (вход по подтверждению снижения)")
    else:
        long_hint = fmt_price(ta.breakout_level) if ta.breakout_level else "локального сопротивления"
        short_hint = fmt_price(ta.breakdown_level) if ta.breakdown_level else "локальной поддержки"
        lines.append(
            f"👉 Позиция: <b>WAIT</b> · long при пробое <b>{long_hint}</b> / short при пробое <b>{short_hint}</b>"
        )
    return "\n".join(lines)


def ta_chart_panel_text(ta: TAAnalysisResult) -> str:
    lines = [f"ВЕРДИКТ: {ta.verdict} {ta.verdict_confidence}/10"]
    if ta.channel:
        lines.append(ta.channel.label)
    if ta.key_levels:
        for kl in ta.key_levels[:4]:
            lines.append(f"{kl.label}: {fmt_price(kl.price)}")
    return "\n".join(lines)
