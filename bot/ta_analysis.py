"""Технический анализ OHLC: уровни, тренды, паттерны, линейка %, BTC-контекст."""
from __future__ import annotations

from dataclasses import dataclass, field

from .bybit_klines import KlineBar
from .market_structure import FiveMinOiBar, MarketStructureContext, analyze_market_structure
from .smc_analysis import SmcContext, analyze_smc, format_smc_compact_html, smc_verdict_boost
from .ta_range_trade import (
    MarketFlowScores,
    RangeTradeSetup,
    build_factor_context,
    detect_liq_cascade_short,
    evaluate_market_flow,
    evaluate_range_trade,
)
from .wave_structure import (
    FibLevel,
    WaveStructureResult,
    analyze_wave_structure,
    apply_wave_to_trade_plan,
    wave_flow_adjustments,
)
from .chart_pattern_models import ChartPattern
from .chart_patterns import detect_chart_patterns, format_chart_pattern_compact, pick_primary_pattern


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
class ForecastPath:
    """Пунктирный прогноз на графике: коррекция или продолжение импульса."""
    kind: str  # correction / continuation
    label: str
    waypoints: list[float]
    confidence: int
    reason: str


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
    range_trade_direction: str = ""
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
    repeat_spike_dump_risk: bool = False
    repeat_spike_dump_note: str = ""
    correction_path: ForecastPath | None = None
    continuation_path: ForecastPath | None = None
    forecast_summary: str = ""
    flow_continuation: int = 0
    flow_correction: int = 0
    flow_notes: list[str] = field(default_factory=list)
    narrative_plain: str = ""
    narrative_plan: str = ""
    narrative_basis: str = ""
    liq_cascade_active: bool = False
    liq_cascade_note: str = ""
    cvd_source: str = "proxy"
    cvd_delta: float | None = None
    # Wave Lite + Fib (график + понятный UX в Hot/Pro)
    fib_levels: list[FibLevel] = field(default_factory=list)
    wave_phase: str = ""
    wave_bias: str = "neutral"
    wave_confidence: int = 0
    wave_leg_start: float | None = None
    wave_leg_end: float | None = None
    wave_has_confluence: bool = False
    wave_confluence_count: int = 0
    wave_confluence_sr: bool = False
    wave_confluence_round: bool = False
    wave_confluence_retest: bool = False
    elliott_label: str = ""
    abc_phase: str = ""
    abc_label_ru: str = ""
    elliott_phase: str = ""
    elliott_confidence: int = 0
    elliott_entry_mode: str = ""
    elliott_entry_ready: bool = False
    elliott_entry_price: float | None = None
    elliott_stop_price: float | None = None
    elliott_tp_prices: list[float] = field(default_factory=list)
    elliott_draw_points: list = field(default_factory=list)
    elliott_fib_classic_ok: bool = False
    elliott_fib_w2: float = 0.0
    elliott_fib_w4: float = 0.0
    # PPT-структуры: растяжение / усечение / диагональ / тип ABC
    elliott_extension: str = ""
    elliott_truncated: bool = False
    elliott_diagonal: str = ""
    elliott_corr_type: str = ""
    elliott_structure_note: str = ""
    elliott_triangle_kind: str = ""
    elliott_triangle_bias: str = ""
    elliott_complex_kind: str = ""
    elliott_fib_targets: list[float] = field(default_factory=list)
    elliott_fib_target_labels: list[str] = field(default_factory=list)
    elliott_path_bias: str = ""
    elliott_path_prices: list[float] = field(default_factory=list)
    elliott_path_labels: list[str] = field(default_factory=list)
    elliott_path_reason: str = ""
    elliott_triangle_obj: object | None = None
    elliott_global_draw_points: list = field(default_factory=list)
    elliott_local_draw_points: list = field(default_factory=list)
    elliott_global_label: str = ""
    elliott_local_label: str = ""
    fib_status: str = ""
    fib_reject_reason: str = ""
    chart_patterns: list[ChartPattern] = field(default_factory=list)
    primary_chart_pattern: ChartPattern | None = None
    # Pro confluence (HTF EW + фигуры + Fib + SMC)
    setup_score: int = 0
    setup_grade: str = ""
    setup_side: str = "neutral"
    setup_label_ru: str = ""
    setup_factors: list[str] = field(default_factory=list)
    setup_ideal_ready: bool = False
    setup_entry: float | None = None
    setup_stop: float | None = None
    setup_tps: list[float] = field(default_factory=list)
    setup_trigger: str = ""
    htf_elliott_label: str = ""
    htf_elliott_phase: str = ""
    htf_elliott_bias: str = "neutral"
    is_ending_diagonal: bool = False
    is_abcde: bool = False
    forecast_path_prices: list[float] = field(default_factory=list)
    forecast_path_labels: list[str] = field(default_factory=list)
    htf_elliott_draw_points: list = field(default_factory=list)


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


def _verdict_to_side(verdict: str) -> str | None:
    if verdict == "LONG":
        return "long"
    if verdict == "SHORT":
        return "short"
    return None


def _range_trade_matches_verdict(verdict: str, range_trade: RangeTradeSetup | None) -> bool:
    side = _verdict_to_side(verdict)
    if not side or range_trade is None:
        return False
    return range_trade.direction == side


def ta_range_trade_opposes_verdict(ta: TAAnalysisResult) -> bool:
    """Range-сетап (край боковика) противоречит вердикту LONG/SHORT."""
    if ta.verdict not in {"LONG", "SHORT"} or not ta.range_trade_direction:
        return False
    want = _verdict_to_side(ta.verdict)
    return bool(want and ta.range_trade_direction != want)


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

    near_res = sorted([p for p in resistances if p > current * 1.0002])
    near_sup = sorted([p for p in supports if p < current * 0.9998], reverse=True)

    if post_pump and local_cons:
        # Низ range после пампа — стабильный SHORT-триггер.
        breakdown = local_cons.bottom
        # ВАЖНО: не ставить LONG-триггер = скользящий max(high) окна.
        # Иначе при каждом апдейте «закрепление ≥X» убегает вверх вместе с ценой
        # (пользователь видит «цена уже выше», а бот снова пишет WAIT + новый X).
        breakout = _post_pump_stable_breakout(
            current=current,
            structural=breakout,
            local_r=local_r,
            cons_top=local_cons.top,
            near_resistances=near_res,
            bars=bars,
        )
    elif local_cons and not breakout and not breakdown:
        breakout = local_cons.top
        breakdown = local_cons.bottom

    if not breakout and local_r:
        breakout = local_r
    if not breakdown and local_s:
        breakdown = local_s

    # Анти-аномалия: не уводить триггер слишком далеко от текущей цены.
    # Если ближайший валидный уровень далеко, лучше оставить триггер пустым и ждать
    # нового формирования структуры, чем предлагать "short ниже всего графика".
    hard_cap = 12.0 if post_pump else 16.0

    if breakout and current > 0:
        dist_up = (breakout - current) / current * 100.0
        if dist_up > hard_cap:
            candidate = next((p for p in near_res if (p - current) / current * 100.0 <= hard_cap), None)
            breakout = candidate
    if breakdown and current > 0:
        dist_down = (current - breakdown) / current * 100.0
        if dist_down > hard_cap:
            candidate = next((p for p in near_sup if (current - p) / current * 100.0 <= hard_cap), None)
            breakdown = candidate

    cons = local_cons if post_pump and local_cons else None
    return breakout, breakdown, cons, post_pump


def _post_pump_stable_breakout(
    *,
    current: float,
    structural: float | None,
    local_r: float | None,
    cons_top: float,
    near_resistances: list[float],
    bars: list[KlineBar],
) -> float | None:
    """Триггер LONG после пампа без «преследования» ценового хая.

    Верх range берём по окну *без* последних 2–3 свечей — иначе текущий
    импульсный хай постоянно переопределяет «закрепление ≥X».
    """
    if current <= 0:
        return structural

    stable_top = _stable_consolidation_top(bars, lookback=32, exclude_recent=3)
    if stable_top is None:
        stable_top = cons_top

    # 1) Структурное сопротивление ещё выше цены — оставляем.
    if structural and structural > current * 1.002:
        return structural

    # 2) Ближайший swing-high / resistance выше цены.
    if local_r and local_r > current * 1.002:
        return local_r
    if near_resistances:
        nxt = near_resistances[0]
        if nxt > current * 1.002:
            return nxt

    # 3) Стабильный верх боковика (до текущего импульса).
    if stable_top > current * 1.0015:
        return stable_top

    # 4) Цена уже взяла стабильный верх — не поднимаем триггер к max(high).
    #    Следующий ориентир = хай пампа в окне, если ещё есть запас.
    lookback = min(30, len(bars))
    if lookback >= 5:
        pump_peak = max(b.high for b in bars[-lookback:])
        if pump_peak > current * 1.006 and pump_peak > stable_top * 1.004:
            return pump_peak

    # Возвращаем пробитый уровень — для intent «уровень уже взят», не новый хай.
    return stable_top if stable_top > 0 else (cons_top if cons_top > 0 else structural)


def _stable_consolidation_top(
    bars: list[KlineBar],
    *,
    lookback: int = 32,
    exclude_recent: int = 3,
) -> float | None:
    """Верх локального range без хвоста текущего импульса."""
    if len(bars) < 10:
        return None
    lb = min(lookback, len(bars))
    end = len(bars) - max(0, exclude_recent)
    start = max(0, len(bars) - lb)
    if end - start < 5:
        segment = bars[-lb:]
    else:
        segment = bars[start:end]
    if not segment:
        return None
    return max(b.high for b in segment)


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


def _pick_pullback_target(
    *,
    current: float,
    consolidation: ConsolidationZone | None,
    smc: SmcContext | None,
    nearest_support: float | None,
    breakdown: float | None,
    rulers: list[RulerMeasurement],
) -> float | None:
    """Ближайшая зона отката ниже текущей цены (2–25% от цены)."""
    if current <= 0:
        return None
    candidates: list[float] = []
    if smc and smc.equilibrium_50 and smc.equilibrium_50 < current * 0.998:
        candidates.append(smc.equilibrium_50)
    if smc and smc.discount_zone:
        lo, hi = smc.discount_zone
        candidates.extend([hi, (lo + hi) / 2.0, lo])
    if consolidation:
        candidates.append(consolidation.bottom)
        candidates.append((consolidation.top + consolidation.bottom) / 2.0)
    if nearest_support and nearest_support < current:
        candidates.append(nearest_support)
    if breakdown and breakdown < current:
        candidates.append(breakdown)
    if smc and smc.fvgs:
        for gap in reversed(smc.fvgs):
            if gap.direction == "bullish" and gap.bottom < current:
                candidates.append((gap.top + gap.bottom) / 2.0)
                break
    for ruler in rulers:
        if ruler.pct > 0 and ruler.to_price > ruler.from_price:
            mid = ruler.from_price + (ruler.to_price - ruler.from_price) * 0.5
            if mid < current:
                candidates.append(mid)
    valid = [
        c for c in candidates
        if c > 0 and 2.0 <= (current - c) / current * 100.0 <= 25.0
    ]
    if not valid:
        return None
    return max(valid)


def build_market_forecast_paths(
    *,
    current: float,
    post_pump: bool,
    phase: str,
    range_position: float,
    drawdown_from_high_pct: float,
    momentum: str,
    momentum_pct: float,
    market_bias: str,
    oi_narrative: str,
    oi_context_strength: float,
    cvd_ratio: float,
    liq_long_boost: int,
    liq_short_boost: int,
    factor_lines: list[str],
    consolidation: ConsolidationZone | None,
    smc: SmcContext | None,
    breakout: float | None,
    breakdown: float | None,
    nearest_support: float | None,
    nearest_resistance: float | None,
    target_prices: list[float],
    repeat_spike_dump_risk: bool,
    verdict: str,
    rulers: list[RulerMeasurement],
    flow: MarketFlowScores | None = None,
) -> tuple[ForecastPath | None, ForecastPath | None, str]:
    """
    Прогноз «коррекция vs продолжение» после импульса/смыва шортов.
    Использует матрицу OI + CVD + ликвидации (как CoinGlass), без отдельного UI.
    """
    if current <= 0:
        return None, None, ""

    relevant = (
        post_pump
        or repeat_spike_dump_risk
        or phase in {"impulse_up", "correction_down", "consolidation", "breakout_setup"}
        or range_position > 0.62
    )
    if not relevant:
        return None, None, ""

    pullback = _pick_pullback_target(
        current=current,
        consolidation=consolidation,
        smc=smc,
        nearest_support=nearest_support,
        breakdown=breakdown,
        rulers=rulers,
    )

    cont_candidates = [p for p in target_prices if p > current * 1.002]
    if breakout and breakout > current * 1.001:
        cont_candidates.append(breakout)
    if nearest_resistance and nearest_resistance > current * 1.001:
        cont_candidates.append(nearest_resistance)
    cont_tp = min(cont_candidates) if cont_candidates else None

    if flow is None:
        flow = evaluate_market_flow(
            momentum=momentum,
            momentum_pct=momentum_pct,
            phase=phase,
            oi_narrative=oi_narrative,
            oi_context_strength=oi_context_strength,
            cvd_ratio=cvd_ratio,
            liq_long_boost=liq_long_boost,
            liq_short_boost=liq_short_boost,
            range_position=range_position,
            post_pump=post_pump,
            drawdown_from_high_pct=drawdown_from_high_pct,
        )

    corr_score = flow.correction // 4
    cont_score = flow.continuation // 4
    factors_ru: list[str] = list(flow.notes[:4])

    if post_pump:
        corr_score += 24
        if "после пампа" not in factors_ru:
            factors_ru.append("после пампа")
    if momentum == "down" and momentum_pct <= -1.0 and (
        post_pump or drawdown_from_high_pct >= 2.5 or repeat_spike_dump_risk
    ):
        corr_score += 22
        if "импульс вниз" not in factors_ru:
            factors_ru.append(f"импульс вниз {momentum_pct:+.1f}%")
        if liq_long_boost > 0:
            corr_score += 14
            factors_ru.append("liq long ↑")
        if oi_narrative in {"long_unwind", "aligned_short", "capitulation"}:
            corr_score += 10
            factors_ru.append(f"OI: {oi_narrative}")
    if phase == "impulse_up" and range_position > 0.75:
        corr_score += 18
    if repeat_spike_dump_risk:
        corr_score += 20
        factors_ru.append("паттерн spike→dump")
    if drawdown_from_high_pct < 2.5 and range_position > 0.82:
        corr_score += 14
        factors_ru.append("у верха range")
    if phase == "correction_down":
        corr_score += 10

    if momentum == "up" and momentum_pct >= 1.0:
        cont_score += 8
    if market_bias == "бычий":
        cont_score += 8
    if phase == "impulse_up":
        cont_score += 6

    corr_conf = int(_clamp(4 + corr_score / 8, 4, 9))
    cont_conf = int(_clamp(4 + cont_score / 8, 4, 9))

    correction_path: ForecastPath | None = None
    continuation_path: ForecastPath | None = None

    if pullback:
        pb_pct = (current - pullback) / current * 100.0
        dip = current * 0.996
        deep = pullback
        if momentum == "down" and momentum_pct <= -1.5 and (post_pump or drawdown_from_high_pct >= 3):
            deep = min(pullback, current * (0.97 if post_pump else 0.985))
            pb_pct = (current - deep) / current * 100.0
        correction_path = ForecastPath(
            kind="correction",
            label="слив ↓" if momentum == "down" and pb_pct >= 4 else "коррекция ↓",
            waypoints=[current, dip, deep, deep * (0.996 if momentum == "down" else 1.004)],
            confidence=corr_conf,
            reason=f"откат к {fmt_price(deep)} (−{pb_pct:.1f}%)",
        )

    if cont_tp is not None and cont_tp > current * 1.003:
        shallow_dip = current * (0.992 if post_pump else 0.996)
        continuation_path = ForecastPath(
            kind="continuation",
            label="продолжение ↑",
            waypoints=[current, shallow_dip, breakout or current * 1.004, cont_tp],
            confidence=cont_conf,
            reason=f"рост к {fmt_price(cont_tp)}",
        )

    if correction_path is None and continuation_path is None:
        return None, None, ""

    # Иерархия: вердикт режет противоположный путь (не «и вверх и вниз»)
    v = (verdict or "").upper()
    if v == "SHORT":
        continuation_path = None
    elif v == "LONG":
        correction_path = None
    elif v == "WAIT":
        # В WAIT направленных стрелок нет — только текст bias
        primary = "коррекция" if (correction_path and (
            not continuation_path or correction_path.confidence >= continuation_path.confidence
        )) else "продолжение"
        factors = ", ".join(dict.fromkeys(factors_ru[:3])) or "нет явного импульса"
        summary = (
            f"WAIT — ждать подтверждение пробоя. Локальный bias: {primary}. "
            f"Факторы: {factors}."
        )
        return None, None, summary

    if correction_path is None and continuation_path is None:
        return None, None, ""

    corr_conf = correction_path.confidence if correction_path else 0
    cont_conf = continuation_path.confidence if continuation_path else 0

    primary = "коррекция" if corr_conf >= cont_conf else "продолжение"
    if correction_path and continuation_path:
        alt = "продолжение" if primary == "коррекция" else "коррекция"
        summary = (
            f"Базовый сценарий: {primary}. Альтернатива: {alt}. "
            f"Факторы: {', '.join(dict.fromkeys(factors_ru[:4]))}."
        )
    elif correction_path:
        summary = f"Ожидается коррекция. {correction_path.reason}. Факторы: {', '.join(dict.fromkeys(factors_ru[:3]))}."
    else:
        summary = f"Ожидается продолжение. {continuation_path.reason if continuation_path else ''}."

    return correction_path, continuation_path, summary


def _narrative_basis_parts(
    *,
    phase_label: str,
    structure_label: str,
    momentum_label: str,
    oi_narrative_label: str,
    market_bias: str,
    flow: MarketFlowScores,
    factor_lines: list[str],
    verdict_reason: str,
) -> list[str]:
    parts: list[str] = []
    if phase_label and phase_label != "Без явной фазы":
        parts.append(f"фаза: {phase_label}")
    if structure_label:
        parts.append(f"структура: {structure_label}")
    if momentum_label:
        parts.append(momentum_label)
    if market_bias:
        parts.append(f"уклон: {market_bias}")
    if oi_narrative_label and oi_narrative_label != "Мало данных OI":
        parts.append(f"OI: {oi_narrative_label}")
    for note in flow.notes:
        if note not in parts:
            parts.append(note)
    for line in factor_lines:
        if line and line not in " · ".join(parts):
            parts.append(line)
    if verdict_reason and verdict_reason not in " · ".join(parts):
        parts.append(verdict_reason[:80])
    return parts[:7]


def build_ta_signal_narrative(
    *,
    verdict: str,
    current: float,
    target_prices: list[float],
    invalidation_price: float | None,
    breakout_level: float | None,
    breakdown_level: float | None,
    bullish: TradeScenario | None,
    bearish: TradeScenario | None,
    flow: MarketFlowScores,
    correction_path: ForecastPath | None,
    continuation_path: ForecastPath | None,
    factor_lines: list[str],
    phase_label: str,
    structure_label: str,
    momentum_label: str,
    oi_narrative_label: str,
    market_bias: str,
    range_trade_label: str,
    primary_scenario: str,
    verdict_reason: str,
) -> tuple[str, str, str]:
    """
    Единый текст для сигналов: plain / plan / basis.
    Строится только из собранного анализа (flow, уровни, сценарии, OI/CVD/liq).
    """
    basis_list = _narrative_basis_parts(
        phase_label=phase_label,
        structure_label=structure_label,
        momentum_label=momentum_label,
        oi_narrative_label=oi_narrative_label,
        market_bias=market_bias,
        flow=flow,
        factor_lines=factor_lines,
        verdict_reason=verdict_reason,
    )
    flow_human = "факторы смешаны — ждать уровень"
    if flow.continuation - flow.correction >= 12:
        flow_human = "скорее продолжение импульса"
    elif flow.correction - flow.continuation >= 12:
        flow_human = "скорее коррекция / откат"
    flow_tag = f"🧭 {flow_human} (cont {flow.continuation} / corr {flow.correction})"
    basis = f"📊 <b>На чём основано:</b> {' · '.join(basis_list)} · {flow_tag}"

    def _path_target(path: ForecastPath | None, *, correction: bool) -> float | None:
        if path is None or not path.waypoints:
            return None
        if correction and len(path.waypoints) >= 3:
            return path.waypoints[2]
        return path.waypoints[-1]

    if verdict == "SHORT":
        scenario = bearish
        tp = (
            target_prices[0]
            if target_prices
            else (scenario.target_prices[0] if scenario and scenario.target_prices else None)
        )
        if tp is None:
            tp = _path_target(correction_path, correction=True) or breakdown_level
        trigger = breakdown_level or (scenario.trigger_price if scenario else None)
        stop = invalidation_price or (scenario.stop_price if scenario else None)
        path_reason = correction_path.reason if correction_path else primary_scenario
        if tp:
            plain = (
                f"📉 <b>Простыми словами:</b> TA <b>SHORT</b> — цель снижения "
                f"<b>{fmt_price(tp)}</b>"
                f"{f' ({path_reason})' if path_reason else ''}."
            )
        else:
            plain = (
                f"📉 <b>Простыми словами:</b> TA <b>SHORT</b>"
                f"{f' — {path_reason}' if path_reason else ''}."
            )
        if breakdown_level and current > breakdown_level * 1.002:
            bounce_to = breakout_level or _path_target(continuation_path, correction=False)
            if bounce_to and bounce_to > current:
                plain += (
                    f" Сейчас отскок — возможен дожим к <b>{fmt_price(bounce_to)}</b>, "
                    f"затем short при ≤<b>{fmt_price(breakdown_level)}</b>."
                )
            else:
                plain += (
                    f" Сейчас цена выше триггера — short <b>не по рынку</b>, "
                    f"только после ≤<b>{fmt_price(breakdown_level)}</b>."
                )
        if continuation_path and flow.continuation >= flow.correction - 12:
            alt = _path_target(continuation_path, correction=False)
            if alt and alt > current:
                plain += (
                    f" Риск отмены: поток допускает отскок к <b>{fmt_price(alt)}</b> "
                    f"(cont {flow.continuation} vs corr {flow.correction})."
                )
        plan_bits: list[str] = []
        if range_trade_label:
            plan_bits.append(range_trade_label)
        if trigger:
            plan_bits.append(f"вход ≤<b>{fmt_price(trigger)}</b>")
        if tp:
            plan_bits.append(f"цель <b>{fmt_price(tp)}</b>")
        if stop:
            plan_bits.append(f"стоп <b>{fmt_price(stop)}</b>")
        plan = f"👉 <b>План:</b> SHORT · {' · '.join(plan_bits)}." if plan_bits else ""
        return plain, plan, basis

    if verdict == "LONG":
        scenario = bullish
        tp = (
            target_prices[0]
            if target_prices
            else (scenario.target_prices[0] if scenario and scenario.target_prices else None)
        )
        if tp is None:
            tp = _path_target(continuation_path, correction=False) or breakout_level
        trigger = breakout_level or (scenario.trigger_price if scenario else None)
        stop = invalidation_price or (scenario.stop_price if scenario else None)
        path_reason = continuation_path.reason if continuation_path else primary_scenario
        if tp:
            plain = (
                f"📈 <b>Простыми словами:</b> TA <b>LONG</b> — цель роста "
                f"<b>{fmt_price(tp)}</b>"
                f"{f' ({path_reason})' if path_reason else ''}."
            )
        else:
            plain = (
                f"📈 <b>Простыми словами:</b> TA <b>LONG</b>"
                f"{f' — {path_reason}' if path_reason else ''}."
            )
        if correction_path and flow.correction >= flow.continuation - 12:
            alt = _path_target(correction_path, correction=True)
            if alt and alt < current:
                plain += (
                    f" Риск отмены: откат к <b>{fmt_price(alt)}</b> "
                    f"(corr {flow.correction} vs cont {flow.continuation})."
                )
        plan_bits = []
        if range_trade_label:
            plan_bits.append(range_trade_label)
        if trigger:
            plan_bits.append(f"вход ≥<b>{fmt_price(trigger)}</b>")
        if tp:
            plan_bits.append(f"цель <b>{fmt_price(tp)}</b>")
        if stop:
            plan_bits.append(f"стоп <b>{fmt_price(stop)}</b>")
        plan = f"👉 <b>План:</b> LONG · {' · '.join(plan_bits)}." if plan_bits else ""
        return plain, plan, basis

    # WAIT — сценарий из потока и реальных уровней, без входа
    if flow.correction > flow.continuation + 10 and correction_path:
        tgt = _path_target(correction_path, correction=True)
        reason = correction_path.reason or ""
        tgt_suffix = ""
        if tgt and fmt_price(tgt) not in reason:
            tgt_suffix = f" к ~<b>{fmt_price(tgt)}</b>"
        plain = (
            f"📐 <b>Простыми словами:</b> WAIT — поток за <b>откат</b> "
            f"{reason}{tgt_suffix}. "
            "Это не вход — ждём подтверждения уровня."
        )
    elif flow.continuation > flow.correction + 10 and continuation_path:
        tgt = _path_target(continuation_path, correction=False)
        reason = continuation_path.reason or ""
        tgt_suffix = ""
        if tgt and fmt_price(tgt) not in reason:
            tgt_suffix = f" к ~<b>{fmt_price(tgt)}</b>"
        plain = (
            f"📐 <b>Простыми словами:</b> WAIT — поток за <b>продолжение</b> "
            f"{reason}{tgt_suffix}. "
            "Это не вход — ждём подтверждения уровня."
        )
    elif primary_scenario:
        plain = (
            f"📐 <b>Простыми словами:</b> WAIT — {primary_scenario}. "
            "Это не вход — ждём подтверждения."
        )
    else:
        plain = (
            "📐 <b>Простыми словами:</b> WAIT — нет подтверждённого направления. "
            "Ждём пробой уровня."
        )

    long_lvl = fmt_price(breakout_level) if breakout_level else "—"
    short_lvl = fmt_price(breakdown_level) if breakdown_level else "—"
    plan = (
        f"👉 <b>План:</b> вне сделки · LONG выше <b>{long_lvl}</b> · "
        f"SHORT ниже <b>{short_lvl}</b>."
    )
    return plain, plan, basis


def detect_repeat_spike_dump_risk(
    bars: list[KlineBar],
    *,
    spike_pct: float = 5.0,
    retrace_ratio: float = 0.55,
    lookahead_bars: int = 18,
) -> tuple[bool, str]:
    """
    Ищет повторяемый паттерн: резкий памп -> заметный откат/слив вскоре после.
    Возвращает флаг риска и короткую заметку для текста анализа.
    """
    n = len(bars)
    if n < 30:
        return False, ""

    events: list[tuple[int, float, float]] = []
    for i in range(6, n - 4):
        base = bars[i - 6].close
        if base <= 0:
            continue
        spike = (bars[i].high - base) / base * 100.0
        if spike < spike_pct:
            continue
        peak = bars[i].high
        end = min(n - 1, i + lookahead_bars)
        post_low = min(b.low for b in bars[i + 1 : end + 1])
        drop_from_peak = (peak - post_low) / peak
        if drop_from_peak >= retrace_ratio:
            events.append((i, spike, drop_from_peak * 100.0))

    if not events:
        return False, ""

    # Если таких эпизодов >=2, считаем, что есть повторяемость поведения.
    repeat = len(events) >= 2
    last_i, last_spike, last_drop = events[-1]
    age = max(0, n - 1 - last_i)
    note = (
        f"повторяемый spike→dump: {len(events)} эпиз., "
        f"последний +{last_spike:.1f}% -> −{last_drop:.0f}% ({age} баров назад)"
    )
    return repeat, note


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
        if is_long:
            score -= 1
            reasons.append("у верха range — long рискован")
        else:
            score += 1
            reasons.append("у верха range — short ближе")
    elif ms.range_position < 0.22:
        if is_long:
            score += 1
            reasons.append("у дна range — long ближе")
        else:
            score -= 1
            reasons.append("у дна range — short рискован")
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


def _trade_quality_guard(
    *,
    verdict: str,
    current: float,
    stop: float | None,
    targets: list[float],
) -> tuple[bool, str]:
    """
    Возвращает (is_bad, reason), если вход статистически невыгоден.
    Нужен, чтобы не оставлять LONG/SHORT при плохом R:R.
    """
    if verdict not in {"LONG", "SHORT"} or current <= 0 or not stop or not targets:
        return False, ""
    tp1 = targets[0]
    if verdict == "LONG":
        if stop >= current * 0.999:
            return True, "стоп выше цены — LONG некорректен"
        if tp1 <= current * 0.999:
            return True, "цель ниже цены — LONG некорректен"
    elif verdict == "SHORT":
        if stop <= current * 1.001:
            return True, "стоп ниже цены — SHORT некорректен"
        if tp1 >= current * 1.001:
            return True, "цель выше цены — SHORT некорректен"
    risk_pct = abs(current - stop) / current * 100.0
    reward_pct = abs(tp1 - current) / current * 100.0
    if risk_pct <= 0:
        return False, ""
    rr = reward_pct / risk_pct
    if rr < 0.8 or (risk_pct > 8.0 and reward_pct < 3.0):
        return True, f"вход невыгоден: риск {risk_pct:.1f}% к TP1 {reward_pct:.1f}% (R:R {rr:.2f})"
    return False, ""


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
    history_bars: list[KlineBar] | None = None,
    taker_cvd: object | None = None,
    pattern_detection_enabled: bool = True,
    pattern_min_confidence: float = 0.55,
) -> TAAnalysisResult:
    oi_bars = oi_bars or []
    swings = find_swing_points(bars)
    levels = detect_horizontal_levels(bars, swings)
    zones = detect_price_zones(bars, swings)
    channel = detect_channel(bars, swings)
    trend_lines = detect_trend_lines(bars, swings)
    consolidation = detect_consolidation(bars)
    patterns = detect_candle_patterns(bars)
    pattern_bars = list(history_bars or bars)
    chart_patterns = detect_chart_patterns(
        pattern_bars,
        enabled=pattern_detection_enabled,
        min_confidence=pattern_min_confidence,
    )
    primary_chart_pattern = pick_primary_pattern(chart_patterns)
    rulers = compute_rulers(bars, swings)
    structure = classify_structure(swings)
    key_levels = build_key_levels(bars, levels, zones)
    bullish, bearish, breakout, breakdown = build_scenarios(
        bars, levels, zones, key_levels, swings,
    )
    # Fib после сильных факторов (П/С, пробой) — не раньше
    sr_prices = [lv.price for lv in levels] + [kl.price for kl in key_levels]
    for z in zones:
        sr_prices.append((z.top + z.bottom) / 2.0)
    wave = analyze_wave_structure(
        bars,
        swings,
        structure_label=structure,
        sr_prices=sr_prices,
        breakout=breakout,
        breakdown=breakdown,
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
    # Pro confluence: HTF Elliott + фигуры + Fib + SMC → идеальный вход
    from .setup_confluence import analyze_setup_confluence
    from .elliott_wave import ElliottWaveResult

    ew_ltf = getattr(wave, "elliott_result", None)
    if ew_ltf is not None and not isinstance(ew_ltf, ElliottWaveResult):
        ew_ltf = None
    setup = analyze_setup_confluence(
        bars,
        swings,
        htf_bars=htf_bars,
        wave=wave,
        ew_ltf=ew_ltf,
        pattern=primary_chart_pattern,
        smc=smc,
        current=bars[-1].close if bars else None,
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

    factors = build_factor_context(bars, liq_context, taker_cvd=taker_cvd)
    repeat_spike_dump_risk, repeat_spike_dump_note = detect_repeat_spike_dump_risk(bars)
    if history_bars:
        hist_risk, hist_note = detect_repeat_spike_dump_risk(history_bars)
        if hist_risk:
            repeat_spike_dump_risk = True
            if hist_note:
                repeat_spike_dump_note = hist_note
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
            or repeat_spike_dump_risk
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

    liq_cascade = detect_liq_cascade_short(
        factors=factors,
        liq=liq_context,
        momentum=momentum,
        momentum_pct=momentum_pct,
        drawdown_pct=ms.drawdown_from_high_pct,
        oi_narrative=ms.oi_narrative,
    )
    cascade_tight_stop: float | None = None
    if liq_cascade.active and liq_cascade.side == "short" and bars:
        lookback = bars[-min(6, len(bars)) :]
        swing_high = max(b.high for b in lookback)
        cascade_tight_stop = swing_high * 1.003
        risk_pct = (cascade_tight_stop - current) / current * 100.0 if current > 0 else 99.0
        if risk_pct <= 9.0:
            verdict = "SHORT"
            conf = max(conf, min(9, 6 + liq_cascade.strength // 2))
            action_priority = "short"
            reason = liq_cascade.note if not reason else f"{liq_cascade.note} · {reason}"
            invalidation_price = cascade_tight_stop
            if not breakdown:
                breakdown = current * 0.999

    if channel and channel.kind == "bear" and verdict == "LONG" and conf < 7 and range_trade is None:
        verdict = "WAIT"
        reason = (reason + " · канал ↓") if reason else "канал ↓ — ждать пробой"
    if repeat_spike_dump_risk and verdict == "LONG" and conf <= 8:
        verdict = "WAIT"
        action_priority = "short" if momentum == "down" else action_priority
        reason = (
            f"{reason} · высокий риск повторного слива после пампа"
            if reason
            else "высокий риск повторного слива после пампа"
        )
    # Защита от входа в LONG на перегретом пике после резкого пампа.
    if (
        verdict == "LONG"
        and post_pump
        and ms.range_position >= 0.90
        and momentum == "up"
        and ms.drawdown_from_high_pct <= 2.0
    ):
        verdict = "WAIT"
        action_priority = "short"
        conf = min(conf, 7)
        reason = (
            f"{reason} · перегретый пик после пампа, высокий риск коррекции"
            if reason
            else "перегретый пик после пампа, высокий риск коррекции"
        )

    # Полka после дампа / post-crash: нет пробоя → WAIT, не SHORT/LONG 8/10
    post_crash_range = (
        ms.phase == "post_crash_weak"
        or (
            consolidation is not None
            and ms.drawdown_from_high_pct >= 8.0
            and ms.phase in {"consolidation", "breakout_setup"}
        )
    )
    if post_crash_range and verdict in {"LONG", "SHORT"}:
        near_short = (
            breakdown is not None
            and current > 0
            and abs(current - breakdown) / current * 100.0 <= 0.45
        )
        near_long = (
            breakout is not None
            and current > 0
            and abs(current - breakout) / current * 100.0 <= 0.45
        )
        triggered = (
            (verdict == "SHORT" and near_short)
            or (verdict == "LONG" and near_long)
        )
        if not triggered:
            action_priority = (
                "short" if verdict == "SHORT" else "long" if verdict == "LONG" else action_priority
            )
            verdict = "WAIT"
            conf = min(conf, 6)
            reason = (
                f"{reason} · боковик после дампа — ждать пробой уровня"
                if reason
                else "боковик после дампа — ждать пробой уровня"
            )
        else:
            conf = min(conf, 7)

    if (
        range_trade is not None
        and verdict in {"LONG", "SHORT"}
        and not _range_trade_matches_verdict(verdict, range_trade)
    ):
        if verdict == "LONG" and range_trade.direction == "short":
            verdict = "WAIT"
            action_priority = "short"
            conf = min(conf, 7)
            reason = (
                f"{reason} · {range_trade.label} — не лонг у сопротивления"
                if reason
                else f"{range_trade.label} — ждать отказ или пробой"
            )
        elif verdict == "SHORT" and range_trade.direction == "long":
            verdict = "WAIT"
            action_priority = "long"
            conf = min(conf, 7)
            reason = (
                f"{reason} · {range_trade.label} — не шорт у поддержки"
                if reason
                else f"{range_trade.label} — ждать отскок или пробой"
            )

    trade_is_long = is_long
    if neutral:
        if verdict == "SHORT" or action_priority == "short":
            trade_is_long = False
        elif verdict == "LONG" or action_priority == "long":
            trade_is_long = True

    range_aligned = _range_trade_matches_verdict(verdict, range_trade)
    entry_mode = "range_edge" if range_aligned else "breakout"
    if liq_cascade.active and verdict == "SHORT":
        entry_mode = "cascade"
    if range_aligned and range_trade and verdict in {"LONG", "SHORT"}:
        inv = range_trade.stop_price
        entry = (range_trade.entry_price * 0.999, range_trade.entry_price * 1.001)
        targets = list(range_trade.targets)
    elif liq_cascade.active and verdict == "SHORT" and cascade_tight_stop is not None:
        inv = cascade_tight_stop
        entry = (current * 0.999, current * 1.001)
        sups = sorted(
            [lv.price for lv in levels if lv.kind == "support" and lv.price < current * 0.998],
            reverse=True,
        )
        targets = sups[:3] if sups else [current * 0.985, current * 0.97, current * 0.955]
    else:
        inv, entry, targets = _trade_levels(
            bars, levels, is_long=trade_is_long, invalidation=invalidation_price,
            bullish=bullish, bearish=bearish,
        )

    # Wave Lite + Fib: стоп/цели и мягкий bias (без текста в Hot)
    inv, targets, _wave_cont, _wave_corr = apply_wave_to_trade_plan(
        verdict=verdict,
        action_priority=action_priority,
        current=current,
        inv=inv,
        targets=targets,
        breakout=breakout,
        breakdown=breakdown,
        wave=wave,
    )
    if (
        wave.valid
        and wave.has_confluence
        and wave.entry_hint_price
        and wave.wave_phase in {"shallow_pullback", "wave_2_4_zone", "deep_pullback"}
        and not range_aligned
        and not (liq_cascade.active and verdict == "SHORT")
    ):
        hint = wave.entry_hint_price
        if action_priority == "long" and wave.wave_bias == "long" and hint < current * 1.002:
            entry = (hint * 0.998, hint * 1.004)
        elif action_priority == "short" and wave.wave_bias == "short" and hint > current * 0.998:
            entry = (hint * 0.996, hint * 1.002)

    if (
        wave.leg is not None
        and wave.wave_phase == "late_impulse"
        and verdict in {"LONG", "SHORT"}
    ):
        if (
            (verdict == "LONG" and wave.leg.direction == "up")
            or (verdict == "SHORT" and wave.leg.direction == "down")
        ):
            conf = min(conf, 7)
            if "вдогонку" not in (reason or ""):
                reason = f"{reason} · у края импульса — лучше от Fib/ретеста" if reason else (
                    "у края импульса — лучше от Fib/ретеста"
                )

    cascade_override_rr = liq_cascade.active and liq_cascade.strength >= 5 and verdict == "SHORT"
    is_bad_trade, bad_trade_reason = _trade_quality_guard(
        verdict=verdict,
        current=current,
        stop=inv,
        targets=targets,
    )
    if is_bad_trade and not cascade_override_rr:
        verdict = "WAIT"
        conf = min(conf, 7)
        reason = f"{reason} · {bad_trade_reason}" if reason else bad_trade_reason
    elif is_bad_trade and cascade_override_rr:
        reason = (
            f"{reason} · агр. short по каскаду, стоп у лок. хая {fmt_price(inv)}"
            if reason
            else f"агр. short по каскаду, стоп {fmt_price(inv)}"
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
    if repeat_spike_dump_note:
        risk_notes.append(repeat_spike_dump_note)
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

    flow = evaluate_market_flow(
        momentum=momentum,
        momentum_pct=momentum_pct,
        phase=ms.phase,
        oi_narrative=ms.oi_narrative,
        oi_context_strength=ms.oi_context_strength,
        cvd_ratio=factors.cvd_ratio,
        liq_long_boost=factors.liq_long_boost,
        liq_short_boost=factors.liq_short_boost,
        range_position=ms.range_position,
        post_pump=post_pump,
        drawdown_from_high_pct=ms.drawdown_from_high_pct,
    )
    w_cont, w_corr = wave_flow_adjustments(wave, action_priority=action_priority)
    if w_cont or w_corr:
        flow = MarketFlowScores(
            continuation=int(_clamp(flow.continuation + w_cont, 0, 100)),
            correction=int(_clamp(flow.correction + w_corr, 0, 100)),
            convergence=flow.convergence,
            notes=list(flow.notes),
        )
    if verdict == "WAIT" and action_priority == "neutral":
        if flow.continuation >= flow.correction + 18 and momentum in {"up", "flat"}:
            action_priority = "long"
        elif flow.correction >= flow.continuation + 18:
            action_priority = "short"
    for note in flow.notes:
        if note not in risk_notes and note not in (reason or ""):
            risk_notes.append(note)
    risk_notes = risk_notes[:7]

    verdict, conf, reason, action_priority = _apply_post_pump_trigger_wait_guard(
        verdict=verdict,
        conf=conf,
        reason=reason,
        action_priority=action_priority,
        post_pump=post_pump,
        current=current,
        breakout=breakout,
        breakdown=breakdown,
        flow_correction=flow.correction,
        flow_continuation=flow.continuation,
        momentum=momentum,
        drawdown_from_high_pct=ms.drawdown_from_high_pct,
    )

    correction_path, continuation_path, forecast_summary = build_market_forecast_paths(
        current=current,
        post_pump=post_pump,
        phase=ms.phase,
        range_position=ms.range_position,
        drawdown_from_high_pct=ms.drawdown_from_high_pct,
        momentum=momentum,
        momentum_pct=momentum_pct,
        market_bias=market_bias,
        oi_narrative=ms.oi_narrative,
        oi_context_strength=ms.oi_context_strength,
        cvd_ratio=factors.cvd_ratio,
        liq_long_boost=factors.liq_long_boost,
        liq_short_boost=factors.liq_short_boost,
        factor_lines=factors.factor_lines,
        consolidation=consolidation,
        smc=smc,
        breakout=breakout,
        breakdown=breakdown,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        target_prices=targets,
        repeat_spike_dump_risk=repeat_spike_dump_risk,
        verdict=verdict,
        rulers=rulers,
        flow=flow,
    )

    narrative_plain, narrative_plan, narrative_basis = build_ta_signal_narrative(
        verdict=verdict,
        current=current,
        target_prices=targets,
        invalidation_price=inv,
        breakout_level=breakout or (consolidation.top if consolidation else None) or nearest_resistance,
        breakdown_level=breakdown or (consolidation.bottom if consolidation else None) or nearest_support,
        bullish=bullish,
        bearish=bearish,
        flow=flow,
        correction_path=correction_path,
        continuation_path=continuation_path,
        factor_lines=factors.factor_lines,
        phase_label=ms.phase_label,
        structure_label=structure,
        momentum_label=momentum_label,
        oi_narrative_label=ms.oi_narrative_label,
        market_bias=market_bias,
        range_trade_label=range_trade.label if range_aligned else "",
        primary_scenario=primary_scenario,
        verdict_reason=reason,
    )

    _ew_conf = int(getattr(wave, "elliott_confidence", 0) or 0)
    _ew_global_pts = _filter_elliott_draw_points(
        list(getattr(wave, "elliott_global_draw_points", None) or []),
        bars=bars,
        phase=ms.phase,
        consolidation=consolidation is not None,
        confidence=_ew_conf,
    )
    _ew_local_pts = _filter_elliott_draw_points(
        list(getattr(wave, "elliott_local_draw_points", None) or []),
        bars=bars,
        phase=ms.phase,
        consolidation=consolidation is not None,
        confidence=_ew_conf,
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
        range_trade_label=range_trade.label if range_aligned else "",
        range_trade_direction=range_trade.direction if range_trade else "",
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
        repeat_spike_dump_risk=repeat_spike_dump_risk,
        repeat_spike_dump_note=repeat_spike_dump_note,
        correction_path=correction_path,
        continuation_path=continuation_path,
        forecast_summary=forecast_summary,
        flow_continuation=flow.continuation,
        flow_correction=flow.correction,
        flow_notes=list(flow.notes),
        narrative_plain=narrative_plain,
        narrative_plan=narrative_plan,
        narrative_basis=narrative_basis,
        liq_cascade_active=liq_cascade.active and verdict == "SHORT",
        liq_cascade_note=liq_cascade.note if liq_cascade.active else "",
        cvd_source=(
            str(getattr(taker_cvd, "source", "proxy") or "proxy")
            if taker_cvd is not None
            and getattr(taker_cvd, "trade_count", 0) > 0
            else "proxy"
        ),
        cvd_delta=(
            float(getattr(taker_cvd, "delta", 0) or 0)
            if taker_cvd is not None
            and getattr(taker_cvd, "trade_count", 0) > 0
            else None
        ),
        fib_levels=list(wave.chart_fib_levels),
        wave_phase=wave.wave_phase if wave.leg else "",
        wave_bias=wave.wave_bias or "neutral",
        wave_confidence=wave.confidence if (wave.leg or wave.elliott_entry_ready) else 0,
        wave_leg_start=wave.leg.start_price if wave.leg else None,
        wave_leg_end=wave.leg.end_price if wave.leg else None,
        wave_has_confluence=bool(wave.has_confluence) if wave.leg else False,
        wave_confluence_count=int(wave.confluence_count) if wave.leg else 0,
        wave_confluence_sr=bool(wave.confluence_sr) if wave.leg else False,
        wave_confluence_round=bool(wave.confluence_round) if wave.leg else False,
        wave_confluence_retest=bool(wave.confluence_retest) if wave.leg else False,
        elliott_label=wave.elliott_label or "",
        abc_phase=wave.abc_phase or "",
        abc_label_ru=wave.abc_label_ru or "",
        elliott_phase=getattr(wave, "elliott_phase", "") or "",
        elliott_confidence=int(getattr(wave, "elliott_confidence", 0) or 0),
        elliott_entry_mode=getattr(wave, "elliott_entry_mode", "") or "",
        elliott_entry_ready=bool(getattr(wave, "elliott_entry_ready", False)),
        elliott_entry_price=getattr(wave, "elliott_entry_price", None),
        elliott_stop_price=getattr(wave, "elliott_stop_price", None),
        elliott_tp_prices=list(getattr(wave, "elliott_tp_prices", None) or []),
        elliott_draw_points=(
            list(_ew_global_pts) + list(_ew_local_pts)
            if (_ew_global_pts or _ew_local_pts)
            else _filter_elliott_draw_points(
                list(getattr(wave, "elliott_draw_points", None) or []),
                bars=bars,
                phase=ms.phase,
                consolidation=consolidation is not None,
                confidence=int(getattr(wave, "elliott_confidence", 0) or 0),
            )
        ),
        elliott_fib_classic_ok=bool(getattr(wave, "elliott_fib_classic_ok", False)),
        elliott_fib_w2=float(getattr(wave, "elliott_fib_w2", 0) or 0),
        elliott_fib_w4=float(getattr(wave, "elliott_fib_w4", 0) or 0),
        elliott_extension=str(getattr(wave, "elliott_extension", "") or ""),
        elliott_truncated=bool(getattr(wave, "elliott_truncated", False)),
        elliott_diagonal=str(getattr(wave, "elliott_diagonal", "") or ""),
        elliott_corr_type=str(getattr(wave, "elliott_corr_type", "") or ""),
        elliott_structure_note=str(getattr(wave, "elliott_structure_note", "") or ""),
        elliott_triangle_kind=str(getattr(wave, "elliott_triangle_kind", "") or ""),
        elliott_triangle_bias=str(getattr(wave, "elliott_triangle_bias", "") or ""),
        elliott_complex_kind=str(getattr(wave, "elliott_complex_kind", "") or ""),
        elliott_fib_targets=list(getattr(wave, "elliott_fib_targets", None) or []),
        elliott_fib_target_labels=list(getattr(wave, "elliott_fib_target_labels", None) or []),
        elliott_path_bias=str(getattr(wave, "elliott_path_bias", "") or ""),
        elliott_path_prices=list(getattr(wave, "elliott_path_prices", None) or []),
        elliott_path_labels=list(getattr(wave, "elliott_path_labels", None) or []),
        elliott_path_reason=str(getattr(wave, "elliott_path_reason", "") or ""),
        elliott_triangle_obj=getattr(wave, "elliott_triangle_obj", None),
        elliott_global_draw_points=_ew_global_pts,
        elliott_local_draw_points=_ew_local_pts,
        elliott_global_label=str(getattr(wave, "elliott_global_label", "") or ""),
        elliott_local_label=str(getattr(wave, "elliott_local_label", "") or ""),
        fib_status=getattr(wave, "fib_status", "") or "",
        fib_reject_reason=getattr(wave, "fib_reject_reason", "") or "",
        chart_patterns=chart_patterns,
        primary_chart_pattern=primary_chart_pattern,
        setup_score=int(setup.score),
        setup_grade=setup.grade,
        setup_side=setup.side,
        setup_label_ru=setup.label_ru,
        setup_factors=list(setup.factors[:6]),
        setup_ideal_ready=bool(setup.ideal_ready),
        setup_entry=setup.entry_price,
        setup_stop=setup.stop_price,
        setup_tps=list(setup.tp_prices[:3]),
        setup_trigger=setup.trigger,
        htf_elliott_label=setup.htf_label_ru,
        htf_elliott_phase=setup.htf_phase,
        htf_elliott_bias=setup.htf_bias,
        is_ending_diagonal=bool(setup.is_ending_diagonal)
        or str(getattr(wave, "elliott_diagonal", "") or "") == "ending",
        is_abcde=bool(setup.is_abcde)
        or str(getattr(wave, "elliott_corr_type", "") or "") == "triangle"
        or bool(getattr(wave, "elliott_triangle_kind", "")),
        forecast_path_prices=[wp.price for wp in setup.forecast_path]
        or list(getattr(wave, "elliott_path_prices", None) or []),
        forecast_path_labels=[wp.label for wp in setup.forecast_path]
        or list(getattr(wave, "elliott_path_labels", None) or []),
        htf_elliott_draw_points=list(setup.htf_draw_points),
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
    if ta.primary_chart_pattern:
        parts.append(format_chart_pattern_compact(ta.primary_chart_pattern))
    if getattr(ta, "setup_grade", "") and ta.setup_score:
        parts.append(f"setup {ta.setup_grade} {ta.setup_score}")
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
    """Какой сценарий рисовать на графике: long / short / neutral.

    WAIT → всегда neutral (без стрелок «и вверх и вниз»).
    """
    if ta.verdict == "WAIT":
        return "neutral"
    if ta.verdict == "LONG":
        return "long"
    if ta.verdict == "SHORT":
        return "short"
    return "neutral"


def _filter_elliott_draw_points(
    points: list,
    *,
    bars: list[KlineBar],
    phase: str,
    consolidation: bool,
    confidence: int,
) -> list:
    """Не рисовать микро 1–5 внутри боковика — шум.
    Крупный импульс/дамп и локальный слой (i–v) сохраняем мягче.
    """
    if not points:
        return []
    prices = [float(getattr(p, "price", 0) or 0) for p in points]
    prices = [p for p in prices if p > 0]
    if len(prices) < 2:
        return []
    mid = (max(prices) + min(prices)) / 2.0
    span_pct = (max(prices) - min(prices)) / mid * 100.0 if mid > 0 else 0.0
    local_labs = {"·0", "i", "ii", "iii", "iv", "v", "a", "b", "c", "d", "e", "w", "x", "y", "z", "x2"}
    is_local = any(str(getattr(p, "label", "")) in local_labs for p in points)
    # Крупная структура (DEXE −70% и т.п.) — всегда рисуем
    if span_pct >= 12.0:
        return list(points)
    # Локальный слой: достаточно 1.8% размаха
    if is_local and span_pct >= 1.8 and confidence >= 4:
        return list(points)
    noisy_phase = phase in {"consolidation", "breakout_setup", "post_crash_weak"} or consolidation
    if noisy_phase and (span_pct < 4.5 or confidence < 6):
        return []
    if span_pct < 2.2 and confidence < 7:
        return []
    return list(points)


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
    if ta.verdict == "SHORT":
        if ta.entry_zone:
            lo, hi = ta.entry_zone
            return (
                f"🚨 <b>Прямо сейчас:</b> не шортить по рынку после сильного падения. "
                f"Ждать откат в зону <b>{fmt_price(lo)}–{fmt_price(hi)}</b> и слабую 5m свечу вниз."
            )
        return (
            "🚨 <b>Прямо сейчас:</b> не шортить по рынку после импульса. "
            "Ждать локальный откат и подтверждение вниз."
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
        if ta.entry_zone:
            lo, hi = ta.entry_zone
            return (
                f"🧩 <b>План просто:</b> short по тренду, но только от отката "
                f"в <b>{fmt_price(lo)}–{fmt_price(hi)}</b> и после разворота вниз на 5m."
            )
        return "🧩 <b>План просто:</b> short по тренду, но только после отката и новой медвежьей 5m свечи."
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


def _manual_concrete_block(ta: TAAnalysisResult) -> list[str]:
    """Жестко-практичный блок для ручного чата: вход / отмена / цель."""
    lines: list[str] = []
    price = ta.current_price if ta.current_price > 0 else 0.0
    tp1 = ta.target_prices[0] if ta.target_prices else None
    stop = ta.invalidation_price

    if ta.verdict == "LONG":
        if ta.breakout_level:
            entry_line = f"вход после 5m close выше <b>{fmt_price(ta.breakout_level)}</b>"
        elif ta.entry_zone:
            lo, hi = ta.entry_zone
            entry_line = f"вход от отката в <b>{fmt_price(lo)}–{fmt_price(hi)}</b> + бычья 5m свеча"
        else:
            entry_line = "вход только после нового подтверждения вверх"

        bad_rr = False
        if price > 0 and stop and tp1:
            risk_pct = abs(price - stop) / price * 100.0
            reward_pct = abs(tp1 - price) / price * 100.0
            if risk_pct > 6.0 and reward_pct < 3.0:
                bad_rr = True
                lines.append(
                    f"⛔ <b>СЕЙЧАС:</b> вход невыгоден (риск ~{risk_pct:.1f}% к TP1 ~{reward_pct:.1f}%). "
                    "Лучше пропуск."
                )
        if not bad_rr:
            lines.append(f"🎯 <b>СЕЙЧАС:</b> {entry_line}.")
        if stop:
            lines.append(f"🛑 <b>ОТМЕНА сценария:</b> ниже <b>{fmt_price(stop)}</b>.")
        if tp1:
            lines.append(f"✅ <b>БЛИЖНЯЯ ЦЕЛЬ:</b> <b>{fmt_price(tp1)}</b>.")
        return lines

    if ta.verdict == "SHORT":
        if ta.breakdown_level:
            entry_line = f"вход после 5m close ниже <b>{fmt_price(ta.breakdown_level)}</b>"
        elif ta.entry_zone:
            lo, hi = ta.entry_zone
            entry_line = f"вход от отката в <b>{fmt_price(lo)}–{fmt_price(hi)}</b> + медвежья 5m свеча"
        else:
            entry_line = "вход только после нового подтверждения вниз"
        lines.append(f"🎯 <b>СЕЙЧАС:</b> {entry_line}.")
        if stop:
            lines.append(f"🛑 <b>ОТМЕНА сценария:</b> выше <b>{fmt_price(stop)}</b>.")
        if tp1:
            lines.append(f"✅ <b>БЛИЖНЯЯ ЦЕЛЬ:</b> <b>{fmt_price(tp1)}</b>.")
        return lines

    long_lvl = fmt_price(ta.breakout_level) if ta.breakout_level else "уровня long"
    short_lvl = fmt_price(ta.breakdown_level) if ta.breakdown_level else "уровня short"
    lines.append(
        f"🎯 <b>СЕЙЧАС:</b> вне сделки; ждать 5m close выше <b>{long_lvl}</b> или ниже <b>{short_lvl}</b>."
    )
    return lines


def _manual_decision_block(ta: TAAnalysisResult) -> list[str]:
    """Понятный блок решений без перегруза терминами."""
    lines: list[str] = []
    scenario = ta.primary_scenario or ("рост" if ta.verdict == "LONG" else "снижение" if ta.verdict == "SHORT" else "боковик/ожидание")
    lines.append(f"🧭 <b>Вероятный сценарий 30–60м:</b> {scenario}.")

    short_pressure = (
        ta.action_priority == "short"
        or any("давление вниз" in s for s in ta.factor_lines)
        or ta.repeat_spike_dump_risk
    )
    if (short_pressure and ta.momentum_pct <= -1.2) or (ta.post_pump and ta.range_position >= 0.88):
        risk = "высокий"
    elif short_pressure:
        risk = "средний"
    else:
        risk = "низкий"
    why: list[str] = []
    if ta.repeat_spike_dump_risk:
        why.append("паттерн spike→dump")
    if ta.post_pump and ta.range_position >= 0.88:
        why.append("перегрев у верха range")
    if any("CVD↓" in s for s in ta.factor_lines):
        why.append("CVD вниз")
    if any("лонги ликвидированы" in s for s in ta.factor_lines):
        why.append("смыв лонгов")
    if ta.action_priority == "short":
        why.append("приоритет short")
    suffix = f" ({', '.join(why[:3])})" if why else ""
    lines.append(f"📉 <b>Риск смыва лонгов:</b> {risk}{suffix}.")

    if ta.verdict == "SHORT":
        act = "искать short только после подтверждения свечой/ретеста уровня"
        no_act = "не ловить long против импульса"
    elif ta.verdict == "LONG":
        act = "искать long только после подтверждения свечой и удержания уровня"
        no_act = "не входить в long на пике без отката"
    else:
        act = "быть вне сделки до подтверждения уровня"
        no_act = "не открывать позицию в середине движения"
    lines.append(f"✅ <b>Что делать:</b> {act}.")
    lines.append(f"⛔ <b>Что НЕ делать:</b> {no_act}.")
    return lines


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

    # Понятный Fib: входить / ждать зону / не строим
    try:
        from .trade_analyst import fib_action_line_html

        lines.append(fib_action_line_html(ta))
    except Exception:
        pass

    if ta.forecast_summary:
        lines.append(f"🔮 {ta.forecast_summary[:120]}")

    return "\n".join(lines)


def ta_manual_compact_html(ta: TAAnalysisResult) -> str:
    """Ручной TA: только суть, без дублей."""
    return ta_action_summary_html(ta)


def _level_dist_pct(current: float, level: float | None) -> float | None:
    if not level or current <= 0:
        return None
    return abs(level - current) / current * 100.0


def _effective_breakout_breakdown(ta: TAAnalysisResult) -> tuple[float | None, float | None]:
    """Рабочие уровни: триггеры TA или границы range / ближайшие swing."""
    bo = ta.breakout_level
    bd = ta.breakdown_level
    if ta.consolidation:
        if bo is None:
            bo = ta.consolidation.top
        if bd is None:
            bd = ta.consolidation.bottom
    if bo is None:
        bo = ta.nearest_resistance
    if bd is None:
        bd = ta.nearest_support
    return bo, bd


def _is_late_dump_context(ta: TAAnalysisResult) -> bool:
    """Цена уже сильно упала — шорт «в хвост» дампа опасен по R:R."""
    if ta.drawdown_from_high_pct >= 12.0:
        return True
    if ta.phase in {"impulse_down", "post_crash_weak"}:
        return True
    if ta.momentum_pct <= -1.2 and "вниз" in (ta.momentum_label or "").lower():
        return True
    return False


def _intent_cancel_level(ta: TAAnalysisResult, user_side: str) -> float | None:
    """Уровень отмены идеи: для SHORT — выше (слом сценария), для LONG — ниже."""
    bo, bd = _effective_breakout_breakdown(ta)
    side = user_side.lower()
    if side == "short":
        return bo or (ta.bullish_scenario.trigger_price if ta.bullish_scenario else None)
    if side == "long":
        return bd or (ta.bearish_scenario.trigger_price if ta.bearish_scenario else None)
    return _display_invalidation(ta)


def _display_invalidation(ta: TAAnalysisResult) -> float | None:
    """Стоп/отмена для общего плана — не уводить на 50% от цены."""
    inv = ta.invalidation_price
    px = ta.current_price
    if inv and px and px > 0:
        if abs(inv - px) / px > 0.12:
            bo, bd = _effective_breakout_breakdown(ta)
            if ta.verdict == "SHORT" or ta.action_priority == "short":
                return bo or inv
            if ta.verdict == "LONG" or ta.action_priority == "long":
                return bd or inv
            return bo if ta.action_priority == "long" else bd or inv
    return inv


def _resolve_intent_breakout(
    ta: TAAnalysisResult,
    sticky_breakout: float | None = None,
) -> float | None:
    """Рабочий LONG-триггер: sticky с прошлого разбора, если ещё актуален."""
    bo, _ = _effective_breakout_breakdown(ta)
    px = ta.current_price or 0.0
    sticky = float(sticky_breakout) if sticky_breakout else None
    if sticky and sticky > 0 and px > 0:
        # Липкий уровень: не поднимаем триггер выше без новой структуры.
        # Если цена уже взяла sticky — оставляем его (ниже), чтобы сказать «уровень взят».
        # Если ещё ниже sticky — держим тот же план, даже если fresh TA чуть сдвинул уровень.
        if sticky <= px * 1.001 or (bo is None) or sticky <= (bo or sticky) * 1.012:
            return sticky
    return bo


def _intent_entry_hint(
    ta: TAAnalysisResult,
    user_side: str,
    *,
    sticky_breakout: float | None = None,
) -> str:
    """Человеческая подсказка «когда входить» для ручного TA."""
    side = user_side.lower()
    bo = _resolve_intent_breakout(ta, sticky_breakout)
    _, bd = _effective_breakout_breakdown(ta)
    px = ta.current_price or 0.0

    if side == "short":
        if ta.liq_cascade_active and ta.invalidation_price:
            return (
                f"агр. short по каскаду · стоп выше <b>{fmt_price(ta.invalidation_price)}</b> "
                f"(локальный хай)"
            )
        if bd and px > bd:
            d = (px - bd) / px * 100.0
            return f"закрепление 5m ≤<b>{fmt_price(bd)}</b> (ещё ~{d:.1f}%)"
        if _is_late_dump_context(ta) and bo and px < bo * 0.995:
            d = (bo - px) / px * 100.0
            return (
                f"<b>не шортить сейчас</b> — дамп уже прошёл (−{ta.drawdown_from_high_pct:.0f}% от хая). "
                f"Ждать отскок к <b>{fmt_price(bo)}</b> (ещё ~{d:.1f}%), потом short при развороте вниз"
            )
        if ta.correction_path and ta.flow_correction >= ta.flow_continuation:
            tgt = ta.correction_path.waypoints[-1] if ta.correction_path.waypoints else None
            if tgt and tgt < px:
                return f"откат к <b>{fmt_price(tgt)}</b>, затем пробой вниз"
        if ta.post_pump and bo and bo > px:
            return (
                f"после пампа: сначала откат/дожим к <b>{fmt_price(bo)}</b>, "
                f"потом short при сломе поддержки"
            )
        return "пробой поддержки range на 5m — без уровня не входить"

    if side == "long":
        if bo and px >= bo * 0.999:
            above_pct = (px - bo) / px * 100.0 if px > 0 else 0.0
            next_r = None
            fresh_bo, _ = _effective_breakout_breakdown(ta)
            if fresh_bo and fresh_bo > px * 1.004:
                next_r = fresh_bo
            elif ta.nearest_resistance and ta.nearest_resistance > px * 1.004:
                next_r = ta.nearest_resistance
            bits = [
                f"<b>уровень ≥{fmt_price(bo)} уже взят</b> "
                f"(цена выше на ~{above_pct:.1f}%)"
            ]
            if ta.post_pump:
                bits.append("после пампа лучше не лонг вдогонку")
                if next_r:
                    bits.append(
                        f"либо ретест <b>{fmt_price(bo)}</b> как поддержки, "
                        f"либо дожим к <b>{fmt_price(next_r)}</b> с объёмом"
                    )
                else:
                    bits.append(
                        f"ждать ретест <b>{fmt_price(bo)}</b> или отказ от хая"
                    )
            elif next_r:
                bits.append(f"следующий ориентир <b>{fmt_price(next_r)}</b>")
            else:
                bits.append("можно рассмотреть long от ретеста, не в рынок «вдогонку»")
            return " · ".join(bits)
        if bo and px < bo:
            d = (bo - px) / px * 100.0
            return f"закрепление 5m ≥<b>{fmt_price(bo)}</b> (ещё ~{d:.1f}%)"
        if ta.post_pump:
            return "после пампа LONG рискован — только при сильном пробое с объёмом"
        return "пробой сопротивления range на 5m"

    if bo and bd:
        return f"выше <b>{fmt_price(bo)}</b> (long) или ниже <b>{fmt_price(bd)}</b> (short)"
    return "сначала дождаться формирования range и уровней"


def _intent_plain_line(
    ta: TAAnalysisResult,
    user_side: str,
    *,
    sticky_breakout: float | None = None,
) -> str:
    """Короткий разбор под идею пользователя — без противоречия с LONG/SHORT."""
    side = user_side.lower()
    bo = _resolve_intent_breakout(ta, sticky_breakout)
    _, bd = _effective_breakout_breakdown(ta)
    px = ta.current_price or 0.0
    corr_wins = ta.flow_correction > ta.flow_continuation + 8
    cont_wins = ta.flow_continuation > ta.flow_correction + 8
    level_taken = bool(bo and px >= bo * 0.999)

    if side == "short":
        if ta.liq_cascade_active:
            return (
                f"📐 <b>Каскад ликвидаций</b> — short по импульсу вниз. "
                f"Стоп выше <b>{fmt_price(ta.invalidation_price)}</b>. "
                "Не держать без стопа — возможен отскок."
            )
        if _is_late_dump_context(ta) and not bd:
            bo_eff, _ = bo, bd
            bits = [
                f"тренд <b>вниз</b> (уже −{ta.drawdown_from_high_pct:.0f}% от хая)",
                "шортить <b>сейчас поздно</b> — стоп далеко, цель близко",
            ]
            if bo_eff and ta.current_price and bo_eff > ta.current_price:
                bits.append(f"лучше short от отскока к <b>{fmt_price(bo_eff)}</b>")
            return "📐 " + ". ".join(bits) + "."
        if ta.post_pump and corr_wins:
            bits = ["после пампа базовый сценарий — <b>откат</b>"]
            if ta.correction_path and ta.correction_path.waypoints:
                bits.append(f"цель отката ~<b>{fmt_price(ta.correction_path.waypoints[-1])}</b>")
            if bd:
                bits.append(f"short по плану — только после ≤<b>{fmt_price(bd)}</b>")
            bits.append("сейчас импульс вверх — <b>не шортить в лоб</b>")
            return "📐 " + ". ".join(bits) + "."
        if cont_wins and bo:
            return (
                f"📐 SHORT против текущего потока: цена тянет к <b>{fmt_price(bo)}</b>. "
                f"Ждать отклонение от сопротивления или слом вниз."
            )
        return ta_plain_forecast_line(ta) or "📐 WAIT — подтвердите уровень перед short."

    if side == "long":
        if ta.post_pump and level_taken:
            return (
                f"📐 Триггер ≥<b>{fmt_price(bo)}</b> <b>уже пройден</b> — импульс есть, "
                "но после пампа вход вдогонку слабый. "
                f"Проф. вариант: long от ретеста <b>{fmt_price(bo)}</b> "
                "или ждать следующий resistance; рынок «сейчас» = высокий риск."
            )
        if ta.post_pump:
            return (
                "📐 LONG после пампа — риск покупки на хайе. "
                f"Имеет смысл только при пробое ≥<b>{fmt_price(bo)}</b> с объёмом."
                if bo
                else "📐 LONG после пампа — высокий риск, ждите откат."
            )
        if level_taken:
            return (
                f"📐 Уровень ≥<b>{fmt_price(bo)}</b> взят — "
                "оценка: long возможен от ретеста, а не по рынку вдогонку."
            )
        return ta_plain_forecast_line(ta) or "📐 Ждём подтверждения для long."

    return ta_plain_forecast_line(ta)


def _manual_smc_brief_html(smc: SmcContext) -> str:
    """1–2 строки SMC для ручного разбора — без полного чеклиста."""
    if not smc:
        return ""
    ready = smc.reversal_ready or (
        smc.structure_break
        and smc.discount_retrace
        and (smc.structure_expansion or smc.liquidity_sweep)
    )
    hits: list[str] = []
    if smc.structure_break:
        hits.append("BOS")
    if smc.liquidity_sweep:
        hits.append("sweep")
    if smc.structure_expansion:
        hits.append("expansion")
    if smc.fvgs:
        hits.append("FVG")
    if smc.discount_retrace:
        hits.append("зона дисконта/премии")
    status = "готов к развороту" if ready else "не готов — ждём sweep/expansion"
    if smc.summary:
        return f"🧠 <b>SMC:</b> {smc.summary} · <i>{status}</i>"
    if hits:
        return f"🧠 <b>SMC:</b> есть {', '.join(hits)} · <i>{status}</i>"
    return f"🧠 <b>SMC:</b> сигналов разворота нет · <i>{status}</i>"


def ta_signal_scenario_line_html(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
    signal_type: str | None = None,
) -> str:
    """Одна строка сценария для обычных сигналов."""
    sig = (signal_side or "").lower()

    if ta.verdict == "LONG":
        state, _ = _long_trigger_state(ta)
        near_ready, _, _ = _near_trigger_ready(ta, "long", signal_type=signal_type)
        armed_aggressive_pump = state == "armed" and _is_aggressive_pump_signal(signal_type)
        if state == "ready" or armed_aggressive_pump or near_ready:
            head = "▶️ <b>Открывать LONG</b>"
        else:
            head = "▶️ <b>LONG по плану</b> · <b>не сейчас</b>"
        if ta.entry_mode == "range_edge" and ta.range_trade_label and not ta_range_trade_opposes_verdict(ta):
            head = f"▶️ <b>LONG</b> · {ta.range_trade_label}" + (
                "" if state == "ready" or armed_aggressive_pump or near_ready else " · не сейчас"
            )
        parts = [head]
        if ta.breakout_level:
            parts.append(f"вход ≥<b>{fmt_price(ta.breakout_level)}</b>")
        if ta.invalidation_price:
            parts.append(f"стоп <b>{fmt_price(ta.invalidation_price)}</b>")
        if ta.target_prices:
            parts.append(f"TP <b>{fmt_price(ta.target_prices[0])}</b>")
        return " · ".join(parts)

    if ta.verdict == "SHORT":
        state, _ = _short_trigger_state(ta)
        near_ready, _, _ = _near_trigger_ready(ta, "short", signal_type=signal_type)
        armed_aggressive_dump = state == "armed" and _is_aggressive_dump_signal(signal_type)
        if state == "ready" or armed_aggressive_dump or near_ready:
            head = "▶️ <b>Открывать SHORT</b>"
        else:
            head = "▶️ <b>SHORT по плану</b> · <b>не сейчас</b>"
        if ta.entry_mode == "range_edge" and ta.range_trade_label and not ta_range_trade_opposes_verdict(ta):
            head = f"▶️ <b>SHORT</b> · {ta.range_trade_label}" + (
                "" if state == "ready" or armed_aggressive_dump or near_ready else " · не сейчас"
            )
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
            if is_short:
                line = (
                    f"▶️ <b>Шорт</b> (ставка на падение) · <b>не сейчас</b> · "
                    f"входить только если цена ≤<b>{fmt_price(trigger)}</b>"
                )
            else:
                line = (
                    f"▶️ <b>Лонг</b> (ставка на рост) · <b>не сейчас</b> · "
                    f"входить только если цена ≥<b>{fmt_price(trigger)}</b>"
                )
            if is_short and ta.action_priority == "long" and ta.breakout_level:
                line += (
                    f" · <i>сейчас ближе идея роста ≥{fmt_price(ta.breakout_level)}</i>"
                )
            elif not is_short and ta.action_priority == "short" and ta.breakdown_level:
                bd_dist = _level_dist_pct(ta.current_price, ta.breakdown_level)
                if bd_dist is not None and bd_dist <= 6.0:
                    line += (
                        f" · <i>или шорт ≤{fmt_price(ta.breakdown_level)}</i>"
                    )
            return line

    if ta.action_priority == "long" and ta.breakout_level:
        return (
            f"▶️ <b>Не входить сейчас</b> · лонг только если цена ≥"
            f"<b>{fmt_price(ta.breakout_level)}</b>"
        )
    if ta.action_priority == "short" and ta.breakdown_level:
        bd_dist = _level_dist_pct(ta.current_price, ta.breakdown_level)
        if sig == "long" and ta.post_pump and bd_dist is not None and bd_dist > 6.0:
            if ta.breakout_level:
                return (
                    f"▶️ <b>Не входить сейчас</b> · после пампа лонг только при ≥"
                    f"<b>{fmt_price(ta.breakout_level)}</b>"
                )
            return "▶️ <b>Не входить сейчас</b> · после пампа, не покупать на хае"
        return (
            f"▶️ <b>Не входить сейчас</b> · шорт только если цена ≤"
            f"<b>{fmt_price(ta.breakdown_level)}</b>"
        )

    triggers: list[str] = []
    if ta.breakout_level:
        triggers.append(f"лонг ≥<b>{fmt_price(ta.breakout_level)}</b>")
    if ta.breakdown_level:
        bd_dist = _level_dist_pct(ta.current_price, ta.breakdown_level)
        if bd_dist is None or bd_dist <= 8.0:
            triggers.append(f"шорт ≤<b>{fmt_price(ta.breakdown_level)}</b>")
    if triggers:
        return f"▶️ <b>Не входить</b> · ждать {' или '.join(triggers)}"
    return "▶️ <b>Не входить</b> · дождаться, пока цена пробьёт уровень"


def ta_conflicts_with_signal(ta: TAAnalysisResult, signal_side: str | None) -> bool:
    sig = (signal_side or "").lower()
    if not sig or ta.verdict == "WAIT":
        return False
    return (sig == "long" and ta.verdict == "SHORT") or (sig == "short" and ta.verdict == "LONG")


def ta_scanner_conflict_line_html(ta: TAAnalysisResult, signal_side: str | None) -> str:
    """Когда сканер (импульс/разворот) не совпадает с вердиктом TA."""
    if not ta_conflicts_with_signal(ta, signal_side):
        return ""
    sig = (signal_side or "").lower()
    score = ta_display_score(ta)
    if sig == "short" and ta.verdict == "LONG":
        lvl = f" при ≥<b>{fmt_price(ta.breakout_level)}</b>" if ta.breakout_level else ""
        return (
            "⚠️ <b>Сканер поймал краткий откат</b> — это <b>не сигнал SHORT</b>.\n"
            f"📐 TA <b>LONG {score}/10</b>: работать только по пробою{lvl}."
        )
    if sig == "long" and ta.verdict == "SHORT":
        lvl = f" при ≤<b>{fmt_price(ta.breakdown_level)}</b>" if ta.breakdown_level else ""
        return (
            "⚠️ <b>Сканер поймал краткий отскок</b> — это <b>не сигнал LONG</b>.\n"
            f"📐 TA <b>SHORT {score}/10</b>: работать только по пробою{lvl}."
        )
    return ""


def _short_trigger_state(ta: TAAnalysisResult) -> tuple[str, str]:
    """
    Состояние триггера SHORT: ready / armed / far / past.
    Вход ≤breakdown — готов только когда цена у уровня или ниже (с допуском).
    """
    if not ta.breakdown_level or ta.current_price <= 0:
        return "far", "нет уровня SHORT"
    bd = ta.breakdown_level
    px = ta.current_price
    if px <= bd * 1.0015:
        if px >= bd * 0.992:
            return "ready", "цена у триггера SHORT"
        return "past", "цена ниже входа — ждите ретест снизу"
    dist_pct = (px - bd) / px * 100.0
    if dist_pct <= 3.0:
        return "armed", f"ждать пробой ≤{fmt_price(bd)} (цена выше на {dist_pct:.1f}%)"
    return "far", f"SHORT далеко ({dist_pct:.1f}% до уровня)"


def _is_aggressive_dump_signal(signal_type: str | None) -> bool:
    """Сильный dump: допускаем шорт в armed, без ожидания точного тика пробоя."""
    return (signal_type or "").lower() in {
        "mega_dump", "vertical_dump", "liq_cascade_dump",
        "pulse_dump", "impulse_dump",
    }


def _is_aggressive_pump_signal(signal_type: str | None) -> bool:
    """Сильный pump: допускаем лонг в armed, без ожидания точного тика пробоя."""
    return (signal_type or "").lower() in {
        "mega_pump", "vertical_pump", "liq_cascade_pump",
        "pulse_pump", "impulse_pump", "short_squeeze",
    }


_EARLY_TRIGGER_SIGNAL_TYPES = frozenset({
    "pulse_pump", "pulse_dump", "impulse_pump", "impulse_dump",
    "mega_pump", "mega_dump", "vertical_pump", "vertical_dump",
    "short_squeeze", "liq_cascade_pump", "liq_cascade_dump",
    "reversal_pump", "reversal_dump", "trend_pump", "trend_dump",
})


def _near_entry_tolerance_pct(signal_type: str | None, *, momentum_pct: float = 0.0) -> float:
    """
    Умный допуск near-entry: 0.1%..0.8%.
    В сильном импульсе допускаем чуть более ранний вход, чтобы не пропускать движение.
    """
    st = (signal_type or "").lower()
    if st in {"mega_dump", "mega_pump", "vertical_dump", "vertical_pump"}:
        base = 0.75
    elif st in {"liq_cascade_dump", "liq_cascade_pump"}:
        base = 0.65
    elif st in {"impulse_dump", "impulse_pump", "trend_dump", "trend_pump"}:
        base = 0.55
    elif st in {"reversal_dump", "reversal_pump"}:
        base = 0.35
    else:
        base = 0.25
    impulse_bonus = min(abs(float(momentum_pct)) * 0.05, 0.15)
    return _clamp(base + impulse_bonus, 0.1, 0.8)


def _near_trigger_ready(
    ta: TAAnalysisResult,
    side: str,
    *,
    signal_type: str | None = None,
) -> tuple[bool, float | None, float]:
    """
    True, если цена в «умной» зоне входа рядом с триггером.
    Возвращает: (near, dist_pct, tolerance_pct).
    """
    if ta.current_price <= 0:
        return False, None, _near_entry_tolerance_pct(signal_type, momentum_pct=ta.momentum_pct)

    tol_pct = _near_entry_tolerance_pct(signal_type, momentum_pct=ta.momentum_pct)
    side_low = side.lower()
    if side_low == "short" and ta.breakdown_level:
        if ta.current_price <= ta.breakdown_level:
            return True, 0.0, tol_pct
        dist = (ta.current_price - ta.breakdown_level) / ta.current_price * 100.0
        return dist <= tol_pct, dist, tol_pct
    if side_low == "long" and ta.breakout_level:
        if ta.current_price >= ta.breakout_level:
            return True, 0.0, tol_pct
        dist = (ta.breakout_level - ta.current_price) / ta.current_price * 100.0
        return dist <= tol_pct, dist, tol_pct
    return False, None, tol_pct


def _long_trigger_state(ta: TAAnalysisResult) -> tuple[str, str]:
    """Состояние триггера LONG: ready / armed / far / past."""
    if not ta.breakout_level or ta.current_price <= 0:
        return "far", "нет уровня LONG"
    bo = ta.breakout_level
    px = ta.current_price
    if px >= bo * 0.9985:
        if px <= bo * 1.008:
            return "ready", "цена у триггера LONG"
        return "past", "цена выше входа — ждите ретест сверху"
    dist_pct = (bo - px) / px * 100.0
    if dist_pct <= 3.0:
        return "armed", f"ждать пробой ≥{fmt_price(bo)} (цена ниже на {dist_pct:.1f}%)"
    return "far", f"LONG далеко ({dist_pct:.1f}% до уровня)"


def ta_opposes_signal_direction(ta: TAAnalysisResult, signal_side: str | None) -> bool:
    """Сканер long/short против вердикта или приоритета TA."""
    sig = (signal_side or "").lower()
    if not sig:
        return False
    if ta_conflicts_with_signal(ta, signal_side):
        return True
    if ta.verdict != "WAIT":
        return False
    return (sig == "long" and ta.action_priority == "short") or (
        sig == "short" and ta.action_priority == "long"
    )


def should_skip_noise_signal(
    ta: TAAnalysisResult,
    signal_side: str,
    signal_score: float,
    *,
    signal_type: str | None = None,
    price_change_percent: float | None = None,
    cvd_ratio: float | None = None,
    cvd_short_max: float = 0.42,
    cvd_long_min: float = 0.58,
) -> tuple[bool, str]:
    """Отсечь слабые WAIT-сигналы, чтобы не забивать чат."""
    sig = signal_side.lower()
    score = ta_display_score(ta)
    timing = int(round(float(signal_score)))
    reason_low = (ta.verdict_reason or "").lower()

    st = (signal_type or "").lower()
    px_chg = float(price_change_percent or 0)
    aggressive_dump = (
        st in {"mega_dump", "impulse_dump", "liq_cascade_dump", "vertical_dump", "trend_dump", "pulse_dump"}
        and sig == "short" and px_chg <= -5.0
    )
    aggressive_pump = (
        st in {"mega_pump", "impulse_pump", "liq_cascade_pump", "vertical_pump", "trend_pump", "pulse_pump"}
        and sig == "long" and px_chg >= 5.0
    )

    if ("вход невыгоден" in reason_low or "плохой r:r" in reason_low) and not (
        aggressive_dump or aggressive_pump
    ):
        return True, "плохой R:R"

    if cvd_ratio is not None:
        if sig == "short" and cvd_ratio >= cvd_long_min:
            return True, f"CVD {cvd_ratio:.0%} buy против SHORT"
        if sig == "long" and cvd_ratio <= cvd_short_max:
            return True, f"CVD {cvd_ratio:.0%} buy против LONG"

    if ta.smc and ta.smc.liquidity_sweep:
        if sig == "short" and ta.smc.sweep_direction == "long":
            return True, "sweep лоев — ждать подтверждения SHORT"
        if sig == "long" and ta.smc.sweep_direction == "short":
            return True, "sweep хаев — ждать подтверждения LONG"

    if ta.verdict != "WAIT":
        return False, ""

    bo, bd = _effective_breakout_breakdown(ta)
    if bo is None and bd is None:
        return True, "WAIT без уровней"

    if ta_opposes_signal_direction(ta, signal_side):
        return True, "сканер vs приоритет TA"

    if ta_conflicts_with_signal(ta, signal_side) and score < 7:
        return True, "WAIT + сканер и TA в разные стороны"

    if timing <= 2 and score < 8:
        return True, "ранний сканер + слабый WAIT"

    trigger = bo if sig == "long" else bd
    if trigger and ta.current_price and ta.current_price > 0:
        dist = abs(trigger - ta.current_price) / ta.current_price * 100.0
        near_ready, _, _ = _near_trigger_ready(ta, sig, signal_type=signal_type)
        if near_ready:
            return False, ""
        is_reversal = signal_type in {"reversal_pump", "reversal_dump"}
        if dist > 5.5 and not is_reversal:
            return True, f"триггер далеко ({dist:.1f}%)"

    return False, ""


def ta_signal_compact_block(
    ta: TAAnalysisResult,
    *,
    signal_side: str,
    readiness: tuple[bool, str] | None = None,
    signal_type: str | None = None,
) -> str:
    """2 строки максимум: что делать по сигналу — без «LONG + WAIT + ждём подтверждения»."""
    sig = signal_side.lower()
    ready = bool(readiness and readiness[0])
    wait_reason = readiness[1] if readiness else ""
    bo, bd = _effective_breakout_breakdown(ta)
    label = "лонг" if sig == "long" else "шорт"
    label_cap = "Лонг" if sig == "long" else "Шорт"
    trig = bo if sig == "long" else bd
    op = "≥" if sig == "long" else "≤"

    if ta.range_trade_direction and (
        (sig == "long" and ta.range_trade_direction == "short")
        or (sig == "short" and ta.range_trade_direction == "long")
    ):
        alt = "шорт" if ta.range_trade_direction == "short" else "лонг"
        alt_trig = bd if ta.range_trade_direction == "short" else bo
        alt_op = "≤" if ta.range_trade_direction == "short" else "≥"
        bits = [f"⚠️ <b>Не по графику</b> · алерт на <b>{label}</b>"]
        if ta.range_trade_label:
            bits.append(ta.range_trade_label)
        if alt_trig:
            bits.append(f"ближе <b>{alt}</b> при {alt_op}<b>{fmt_price(alt_trig)}</b>")
        return " · ".join(bits)

    if ready:
        scenario = ta_signal_scenario_line_html(
            ta,
            signal_side=signal_side,
            signal_type=signal_type,
        )
        if scenario:
            return f"✅ <b>Готов</b>\n{scenario}"
        return "✅ <b>Готов к входу</b> по плану TA"

    if "плохой r:r" in wait_reason.lower() or "вход невыгоден" in (ta.verdict_reason or "").lower():
        if trig:
            return (
                f"🚫 <b>Пропуск</b> · соотношение риск/прибыль плохое · "
                f"смотреть {label} при {op}<b>{fmt_price(trig)}</b>"
            )
        return "🚫 <b>Пропуск</b> · соотношение риск/прибыль плохое"

    if ta_conflicts_with_signal(ta, signal_side):
        conflict = ta_scanner_conflict_line_html(ta, signal_side)
        if conflict:
            return conflict.replace("\n", " · ")

    if ta.verdict == "WAIT" and ta_opposes_signal_direction(ta, signal_side):
        pri = "шорт" if ta.action_priority == "short" else "лонг"
        pri_lvl = bd if ta.action_priority == "short" else bo
        pri_op = "≤" if ta.action_priority == "short" else "≥"
        if pri_lvl:
            return (
                f"🚫 <b>Не по алерту</b> · алерт на {label}, а график ближе к <b>{pri}</b> "
                f"при {pri_op}<b>{fmt_price(pri_lvl)}</b>"
            )
        return f"🚫 <b>Не по алерту</b> · {label} не совпадает с графиком"

    if ta.verdict in {"LONG", "SHORT"}:
        scenario = ta_signal_scenario_line_html(
            ta,
            signal_side=signal_side,
            signal_type=signal_type,
        )
        if scenario:
            return scenario
        return f"🔶 <b>{ta.verdict}</b> · {wait_reason}" if wait_reason else f"🔶 <b>{ta.verdict}</b> по плану"

    ctx_bits: list[str] = []
    if ta.post_pump and sig == "long":
        ctx_bits.append("после пампа")
    elif ta.consolidation:
        ctx_bits.append("пробой range")
    is_reversal = signal_type in {"reversal_pump", "reversal_dump"}
    if is_reversal:
        ctx_bits.append("разворот")

    if trig and ta.current_price and ta.current_price > 0:
        dist = abs(trig - ta.current_price) / ta.current_price * 100.0
        ctx = f" · {', '.join(ctx_bits)}" if ctx_bits else ""
        line1 = (
            f"🔶 <b>Ждать {label}</b> · цена должна быть {op}<b>{fmt_price(trig)}</b> "
            f"(ещё ~{dist:.1f}%){ctx}"
        )
    elif trig:
        ctx = f" · {', '.join(ctx_bits)}" if ctx_bits else ""
        line1 = f"🔶 <b>Ждать {label}</b> · цена {op}<b>{fmt_price(trig)}</b>{ctx}"
    else:
        line1 = "🔶 <b>Ждать</b> · уровень формируется"

    line2 = ""
    if ta.correction_path and ta.flow_correction > ta.flow_continuation + 8:
        wp = ta.correction_path.waypoints[-1] if ta.correction_path.waypoints else None
        if wp:
            line2 = f"<i>Базовый сценарий: откат к ~{fmt_price(wp)}</i>"
    elif ta.continuation_path and ta.flow_continuation > ta.flow_correction + 8:
        reason = (ta.continuation_path.reason or "").strip()
        if reason and "рост к" not in reason.lower():
            line2 = f"<i>{reason[:85]}</i>" if len(reason) <= 85 else f"<i>{reason[:82]}…</i>"

    return f"{line1}\n{line2}" if line2 else line1


def evaluate_entry_readiness(
    ta: TAAnalysisResult,
    signal_side: str,
    signal_score: int | float,
    *,
    min_ta_score: int = 7,
    max_trigger_dist_pct: float = 2.5,
    min_timing_score: int = 2,
    max_timing_score: int = 9,
    require_smc: bool = False,
    check_scanner_timing: bool = True,
    signal_type: str | None = None,
    accept_armed: bool = False,
    cvd_ratio: float | None = None,
    cvd_short_max: float = 0.42,
    cvd_long_min: float = 0.58,
) -> tuple[bool, str]:
    """Готов ли сигнал к входу: TA LONG/SHORT, триггер близко, без конфликтов."""
    if check_scanner_timing:
        timing = int(round(float(signal_score)))
        if timing < min_timing_score:
            return False, f"рано для входа ({timing}/10)"
        if timing > max_timing_score:
            return False, f"поздно ({timing}/10)"

    if "вход невыгоден" in (ta.verdict_reason or "").lower():
        return False, "плохой R:R"

    if ta_range_trade_opposes_verdict(ta):
        return False, "range-сетап против вердикта TA"

    sig = (signal_side or "").lower()
    if cvd_ratio is not None:
        if sig == "short" and cvd_ratio >= cvd_long_min:
            return False, f"CVD {cvd_ratio:.0%} buy — поток против SHORT"
        if sig == "long" and cvd_ratio <= cvd_short_max:
            return False, f"CVD {cvd_ratio:.0%} buy — поток против LONG"

    if ta.smc and ta.smc.liquidity_sweep:
        if sig == "short" and ta.smc.sweep_direction == "long":
            if ta.smc.reversal_ready and ta.smc.reversal_direction == "long":
                return False, "sweep лоев + reversal LONG"
            if signal_type in {"reversal_dump", "impulse_dump", "trend_dump", "vertical_dump"}:
                return False, "sweep лоев — short после снятия ликвидности рано"

    is_reversal = signal_type in {"reversal_pump", "reversal_dump"}
    if ta_conflicts_with_signal(ta, signal_side) and not is_reversal:
        return False, "сканер и TA в разные стороны"

    st = (signal_type or "").lower()
    effective_verdict = ta.verdict
    if ta.verdict == "WAIT":
        # Ранний WAIT→направление только если не погоня у края диапазона
        late_chase = (
            (sig == "long" and ta.range_position >= 0.85 and ta.momentum_pct >= 0.5)
            or (sig == "short" and ta.range_position <= 0.15 and ta.momentum_pct <= -0.5)
            or (ta.wave_phase or "") == "late_impulse"
        )
        early_trigger = (
            not late_chase
            and st in _EARLY_TRIGGER_SIGNAL_TYPES
            and (
                (sig == "long" and ta.action_priority == "long")
                or (sig == "short" and ta.action_priority == "short")
            )
        )
        if not early_trigger:
            if late_chase:
                return False, "импульс у края — не вдогонку, ждать откат/уровень"
            return False, "TA ждёт пробой уровня"
        effective_verdict = "LONG" if ta.action_priority == "long" else "SHORT"

    score = ta_display_score(ta)
    if score < min_ta_score:
        return False, f"TA {score}/10 — слабый сетап"

    if effective_verdict == "LONG":
        state, reason = _long_trigger_state(ta)
        near_ready, near_dist, near_tol = _near_trigger_ready(ta, "long", signal_type=signal_type)
        aggressive_pump = _is_aggressive_pump_signal(signal_type)
        if signal_type == "reversal_dump" and state not in {"ready", "armed"}:
            return False, f"откат вниз — {reason}"
        if ta.momentum_label and "вниз" in ta.momentum_label and ta.momentum_pct <= -0.8 and state != "ready":
            return False, f"импульс вниз — {reason}"
        if state == "ready":
            pass
        elif state == "armed" and (accept_armed or aggressive_pump or near_ready):
            return True, reason
        elif near_ready:
            if near_dist is not None:
                return True, f"цена у LONG-триггера ({near_dist:.2f}% до входа, допуск {near_tol:.2f}%)"
            return True, "цена у LONG-триггера"
        else:
            return False, reason
        if require_smc and ta.smc:
            smc_ok = ta.smc.structure_expansion or ta.smc.liquidity_sweep
            if not smc_ok:
                return False, "SMC не готов (нет expansion/sweep)"
        return True, "триггер LONG подтверждён"
    if effective_verdict == "SHORT":
        state, reason = _short_trigger_state(ta)
        near_ready, near_dist, near_tol = _near_trigger_ready(ta, "short", signal_type=signal_type)
        aggressive_dump = _is_aggressive_dump_signal(signal_type)
        if signal_type == "reversal_pump" and state not in {"ready", "armed"}:
            return False, f"отскок вверх — {reason}"
        if ta.momentum_pct >= 0.8 and state != "ready":
            mom = ta.momentum_label or "рост"
            return False, f"{mom} — {reason}"
        if state == "ready":
            pass
        elif state == "armed" and (accept_armed or aggressive_dump or near_ready):
            return True, reason
        elif near_ready:
            if near_dist is not None:
                return True, f"цена у SHORT-триггера ({near_dist:.2f}% до входа, допуск {near_tol:.2f}%)"
            return True, "цена у SHORT-триггера"
        else:
            return False, reason
        if require_smc and ta.smc:
            smc_ok = ta.smc.structure_expansion or ta.smc.liquidity_sweep
            if not smc_ok:
                return False, "SMC не готов (нет expansion/sweep)"
        return True, "триггер SHORT подтверждён"
    return False, "нет направления TA"


def entry_readiness_line_html(ready: bool, reason: str) -> str:
    if ready:
        return "✅ <b>Готов</b> — вход по плану TA"
    low = reason.lower()
    if any(x in low for x in ("отскок", "откат", "ждать пробой", "цена выше", "цена ниже на")):
        return f"🔶 <b>Не сейчас</b> · {reason}"
    if "TA ждёт" in reason:
        return f"⏳ <b>Ждать уровень</b> · {reason}"
    return f"⏳ <b>Ждать</b> · {reason}"


def format_flow_direction_label(ta: TAAnalysisResult) -> str:
    """Человекочитаемый вывод матрицы OI+CVD+liq → коррекция vs продолжение."""
    cont = int(ta.flow_continuation or 0)
    corr = int(ta.flow_correction or 0)
    if cont <= 0 and corr <= 0:
        return ""
    diff = cont - corr
    if diff >= 15:
        return f"факторы склоняются к <b>продолжению</b> (cont {cont} / corr {corr})"
    if diff <= -15:
        return f"факторы склоняются к <b>коррекции</b> (cont {cont} / corr {corr})"
    return f"факторы <b>смешаны</b> — нужен пробой уровня (cont {cont} / corr {corr})"


def ta_hot_analysis_block_html(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
) -> str:
    """Единый блок Hot: куда цена, конфликт со сканером, ключевые факторы."""
    parts: list[str] = []

    conflict = ta_scanner_conflict_line_html(ta, signal_side)
    if conflict:
        parts.append(conflict)

    if ta.narrative_plain:
        parts.append(ta.narrative_plain)
    elif ta.forecast_summary:
        parts.append(f"🔮 {ta.forecast_summary[:220]}")

    flow_line = format_flow_direction_label(ta)
    if flow_line:
        parts.append(f"🧭 {flow_line}")

    factors: list[str] = []
    if ta.phase_label and ta.phase_label != "Без явной фазы":
        factors.append(ta.phase_label)
    if ta.structure_label:
        factors.append(ta.structure_label[:48])
    if ta.oi_narrative_label and ta.oi_narrative_label != "Мало данных OI":
        factors.append(f"OI: {ta.oi_narrative_label}")
    if ta.smc and ta.smc.htf_structure:
        factors.append(f"HTF {ta.smc.htf_structure}")
    if ta.momentum_label:
        factors.append(ta.momentum_label)
    for note in (ta.flow_notes or [])[:2]:
        if note and note not in " · ".join(factors):
            factors.append(note[:56])
    if factors:
        parts.append("📊 " + " · ".join(list(dict.fromkeys(factors))[:5]))

    return "\n".join(parts)


def ta_plain_forecast_line(ta: TAAnalysisResult) -> str:
    """Простой прогноз — из единого narrative (run_ta_analysis)."""
    if ta.narrative_plain:
        return ta.narrative_plain
    if not ta.forecast_summary:
        return ""
    return f"📐 {ta.forecast_summary[:180]}"


def ta_signal_forecast_summary_line(ta: TAAnalysisResult) -> str:
    """Блок «на чём основано» — из единого narrative."""
    if ta.narrative_basis:
        return ta.narrative_basis
    if ta.forecast_summary:
        return f"🔮 {ta.forecast_summary[:150]}"
    return ""


def ta_what_to_do_line(ta: TAAnalysisResult, *, ready: bool = False) -> str:
    """План действий — из единого narrative."""
    if ta.narrative_plan:
        if ta.verdict in {"LONG", "SHORT"} and ready:
            return ta.narrative_plan
        if ta.verdict == "WAIT":
            return ta.narrative_plan
    return ""


def format_scenario_update_html(
    *,
    symbol: str,
    exchange: str,
    update_kind: str,
    price: float,
    move_pct: float,
    reference_price: float,
    correction_target: float | None = None,
    breakdown_level: float | None = None,
    breakout_level: float | None = None,
    ta: TAAnalysisResult | None = None,
    stop_hint: float | None = None,
    target_hints: list[float] | None = None,
    user_intent: str = "",
) -> str:
    """Короткое уведомление фазы 2: подтверждение сценария после первого сигнала."""
    ex = exchange.replace("Bybit", "ByBit").replace("bybit", "ByBit")
    header = f"🔔 <b>Обновление сценария</b> · {ex}\n<b>{symbol}</b> · ${fmt_price(price)}\n"

    if update_kind == "correction_started":
        body = (
            f"📉 <b>Откат начался</b> — −{move_pct:.1f}% от локального хая "
            f"(<b>{fmt_price(reference_price)}</b>)."
        )
        if correction_target:
            body += f"\nЦель отката по плану: ~<b>{fmt_price(correction_target)}</b>."
        if breakdown_level:
            body += f"\n▶️ SHORT только после закрепления ≤<b>{fmt_price(breakdown_level)}</b>."
        else:
            body += "\n▶️ Пока <b>не входить</b> — ждём подтверждения уровня."
    elif update_kind == "continuation_confirmed":
        body = (
            f"📈 <b>Продолжение</b> +{move_pct:.1f}% от алерта "
            f"(хай <b>{fmt_price(reference_price)}</b>). Откат <b>снят</b>."
        )
        if breakout_level:
            body += f"\n▶️ LONG при ≥<b>{fmt_price(breakout_level)}</b>."
    elif update_kind == "entry_short":
        body = (
            f"🔻 <b>Можно смотреть SHORT</b> — цена ≤<b>{fmt_price(reference_price)}</b> "
            f"({move_pct:+.1f}% от первого алерта)."
        )
        if stop_hint:
            body += f"\n🛑 стоп ~<b>{fmt_price(stop_hint)}</b>"
        if target_hints:
            tps = " / ".join(fmt_price(t) for t in target_hints[:2])
            body += f"\n🎯 {tps}"
        body += "\nПодтверждение: пробой + закрепление. Проверьте объём."
    elif update_kind == "entry_long":
        body = (
            f"🔺 <b>Можно смотреть LONG</b> — цена ≥<b>{fmt_price(reference_price)}</b> "
            f"({move_pct:+.1f}% от первого алерта)."
        )
        if stop_hint:
            body += f"\n🛑 стоп ~<b>{fmt_price(stop_hint)}</b>"
        if target_hints:
            tps = " / ".join(fmt_price(t) for t in target_hints[:2])
            body += f"\n🎯 {tps}"
        body += "\nПодтверждение: пробой + закрепление. Проверьте объём."
    elif update_kind == "cancelled_late":
        body = (
            f"Цена уже ушла от уровня <b>{fmt_price(reference_price)}</b> "
            f"({move_pct:+.1f}%) — ловить поздно, слежка снята."
        )
    elif update_kind == "cancelled_opposite":
        intent = (user_intent or "").upper() or "сценарий"
        body = (
            f"Против {intent}: ушли через <b>{fmt_price(reference_price)}</b> "
            f"({move_pct:+.1f}%). Сценарий отменён."
        )
    elif update_kind == "expired":
        body = "Время слежки вышло без подтверждённого входа."
    elif update_kind == "cancelled_user":
        body = "Слежка отменена вручную."
    else:
        body = f"Обновление: {update_kind}"

    if ta is not None and ta.verdict in {"LONG", "SHORT"}:
        score = ta_display_score(ta)
        body += f"\n📐 TA сейчас: <b>{ta.verdict}</b> {score}/10"
    return header + body


def ta_scenario_followup_caption_html(
    ta: TAAnalysisResult,
    update_kind: str,
    signal_side: str | None = None,
) -> str:
    """Одна строка к обновлению сценария."""
    if update_kind == "continuation_confirmed":
        if ta.breakout_level:
            return f"▶️ LONG при ≥<b>{fmt_price(ta.breakout_level)}</b>"
        return ""
    if update_kind == "correction_started":
        if ta.breakdown_level:
            return f"▶️ SHORT при ≤<b>{fmt_price(ta.breakdown_level)}</b>"
        return ""
    return ta_signal_scenario_line_html(ta, signal_side=signal_side)


def ta_user_intent_html(
    ta: TAAnalysisResult,
    user_side: str,
    *,
    sticky_breakout: float | None = None,
) -> str:
    """Оценка идеи пользователя: хочу SHORT / LONG vs текущий TA и прогноз."""
    side = user_side.lower()
    if side not in {"long", "short"}:
        return ""
    label = "LONG" if side == "long" else "SHORT"
    emoji = "🔺" if side == "long" else "🔻"
    score = ta_display_score(ta)
    rr_bad = (
        "вход невыгоден" in (ta.verdict_reason or "").lower()
        and not ta.liq_cascade_active
    )

    corr = ta.correction_path
    cont = ta.continuation_path
    forecast_correction = bool(corr and (cont is None or corr.confidence >= cont.confidence))

    aligned_verdict = (side == "long" and ta.verdict == "LONG") or (
        side == "short" and ta.verdict == "SHORT"
    )
    opposed_verdict = (side == "long" and ta.verdict == "SHORT") or (
        side == "short" and ta.verdict == "LONG"
    )
    aligned_priority = (side == "short" and ta.action_priority == "short") or (
        side == "long" and ta.action_priority == "long"
    )

    bo = _resolve_intent_breakout(ta, sticky_breakout)
    _, bd = _effective_breakout_breakdown(ta)
    px = ta.current_price or 0.0
    long_level_taken = bool(side == "long" and bo and px >= bo * 0.999)

    lines = [f"{emoji} <b>Ваша идея:</b> открыть <b>{label}</b>"]

    if ta.liq_cascade_active and side == "short":
        lines.append(
            "🟢 <b>Оценка:</b> <b>каскад ликвидаций лонгов</b> (как на CoinGlass) — "
            "агрессивный SHORT по импульсу, стоп у локального хая."
        )
        if ta.liq_cascade_note:
            lines.append(f"💥 {ta.liq_cascade_note}")
    elif rr_bad:
        if side == "short" and _is_late_dump_context(ta):
            lines.append(
                "🟡 <b>Оценка:</b> направление <b>вниз — логично</b>, но сейчас <b>NO TRADE</b>: "
                "цена уже в сильном дампе — шорт «в хвост» = большой стоп, маленькая цель."
            )
            bo_hint = bo
            if bo_hint and ta.current_price and bo_hint > ta.current_price:
                lines.append(
                    f"💡 <b>Когда шортить:</b> после отскока к <b>{fmt_price(bo_hint)}</b> "
                    f"и разворота вниз (не по текущей цене)."
                )
        else:
            lines.append(
                "⛔ <b>Оценка:</b> сейчас <b>NO TRADE</b> — соотношение риск/прибыль плохое, "
                "лучше дождаться лучшей точки."
            )
    elif aligned_verdict:
        if side == "long":
            state, _ = _long_trigger_state(ta)
            if ta.post_pump and state in {"armed", "far", "past"}:
                lines.append(
                    "🔴 <b>Оценка:</b> bias LONG, но <b>НЕ входить сейчас</b> — "
                    "после пампа только close ≥ триггера. Market-лонг = риск отката."
                )
            elif state in {"armed", "far"}:
                lines.append(
                    f"🟡 <b>Оценка:</b> TA <b>{label}</b> {score}/10 — "
                    "идея верная, но <b>ждите пробой</b> уровня (не market)."
                )
            else:
                lines.append(
                    f"✅ <b>Оценка:</b> TA совпадает с вами — <b>{label}</b> {score}/10, "
                    "работаем по плану ниже."
                )
        elif side == "short":
            state, _ = _short_trigger_state(ta)
            if state in {"armed", "far"}:
                lines.append(
                    f"🟡 <b>Оценка:</b> TA <b>{label}</b> {score}/10 — "
                    "ждите пробой вниз, не входить по рынку."
                )
            else:
                lines.append(
                    f"✅ <b>Оценка:</b> TA совпадает с вами — <b>{label}</b> {score}/10, "
                    "работаем по плану ниже."
                )
        else:
            lines.append(
                f"✅ <b>Оценка:</b> TA совпадает с вами — <b>{label}</b> {score}/10, "
                "работаем по плану ниже."
            )
    elif opposed_verdict:
        lines.append(
            f"🔴 <b>Оценка:</b> ваш <b>{label}</b> против текущего TA "
            f"(<b>{ta.verdict}</b> {score}/10) — высокий риск ошибки."
        )
    elif ta.verdict == "WAIT":
        if side == "long" and long_level_taken and ta.post_pump:
            lines.append(
                "🟡 <b>Оценка:</b> триггер LONG <b>уже пройден</b> — импульс вверх есть, "
                "но после пампа <b>лонг вдогонку</b> = риск покупки на хае. "
                "Лучше ретест или следующий resistance."
            )
        elif side == "long" and long_level_taken:
            lines.append(
                "🟢 <b>Оценка:</b> уровень входа <b>уже взят</b> — идея живая, "
                "но предпочтительнее вход от ретеста, не market chase."
            )
        elif side == "short" and forecast_correction:
            lines.append(
                "🟡 <b>Оценка:</b> направление <b>логичное</b> — базовый сценарий откат, "
                "но TA ещё <b>WAIT</b>: не входить без триггера."
            )
        elif side == "long" and forecast_correction and ta.post_pump:
            lines.append(
                "🔴 <b>Оценка:</b> LONG <b>против</b> сценария отката после пампа — "
                "риск покупки на хае."
            )
        elif side == "long" and not forecast_correction:
            lines.append(
                "🟢 <b>Оценка:</b> LONG совпадает с сценарием <b>продолжения</b> — "
                "ждите подтверждения пробоя."
            )
        elif side == "short" and not forecast_correction:
            lines.append(
                "🔴 <b>Оценка:</b> SHORT против сценария <b>продолжения вверх</b> — "
                "лучше не торопиться."
            )
        elif aligned_priority:
            lines.append(
                f"🟡 <b>Оценка:</b> ближе к вашему <b>{label}</b>, "
                "но подтверждения на графике ещё нет."
            )
        else:
            lines.append(
                f"⚪ <b>Оценка:</b> TA нейтрален — <b>{label}</b> пока без подтверждения."
            )
    else:
        lines.append(f"⚪ <b>Оценка:</b> проверьте уровни перед входом в <b>{label}</b>.")

    hint = _intent_entry_hint(ta, side, sticky_breakout=sticky_breakout)
    prefix = "подтверждение" if side == "short" else "вход"
    lines.append(f"▶️ <b>Когда входить ({prefix}):</b> {hint}.")

    if side == "short" and ta.post_pump and bo and bd and ta.current_price:
        if bo > ta.current_price * 1.002:
            lines.append(
                f"💡 <b>Агрессивно (выше риск):</b> short от отскока к "
                f"<b>{fmt_price(bo)}</b> — стоп выше range, не ждать пробоя ≤{fmt_price(bd)}."
            )
        else:
            mid = (bo + bd) / 2.0
            if abs(ta.current_price - mid) / ta.current_price < 0.04:
                lines.append(
                    f"💡 <b>Середина range</b> (~{fmt_price(mid)}): short без триггера — плохой R:R."
                )

    cancel = _intent_cancel_level(ta, side)
    if cancel:
        verb = "выше" if side == "short" else "ниже"
        lines.append(f"🛑 <b>Отмена идеи:</b> при пробое {verb} <b>{fmt_price(cancel)}</b>.")

    plain = _intent_plain_line(ta, side, sticky_breakout=sticky_breakout)
    if plain:
        lines.append(plain)
    lines.append(
        "📈 <b>На графике:</b> пунктир — <i>коррекция ↓</i> (оранж.) / "
        "<i>продолжение ↑</i> (зел.), ярче — вероятнее."
    )
    return "\n".join(lines)


def ta_signal_caption_html(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
    readiness: tuple[bool, str] | None = None,
    show_readiness_badge: bool = True,
    compact: bool = True,
    signal_type: str | None = None,
) -> str:
    """Сигналы: короткий блок для решения (по умолчанию compact)."""
    if not compact:
        return _ta_signal_caption_verbose(ta, signal_side=signal_side, readiness=readiness, show_readiness_badge=show_readiness_badge)
    return ta_signal_caption_compact_html(
        ta,
        signal_side=signal_side,
        readiness=readiness,
        show_readiness_badge=show_readiness_badge,
        signal_type=signal_type,
    )


def ta_signal_caption_compact_html(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
    readiness: tuple[bool, str] | None = None,
    show_readiness_badge: bool = True,
    signal_type: str | None = None,
) -> str:
    """1–2 строки: чёткий план без дублирования."""
    sig = (signal_side or "").lower()
    if sig not in {"long", "short"}:
        return ta_signal_scenario_line_html(ta, signal_side=signal_side) or ""
    return ta_signal_compact_block(
        ta,
        signal_side=sig,
        readiness=readiness,
        signal_type=signal_type,
    )


def _ta_signal_caption_verbose(
    ta: TAAnalysisResult,
    *,
    signal_side: str | None = None,
    readiness: tuple[bool, str] | None = None,
    show_readiness_badge: bool = True,
) -> str:
    """Полный TA-блок (ручной разбор / отладка)."""
    score = ta_display_score(ta)
    sig = (signal_side or "").lower()

    lines: list[str] = []
    if readiness is not None and show_readiness_badge:
        lines.append(entry_readiness_line_html(readiness[0], readiness[1]))
        action_line = ta_what_to_do_line(ta, ready=readiness[0])
        if not action_line and ta.narrative_plan:
            action_line = ta.narrative_plan
        if action_line:
            lines.append(action_line)

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
            bd_dist = _level_dist_pct(ta.current_price, ta.breakdown_level)
            if sig == "long" and ta.post_pump and bd_dist is not None and bd_dist > 6.0:
                extra = " · после пампа, ждём уровень"
            else:
                extra = " · ближе SHORT"
        else:
            extra = ""
        line = f"📐 TA · WAIT {score}/10{extra}"
    conflict = ta_scanner_conflict_line_html(ta, signal_side)
    smc_line = format_smc_compact_html(ta.smc) if ta.smc and ta.smc.smc_score >= 4 else ""
    base = f"{line}\n{ta_signal_scenario_line_html(ta, signal_side=signal_side)}"
    if conflict:
        base = f"{conflict}\n{base}"
    extra: list[str] = []
    plain = ta_plain_forecast_line(ta)
    if plain:
        extra.append(plain)
    forecast_line = ta_signal_forecast_summary_line(ta)
    if forecast_line:
        extra.append(forecast_line)
    if not ta.narrative_basis:
        if ta.oi_narrative_label and ta.oi_narrative_label != "Мало данных OI":
            extra.append(f"OI: {ta.oi_narrative_label}")
        for fl in ta.factor_lines[:2]:
            if fl and fl not in " ".join(extra):
                extra.append(fl)
    if smc_line:
        extra.append(smc_line)
    if extra:
        base = base + "\n" + "\n".join(extra)
    if lines:
        return "\n".join(lines) + "\n" + base
    return base


def _apply_post_pump_trigger_wait_guard(
    *,
    verdict: str,
    conf: int,
    reason: str,
    action_priority: str,
    post_pump: bool,
    current: float,
    breakout: float | None,
    breakdown: float | None,
    flow_correction: int,
    flow_continuation: int,
    momentum: str,
    drawdown_from_high_pct: float,
) -> tuple[str, int, str, str]:
    """После пампа без пробоя уровня — WAIT, не «активный LONG/SHORT» по рынку."""
    if not post_pump:
        return verdict, conf, reason, action_priority

    corr_bias = flow_correction >= flow_continuation - 2

    if verdict == "LONG" and breakout and current > 0:
        stub = TAAnalysisResult(
            verdict="LONG",
            breakout_level=breakout,
            current_price=current,
        )
        state, _ = _long_trigger_state(stub)
        if state in {"armed", "far", "past"}:
            wait_reason = f"после пампа — LONG только по close ≥{fmt_price(breakout)}"
            if corr_bias or momentum == "down" or drawdown_from_high_pct >= 1.5:
                wait_reason += " · базовый сценарий откат"
            verdict = "WAIT"
            action_priority = "long"
            conf = min(conf, 7 if corr_bias else 8)
            reason = f"{reason} · {wait_reason}" if reason else wait_reason

    if verdict == "SHORT" and breakdown and current > 0:
        stub = TAAnalysisResult(
            verdict="SHORT",
            breakdown_level=breakdown,
            current_price=current,
        )
        state, _ = _short_trigger_state(stub)
        cont_bias = flow_continuation > flow_correction + 2
        if state in {"armed", "far"} and cont_bias:
            wait_reason = f"после дампа — SHORT только по close ≤{fmt_price(breakdown)}"
            verdict = "WAIT"
            action_priority = "short"
            conf = min(conf, 8)
            reason = f"{reason} · {wait_reason}" if reason else wait_reason

    return verdict, conf, reason, action_priority


def _manual_signal_status(ta: TAAnalysisResult) -> str:
    """Человекочитаемый статус для ручного TA — без ложного «активен»."""
    rr_bad = "вход невыгоден" in (ta.verdict_reason or "").lower()
    if rr_bad:
        return "заблокирован (bad R:R)"

    if ta.verdict == "LONG":
        state, _ = _long_trigger_state(ta)
        if state == "ready":
            return "готов — вход по триггеру LONG"
        if state in {"armed", "far"}:
            return "ждёт пробой LONG — не входить по рынку"
        if state == "past":
            return "цена выше триггера — ждите ретест, не chase"
    if ta.verdict == "SHORT":
        state, _ = _short_trigger_state(ta)
        if state == "ready":
            return "готов — вход по триггеру SHORT"
        if state in {"armed", "far"}:
            return "ждёт пробой SHORT — не входить по рынку"
        if state == "past":
            return "цена ниже триггера — ждите ретест"

    if ta.verdict == "WAIT":
        if ta.action_priority == "long":
            return "WAIT · bias LONG — только по пробою вверх"
        if ta.action_priority == "short":
            return "WAIT · bias SHORT — только по пробою вниз"
        return "WAIT — без подтверждения"

    if ta.action_priority in {"long", "short"}:
        return f"armed — bias {ta.action_priority.upper()}, ждёт уровень"
    return "не активен"


def _manual_verdict_headline(ta: TAAnalysisResult) -> str:
    score = ta_display_score(ta)
    if ta.verdict == "WAIT" and ta.action_priority in {"long", "short"}:
        bias = ta.action_priority.upper()
        return f"📐 <b>TA</b> · <b>WAIT</b> · bias <b>{bias}</b> {score}/10"
    return f"📐 <b>TA</b> · <b>{ta.verdict}</b> {score}/10"


def ta_manual_detailed_html(ta: TAAnalysisResult) -> str:
    """Ручной TA: компактно и с акцентом на решение."""
    score = ta_display_score(ta)
    rr_bad = "вход невыгоден" in (ta.verdict_reason or "").lower()
    signal_status = _manual_signal_status(ta)

    p_short = 34
    p_long = 33
    if ta.verdict == "SHORT":
        p_short, p_long = 62, 20
    elif ta.verdict == "LONG":
        p_long, p_short = 62, 20
    elif ta.action_priority == "short":
        p_short, p_long = 52, 24
    elif ta.action_priority == "long":
        p_long, p_short = 52, 24
    p_flat = max(8, 100 - p_short - p_long)

    lines = [
        _manual_verdict_headline(ta),
        f"📍 <b>Сейчас:</b> цена <b>{fmt_price(ta.current_price)}</b> · {ta.momentum_label or ta.phase_label or 'контекст'}",
        f"🧭 <b>Статус:</b> {signal_status}.",
        f"📊 <b>Сценарии:</b> SHORT {p_short}% · LONG {p_long}% · FLAT {p_flat}%.",
    ]

    try:
        from .trade_analyst import fib_action_line_html

        lines.append(fib_action_line_html(ta))
    except Exception:
        if getattr(ta, "fib_reject_reason", None):
            lines.append(f"📐 Fib не строим: {ta.fib_reject_reason[:90]}")
        elif ta.fib_levels:
            lines.append("📐 Fib на графике · вход только с confluence П/С")

    if ta.narrative_plain:
        lines.append(ta.narrative_plain)
    flow_dir = format_flow_direction_label(ta)
    if flow_dir:
        lines.append(f"🧭 {flow_dir}")
    factor_bits: list[str] = []
    if ta.phase_label and ta.phase_label != "Без явной фазы":
        factor_bits.append(ta.phase_label)
    if ta.oi_narrative_label and ta.oi_narrative_label != "Мало данных OI":
        factor_bits.append(f"OI: {ta.oi_narrative_label}")
    if ta.smc and ta.smc.htf_structure:
        factor_bits.append(f"HTF {ta.smc.htf_structure}")
    if factor_bits:
        lines.append("📊 " + " · ".join(factor_bits[:4]))

    bo_lvl, bd_lvl = _effective_breakout_breakdown(ta)
    long_lvl = fmt_price(bo_lvl) if bo_lvl else "—"
    short_lvl = fmt_price(bd_lvl) if bd_lvl else "—"
    inv = _display_invalidation(ta)
    stop_lvl = fmt_price(inv) if inv else "—"
    tp1 = fmt_price(ta.target_prices[0]) if ta.target_prices else "—"

    if rr_bad:
        lines.append("⛔ <b>Решение:</b> <b>NO TRADE</b> (пропуск до лучшей точки входа).")
    elif ta.verdict == "LONG":
        lines.append(f"✅ <b>Решение:</b> LONG только после подтверждения выше <b>{long_lvl}</b>.")
    elif ta.verdict == "SHORT":
        lines.append(f"✅ <b>Решение:</b> SHORT только после подтверждения ниже <b>{short_lvl}</b>.")
    else:
        lines.append(
            f"⏳ <b>Решение:</b> WAIT. Ждать 5m close выше <b>{long_lvl}</b> "
            f"или ниже <b>{short_lvl}</b>."
        )

    if ta.current_price > 0 and bd_lvl and bd_lvl < ta.current_price:
        dist_short = (ta.current_price - bd_lvl) / ta.current_price * 100.0
        lines.append(f"👉 <b>Триггер SHORT:</b> 5m close ниже <b>{short_lvl}</b> (до уровня ~{dist_short:.1f}%).")
    elif bd_lvl:
        lines.append(f"👉 <b>Триггер SHORT:</b> 5m close ниже <b>{short_lvl}</b> + ретест снизу.")

    if ta.current_price > 0 and bo_lvl and bo_lvl > ta.current_price:
        dist_long = (bo_lvl - ta.current_price) / ta.current_price * 100.0
        lines.append(f"👉 <b>Триггер LONG:</b> 5m close выше <b>{long_lvl}</b> (до уровня ~{dist_long:.1f}%).")
    elif bo_lvl:
        lines.append(f"👉 <b>Триггер LONG:</b> 5m close выше <b>{long_lvl}</b> + ретест сверху.")

    lines.append(f"🎯 <b>План:</b> вход по факту · отмена <b>{stop_lvl}</b> · TP1 <b>{tp1}</b>.")
    if ta.verdict_reason:
        lines.append(f"⏱ <b>Протухание идеи:</b> если 3 свечи 5m без подтверждения — отменить вход.")

    risk_bits: list[str] = []
    if ta.post_pump:
        risk_bits.append("перегрев после пампа")
    if ta.repeat_spike_dump_risk:
        risk_bits.append("повторяемый spike→dump")
    if ta.post_pump and ta.flow_correction > ta.flow_continuation + 8:
        risk_bits.append("базовый сценарий — откат")
    elif ta.action_priority == "short":
        risk_bits.append("приоритет short")
    elif ta.action_priority == "long":
        risk_bits.append("приоритет long")
    if not risk_bits and ta.verdict_reason:
        risk_bits.append(ta.verdict_reason.split(" · ")[0])
    if risk_bits:
        lines.append(f"⚠️ <b>Риск:</b> {', '.join(risk_bits[:3])}.")

    if ta.forecast_summary:
        lines.append(f"🔮 <b>Прогноз:</b> {ta.forecast_summary}")

    if ta.cvd_delta is not None and ta.cvd_delta < 0 and (
        ta.verdict == "LONG" or ta.action_priority == "long"
    ):
        lines.append(
            f"⚠️ <b>CVD Δ отриц.</b> ({ta.cvd_delta / 1000:.1f}K) — агрессивные продажи, "
            "лонг только после подтверждения пробоя."
        )

    for fl in ta.factor_lines:
        if "CVD" in fl:
            src = {"live": "Bybit live", "taker": "Bybit taker"}.get(
                ta.cvd_source, "прокси по свечам",
            )
            lines.append(f"📈 <b>CVD ({src}):</b> {fl}")
            break

    smc_brief = _manual_smc_brief_html(ta.smc) if ta.smc else ""
    if smc_brief:
        lines.append(smc_brief)

    return "\n".join(lines)


def ta_telegram_caption_html(ta: TAAnalysisResult) -> str:
    """Подпись к графику сигналов (компактно)."""
    lines = [ta_action_summary_html(ta)]
    if ta.verdict_reason:
        note = ta.verdict_reason.split(" · ")[0][:60]
        lines.append(f"<i>{note}</i>")
    return "\n".join(lines)


def ta_analysis_chart_caption_html(
    ta: TAAnalysisResult,
    *,
    analysis_direction: str,
    post_dump_late: bool = False,
    liq_cascade_note: str = "",
) -> str:
    """Подпись к графику в чате анализов — согласована с текстом разбора."""
    side = analysis_direction.lower()
    if side not in {"long", "short"}:
        side = ta.action_priority if ta.action_priority in {"long", "short"} else "short"

    lines: list[str] = []
    if liq_cascade_note:
        lines.append(f"💥 {liq_cascade_note}")
    if post_dump_late or _is_late_dump_context(ta):
        lines.append("⚠️ <b>После обвала</b> — не гонись за входом")

    block = ta_signal_compact_block(
        ta,
        signal_side=side,
        readiness=(False, "анализ"),
    )
    if block:
        lines.append(block)
    else:
        lines.append(ta_action_summary_html(ta))

    if ta.phase_label and ta.verdict == "WAIT":
        lines.append(f"<i>{ta.phase_label}</i>")

    for fl in ta.factor_lines:
        if "CVD" in fl or "taker" in fl.lower() or "live" in fl.lower():
            src = {"live": "live", "taker": "taker"}.get(ta.cvd_source, "")
            if src:
                lines.append(f"📈 {fl}")
            break

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
    if ta.forecast_summary:
        lines.append(ta.forecast_summary[:64])
    for fl in ta.factor_lines[:2]:
        lines.append(fl[:48])
    return "\n".join(lines[:8])


def ta_chart_plan_text(ta: TAAnalysisResult) -> str:
    if not ta.trader_plan:
        return ""
    lines = ["ПЛАН ДЕЙСТВИЙ"]
    for i, step in enumerate(ta.trader_plan[:5], 1):
        lines.append(f"{i}. {step}")
    return "\n".join(lines)


def ta_chart_summary_text(ta: TAAnalysisResult) -> str:
    score = ta_display_score(ta)
    lines = [f"ИТОГ: {ta.verdict} {score}/10"]
    if ta.professional_summary:
        lines.append(ta.professional_summary[:200])
    if ta.verdict_reason:
        lines.append(ta.verdict_reason.split(" · ")[0][:80])
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
        lines.append(ta.primary_scenario[:55])
    if ta.range_position:
        lines.append(f"range: {ta.range_position * 100:.0f}%")
    return "\n".join(lines[:8])


def ta_chart_bottom_right_text(ta: TAAnalysisResult) -> str:
    """Нижний правый блок: контекст, поток, факторы, прогноз."""
    import re

    def _plain(s: str) -> str:
        return re.sub(r"<[^>]+>", "", s).strip()

    lines = ["АНАЛИЗ"]
    if ta.flow_correction or ta.flow_continuation:
        lines.append(f"Поток: откат {ta.flow_correction} · рост {ta.flow_continuation}")
    for note in ta.flow_notes[:3]:
        if note not in lines:
            lines.append(note[:56])
    for fl in ta.factor_lines[:2]:
        lines.append(fl[:52])
    if ta.narrative_plain:
        lines.append(_plain(ta.narrative_plain)[:100])
    elif ta.narrative_plan:
        lines.append(_plain(ta.narrative_plan)[:90])
    if ta.smc and ta.smc.summary:
        lines.append(ta.smc.summary[:72])
    ctx = ta_chart_context_text(ta)
    for row in ctx.splitlines()[1:4]:
        if row and row not in " · ".join(lines):
            lines.append(row[:58])
    return "\n".join(lines[:6])


def ta_chart_tv_overlay_text(
    ta: TAAnalysisResult,
    *,
    hours: int = 5,
    interval_minutes: int = 5,
) -> str:
    """Компактная панель для TV-оверлея — без дублирования блоков."""
    import re

    score = ta_display_score(ta)
    lines = [
        f"ИТОГ: {ta.verdict} {score}/10",
        _verdict_plain(ta)[:60],
    ]
    triggers: list[str] = []
    if ta.breakout_level:
        extra = f" +{ta.dist_to_long_pct:.1f}%" if ta.dist_to_long_pct is not None else ""
        triggers.append(f"LONG {fmt_price(ta.breakout_level)}{extra}")
    if ta.breakdown_level:
        extra = f" −{ta.dist_to_short_pct:.1f}%" if ta.dist_to_short_pct is not None else ""
        triggers.append(f"SHORT {fmt_price(ta.breakdown_level)}{extra}")
    if triggers:
        lines.append(" · ".join(triggers))
    row_bits: list[str] = []
    if ta.invalidation_price:
        row_bits.append(f"SL {fmt_price(ta.invalidation_price)}")
    if ta.target_prices:
        row_bits.append(f"TP {' → '.join(fmt_price(t) for t in ta.target_prices[:2])}")
    if row_bits:
        lines.append(" · ".join(row_bits))
    if ta.primary_scenario:
        lines.append(ta.primary_scenario[:64])
    elif ta.narrative_plain:
        lines.append(re.sub(r"<[^>]+>", "", ta.narrative_plain).strip()[:64])
    lines.append(f"{hours}ч / {interval_minutes}m")
    return "\n".join(lines[:7])


def ta_chart_legend_text() -> str:
    return (
        "█ BUY/flat/SELL | пунктир = сценарий | sweep ○ | Vol/RSI | "
        "STOP/TP | коррекция/продолжение"
    )
