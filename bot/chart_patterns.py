"""Детекция классических графических фигур на OHLC."""
from __future__ import annotations

from dataclasses import dataclass

from .bybit_klines import KlineBar
from .chart_pattern_models import ChartPattern, PatternLine, PatternPoint
from .pattern_specs import (
    DOUBLE_EXTREMUM_TOLERANCE_PCT,
    DOUBLE_MIN_BARS_BETWEEN,
    FLAG_BODY_MAX_BARS,
    FLAG_BODY_MIN_BARS,
    FLAG_PARALLEL_SLOPE_RATIO,
    FLAG_POLE_MAX_BARS,
    FLAG_POLE_MIN_BARS,
    CUP_ENABLED,
    CUP_HANDLE_MAX_BARS,
    CUP_MIN_BARS,
    CUP_RIM_TOLERANCE_PCT,
    DIAMOND_MIN_SWINGS,
    HEAD_SHOULDER_TOLERANCE_PCT,
    MAX_CHART_PATTERNS,
    MAX_REPORT_PATTERNS,
    MIN_DRAW_CONFIDENCE,
    MIN_PATTERN_CONFIDENCE,
    MIN_TRADE_PATTERN_CONFIDENCE,
    OVERLAP_FAMILIES,
    PATTERN_LABELS_RU,
    PENNANT_BODY_MAX_BARS,
    RECTANGLE_MAX_RANGE_PCT,
    RECTANGLE_MIN_BARS,
    ROUNDED_MIN_BARS,
    ROUNDED_RIM_TOLERANCE_PCT,
    TARGET_HS_FACTOR,
    TARGET_POLE_FACTOR,
    TARGET_RECTANGLE_FACTOR,
    TARGET_TRIANGLE_FACTOR,
    THREE_INDIANS_FIB,
    THREE_INDIANS_MIN_BARS,
    THREE_INDIANS_TOLERANCE_PCT,
    TRIPLE_EXTREMUM_TOLERANCE_PCT,
    TRIPLE_MAX_SPAN_BARS,
    TRIPLE_MIN_BARS_BETWEEN,
    TRIANGLE_MIN_SWINGS,
    VOLUME_BREAKOUT_SPIKE,
    VOLUME_CONTRACT_RATIO,
    WEDGE_MIN_SWINGS,
)


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    kind: str


def _find_swing_points(bars: list[KlineBar], *, window: int = 2) -> list[SwingPoint]:
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


def compute_atr(bars: list[KlineBar], *, period: int = 14) -> float:
    if not bars:
        return 1.0
    if len(bars) < 2:
        return max(bars[-1].high - bars[-1].low, bars[-1].close * 0.005)
    trs: list[float] = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        )
        trs.append(tr)
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window) if window else bars[-1].close * 0.005


def find_pattern_swings(bars: list[KlineBar], *, window: int = 3) -> list[SwingPoint]:
    """Свинги для паттернов: меньше шума, чем window=2."""
    raw = _find_swing_points(bars, window=window)
    if not raw:
        return []
    atr = compute_atr(bars)
    min_move = atr * 0.45
    filtered: list[SwingPoint] = []
    for swing in raw:
        if filtered and swing.kind == filtered[-1].kind:
            prev = filtered[-1]
            if swing.kind == "high" and swing.price >= prev.price:
                filtered[-1] = swing
            elif swing.kind == "low" and swing.price <= prev.price:
                filtered[-1] = swing
            continue
        if filtered:
            if swing.index - filtered[-1].index < 4:
                continue
            if abs(swing.price - filtered[-1].price) < min_move:
                continue
        filtered.append(swing)
    return filtered


def _line_value(line: PatternLine, idx: int) -> float:
    if line.end_idx == line.start_idx:
        return line.start_price
    slope = (line.end_price - line.start_price) / (line.end_idx - line.start_idx)
    return line.start_price + slope * (idx - line.start_idx)


def _pct_diff(a: float, b: float) -> float:
    ref = (a + b) / 2.0
    if ref <= 0:
        return 100.0
    return abs(a - b) / ref * 100.0

def _status_from_break(
    bars: list[KlineBar],
    *,
    bullish: bool,
    trigger: float,
    buffer_pct: float = 0.05,
) -> str:
    if not bars:
        return "forming"
    close = bars[-1].close
    buf = trigger * buffer_pct / 100.0
    if bullish and close > trigger + buf:
        return "confirmed"
    if not bullish and close < trigger - buf:
        return "confirmed"
    return "forming"


def _avg_volume(bars: list[KlineBar], start: int, end: int) -> float:
    if not bars or end <= start:
        return 0.0
    start = max(0, start)
    end = min(len(bars) - 1, end)
    if end < start:
        return 0.0
    vols = [max(0.0, float(getattr(b, "volume", 0) or 0)) for b in bars[start : end + 1]]
    if not vols or sum(vols) <= 0:
        return 0.0
    return sum(vols) / len(vols)


def _volume_facts(
    bars: list[KlineBar],
    *,
    impulse_start: int,
    impulse_end: int,
    body_start: int,
    body_end: int,
    status: str,
) -> tuple[bool, bool, float, str]:
    """Сжатие объёма в фигуре + всплеск на пробое (классика флаг/вымпел/дно)."""
    pole_vol = _avg_volume(bars, impulse_start, impulse_end)
    body_vol = _avg_volume(bars, body_start, body_end)
    break_vol = _avg_volume(bars, max(0, len(bars) - 3), len(bars) - 1)
    contracted = bool(pole_vol > 0 and body_vol > 0 and body_vol <= pole_vol * VOLUME_CONTRACT_RATIO)
    breakout = bool(
        status == "confirmed"
        and body_vol > 0
        and break_vol >= body_vol * VOLUME_BREAKOUT_SPIKE
    )
    score = 0.0
    if contracted:
        score += 0.5
    if breakout:
        score += 0.5
    note = ""
    if contracted and breakout:
        note = "объём сжался в фигуре → всплеск на пробое (классика)"
    elif contracted:
        note = "объём сжимается внутри фигуры — ждать пробой"
    elif breakout:
        note = "всплеск объёма на пробое"
    return contracted, breakout, score, note


def _score_geometry(base: float, **parts: float) -> tuple[float, dict[str, float]]:
    breakdown = {k: round(v, 3) for k, v in parts.items()}
    total = base * 0.35 + sum(parts.values())
    return min(1.0, max(0.0, total)), breakdown


def _prior_trend_bias(bars: list[KlineBar], end_idx: int, *, lookback: int = 24) -> str:
    """bullish / bearish / neutral по движению до фигуры."""
    if end_idx <= 2:
        return "neutral"
    start = max(0, end_idx - lookback)
    move = bars[end_idx].close - bars[start].open
    ref = abs(bars[start].open) or 1e-9
    pct = move / ref * 100.0
    if pct >= 1.2:
        return "bullish"
    if pct <= -1.2:
        return "bearish"
    return "neutral"


def _is_near_range_extreme(
    bars: list[KlineBar],
    *,
    side: str,
    lookback: int = 40,
    band_pct: float = 0.22,
) -> bool:
    """Ложный пробой только у пика/дна движения (статья)."""
    if len(bars) < 10:
        return False
    seg = bars[-lookback:]
    hi = max(b.high for b in seg)
    lo = min(b.low for b in seg)
    span = hi - lo
    if span <= 0:
        return False
    close = bars[-1].close
    if side == "bearish":
        return close >= hi - span * band_pct
    return close <= lo + span * band_pct


def _patterns_overlap(a: ChartPattern, b: ChartPattern) -> bool:
    """Одна ценовая/временная зона — не показывать оба."""
    if not a.points or not b.points:
        return a.kind == b.kind
    a0 = min(p.index for p in a.points)
    a1 = max(p.index for p in a.points)
    b0 = min(p.index for p in b.points)
    b1 = max(p.index for p in b.points)
    # пересечение по времени
    if a1 < b0 or b1 < a0:
        return False
    overlap = min(a1, b1) - max(a0, b0)
    span = max(a1 - a0, b1 - b0, 1)
    if overlap / span < 0.35:
        return False
    for family in OVERLAP_FAMILIES:
        if a.kind in family and b.kind in family:
            return True
    # близкие зоны по цене
    if a.zone_top and a.zone_bottom and b.zone_top and b.zone_bottom:
        mid_a = (a.zone_top + a.zone_bottom) / 2
        mid_b = (b.zone_top + b.zone_bottom) / 2
        if mid_a > 0 and abs(mid_a - mid_b) / mid_a < 0.012:
            return True
    return False


def _suppress_overlaps(patterns: list[ChartPattern]) -> list[ChartPattern]:
    """Приоритет: confirmed → выше confidence → Баскервили сильнее сырой ГиП."""
    def _rank(p: ChartPattern) -> tuple:
        bask = 1 if p.kind.startswith("baskerville") else 0
        conf_ok = 1 if p.status == "confirmed" else 0
        return (conf_ok, bask, p.confidence)

    kept: list[ChartPattern] = []
    for pat in sorted(patterns, key=_rank, reverse=True):
        if any(_patterns_overlap(pat, k) for k in kept):
            continue
        kept.append(pat)
    return kept


def _detect_rectangle(
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    """Горизонтальный канал: продолжение или разворот после пробоя."""
    out: list[ChartPattern] = []
    n = len(bars)
    if n < RECTANGLE_MIN_BARS + 8:
        return out
    for width in range(RECTANGLE_MIN_BARS, min(48, n - 4)):
        start = n - width - 3
        if start < 2:
            continue
        body = bars[start : start + width]
        top = max(b.high for b in body)
        bottom = min(b.low for b in body)
        mid = (top + bottom) / 2
        if mid <= 0:
            continue
        range_pct = (top - bottom) / mid * 100.0
        if range_pct > RECTANGLE_MAX_RANGE_PCT or (top - bottom) < atr * 0.7:
            continue
        # касания границ
        top_hits = sum(1 for b in body if abs(b.high - top) / mid * 100 <= 0.35)
        bot_hits = sum(1 for b in body if abs(b.low - bottom) / mid * 100 <= 0.35)
        if top_hits < 2 or bot_hits < 2:
            continue
        prior = _prior_trend_bias(bars, start)
        height = top - bottom
        close = bars[-1].close
        if close > top * 1.001:
            direction = "bullish"
            status = "confirmed"
            subtype = "continuation" if prior == "bullish" else "reversal"
            target = top + height * TARGET_RECTANGLE_FACTOR
            stop = bottom - atr * 0.2
        elif close < bottom * 0.999:
            direction = "bearish"
            status = "confirmed"
            subtype = "continuation" if prior == "bearish" else "reversal"
            target = bottom - height * TARGET_RECTANGLE_FACTOR
            stop = top + atr * 0.2
        else:
            direction = "bullish" if prior == "bullish" else "bearish" if prior == "bearish" else "neutral"
            status = "forming"
            subtype = "continuation"
            target = top + height * TARGET_RECTANGLE_FACTOR if direction != "bearish" else bottom - height * TARGET_RECTANGLE_FACTOR
            stop = bottom - atr * 0.2 if direction != "bearish" else top + atr * 0.2
        conf, breakdown = _score_geometry(
            0.62,
            touches=0.14 if top_hits + bot_hits >= 5 else 0.08,
            channel=0.12,
            breakout=0.14 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="rectangle",
                subtype=subtype,
                status=status,
                points=(
                    PatternPoint(start, top, "rect_top"),
                    PatternPoint(start + width - 1, bottom, "rect_bottom"),
                ),
                lines=(
                    PatternLine(start, top, start + width - 1, top, "upper_bound"),
                    PatternLine(start, bottom, start + width - 1, bottom, "lower_bound"),
                ),
                zone_top=top,
                zone_bottom=bottom,
                neckline=None,
                pole_height=height,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:rectangle",
                label_ru=PATTERN_LABELS_RU["rectangle"] + (f" ({subtype})" if subtype else ""),
                direction=direction,
            )
        )
        break
    return out[:1]


def _detect_double_top_bottom(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    if len(highs) >= 2:
        for i in range(len(highs) - 1):
            h1, h2 = highs[i], highs[i + 1]
            if h2.index - h1.index < DOUBLE_MIN_BARS_BETWEEN:
                continue
            # без большого временного разрыва (статья: сразу после H1 → H2)
            if h2.index - h1.index > 40:
                continue
            if _pct_diff(h1.price, h2.price) > DOUBLE_EXTREMUM_TOLERANCE_PCT:
                continue
            if _prior_trend_bias(bars, h1.index) != "bullish":
                continue
            valleys = [s for s in lows if h1.index < s.index < h2.index]
            if not valleys:
                continue
            valley = min(valleys, key=lambda s: s.price)
            peak = max(h1.price, h2.price)
            neckline = valley.price
            height = peak - neckline
            if height < atr * 0.8:
                continue
            target = neckline - height * TARGET_HS_FACTOR
            stop = peak + atr * 0.25
            status = _status_from_break(bars, bullish=False, trigger=neckline)
            conf, breakdown = _score_geometry(
                0.62,
                symmetry=0.16 if _pct_diff(h1.price, h2.price) < 0.35 else 0.08,
                spacing=0.12,
                neckline=0.10,
                context=0.08,
                breakout=0.14 if status == "confirmed" else 0.0,
            )
            if conf < MIN_PATTERN_CONFIDENCE:
                continue
            out.append(
                ChartPattern(
                    kind="double_top",
                    subtype="reversal",
                    status=status,
                    points=(
                        PatternPoint(h1.index, h1.price, "peak1"),
                        PatternPoint(h2.index, h2.price, "peak2"),
                        PatternPoint(valley.index, valley.price, "neck"),
                    ),
                    lines=(
                        PatternLine(h1.index, h1.price, h2.index, h2.price, "resistance"),
                        PatternLine(valley.index, valley.price, h2.index, valley.price, "neckline"),
                    ),
                    zone_top=peak,
                    zone_bottom=neckline,
                    neckline=PatternLine(valley.index, neckline, h2.index, neckline, "neckline"),
                    pole_height=height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule="buyhold:double_top",
                    label_ru=PATTERN_LABELS_RU["double_top"],
                    direction="bearish",
                )
            )

    if len(lows) >= 2 and highs:
        for i in range(len(lows) - 1):
            l1, l2 = lows[i], lows[i + 1]
            if l2.index - l1.index < DOUBLE_MIN_BARS_BETWEEN:
                continue
            if l2.index - l1.index > 40:
                continue
            if _pct_diff(l1.price, l2.price) > DOUBLE_EXTREMUM_TOLERANCE_PCT:
                continue
            if _prior_trend_bias(bars, l1.index) != "bearish":
                continue
            peaks = [s for s in highs if l1.index < s.index < l2.index]
            if not peaks:
                continue
            peak = max(peaks, key=lambda s: s.price)
            neckline = peak.price
            trough = min(l1.price, l2.price)
            height = neckline - trough
            if height < atr * 0.8:
                continue
            target = neckline + height * TARGET_HS_FACTOR
            stop = trough - atr * 0.25
            status = _status_from_break(bars, bullish=True, trigger=neckline)
            conf, breakdown = _score_geometry(
                0.62,
                symmetry=0.16 if _pct_diff(l1.price, l2.price) < 0.35 else 0.08,
                spacing=0.12,
                neckline=0.10,
                context=0.08,
                breakout=0.14 if status == "confirmed" else 0.0,
            )
            if conf < MIN_PATTERN_CONFIDENCE:
                continue
            out.append(
                ChartPattern(
                    kind="double_bottom",
                    subtype="reversal",
                    status=status,
                    points=(
                        PatternPoint(l1.index, l1.price, "trough1"),
                        PatternPoint(l2.index, l2.price, "trough2"),
                        PatternPoint(peak.index, peak.price, "neck"),
                    ),
                    lines=(
                        PatternLine(l1.index, l1.price, l2.index, l2.price, "support"),
                        PatternLine(peak.index, neckline, l2.index, neckline, "neckline"),
                    ),
                    zone_top=neckline,
                    zone_bottom=trough,
                    neckline=PatternLine(peak.index, neckline, l2.index, neckline, "neckline"),
                    pole_height=height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule="buyhold:double_bottom",
                    label_ru=PATTERN_LABELS_RU["double_bottom"],
                    direction="bullish",
                )
            )
    return out


def _detect_head_shoulders(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    """ГиП: High1 < High2 > High3, шея по коррекционным Low.
    Перевёрнутая: Low1 > Low2 < Low3, шея по коррекционным High.
    Только после направленного движения (картинки BuyHold).
    """
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 3 or len(lows) < 2:
        return out

    for i in range(len(highs) - 2):
        ls, head, rs = highs[i], highs[i + 1], highs[i + 2]
        # High1 < High2 > High3
        if not (ls.price < head.price and head.price > rs.price):
            continue
        if _pct_diff(ls.price, rs.price) > HEAD_SHOULDER_TOLERANCE_PCT:
            continue
        prior = _prior_trend_bias(bars, ls.index, lookback=28)
        if prior != "bullish":
            continue
        left_vals = [s for s in lows if ls.index < s.index < head.index]
        right_vals = [s for s in lows if head.index < s.index < rs.index]
        if not left_vals or not right_vals:
            continue
        nl_l = min(left_vals, key=lambda s: s.price)
        nl_r = min(right_vals, key=lambda s: s.price)
        neckline = PatternLine(nl_l.index, nl_l.price, nl_r.index, nl_r.price, "neckline")
        neck_now = _line_value(neckline, rs.index)
        height = head.price - neck_now
        if height < atr * 1.1:
            continue
        # закрепление под шеей
        status = _status_from_break(bars, bullish=False, trigger=neck_now)
        target = neck_now - height * TARGET_HS_FACTOR
        stop = max(ls.price, rs.price) + atr * 0.15
        # идеал: шея горизонтальна или слегка вверх (после роста)
        neck_slope_bonus = 0.06 if nl_r.price >= nl_l.price * 0.998 else 0.0
        conf, breakdown = _score_geometry(
            0.64,
            structure=0.14,
            shoulders=0.12 if _pct_diff(ls.price, rs.price) < 1.5 else 0.06,
            context=0.10,
            neck=neck_slope_bonus,
            breakout=0.14 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="head_shoulders",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(ls.index, ls.price, "left_shoulder"),
                    PatternPoint(head.index, head.price, "head"),
                    PatternPoint(rs.index, rs.price, "right_shoulder"),
                    PatternPoint(nl_l.index, nl_l.price, "neck_left"),
                    PatternPoint(nl_r.index, nl_r.price, "neck_right"),
                ),
                lines=(neckline,),
                zone_top=head.price,
                zone_bottom=min(nl_l.price, nl_r.price),
                neckline=neckline,
                pole_height=height,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:head_shoulders_H1<H2>H3",
                label_ru=PATTERN_LABELS_RU["head_shoulders"],
                direction="bearish",
            )
        )

    for i in range(len(lows) - 2):
        ls, head, rs = lows[i], lows[i + 1], lows[i + 2]
        # Low1 > Low2 < Low3
        if not (ls.price > head.price and head.price < rs.price):
            continue
        if _pct_diff(ls.price, rs.price) > HEAD_SHOULDER_TOLERANCE_PCT:
            continue
        prior = _prior_trend_bias(bars, ls.index, lookback=28)
        if prior != "bearish":
            continue
        left_peaks = [s for s in highs if ls.index < s.index < head.index]
        right_peaks = [s for s in highs if head.index < s.index < rs.index]
        if not left_peaks or not right_peaks:
            continue
        nl_l = max(left_peaks, key=lambda s: s.price)
        nl_r = max(right_peaks, key=lambda s: s.price)
        neckline = PatternLine(nl_l.index, nl_l.price, nl_r.index, nl_r.price, "neckline")
        neck_now = _line_value(neckline, rs.index)
        height = neck_now - head.price
        if height < atr * 1.1:
            continue
        status = _status_from_break(bars, bullish=True, trigger=neck_now)
        target = neck_now + height * TARGET_HS_FACTOR
        stop = min(ls.price, rs.price) - atr * 0.15
        # статья: в идеале шея наклонена вниз
        neck_slope_bonus = 0.08 if nl_r.price <= nl_l.price * 1.002 else 0.0
        conf, breakdown = _score_geometry(
            0.64,
            structure=0.14,
            shoulders=0.12 if _pct_diff(ls.price, rs.price) < 1.5 else 0.06,
            context=0.10,
            neck=neck_slope_bonus,
            breakout=0.14 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="inverse_head_shoulders",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(ls.index, ls.price, "left_shoulder"),
                    PatternPoint(head.index, head.price, "head"),
                    PatternPoint(rs.index, rs.price, "right_shoulder"),
                    PatternPoint(nl_l.index, nl_l.price, "neck_left"),
                    PatternPoint(nl_r.index, nl_r.price, "neck_right"),
                ),
                lines=(neckline,),
                zone_top=max(nl_l.price, nl_r.price),
                zone_bottom=head.price,
                neckline=neckline,
                pole_height=height,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:inverse_hs_L1>L2<L3",
                label_ru=PATTERN_LABELS_RU["inverse_head_shoulders"],
                direction="bullish",
            )
        )
    # оставляем лучшие 2 для Баскервилей / suppress
    return sorted(out, key=lambda p: p.confidence, reverse=True)[:2]


def _fit_bounds(
    bars: list[KlineBar],
    start_idx: int,
    end_idx: int,
) -> tuple[float, float, float, float] | None:
    seg = bars[start_idx : end_idx + 1]
    if len(seg) < 3:
        return None
    highs = [b.high for b in seg]
    lows = [b.low for b in seg]
    return start_idx, end_idx, max(highs), min(lows)


def _detect_flag_pennant(
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    """Флаг = почти параллельный канал против штока.
    Вымпел = короткий сходящийся треугольник A-B-C-D после резкого импульса.
    Цель ≈ 0.85 × длина штока от точки пробоя.
    """
    out: list[ChartPattern] = []
    n = len(bars)
    if n < 24:
        return out
    swings = find_pattern_swings(bars, window=2)

    search_from = max(8, n - 70)
    for pole_start in range(search_from, n - 10):
        for pole_len in range(FLAG_POLE_MIN_BARS, FLAG_POLE_MAX_BARS + 1):
            pole_end = pole_start + pole_len
            if pole_end >= n - 6:
                break
            pole_move = bars[pole_end].close - bars[pole_start].open
            if abs(pole_move) < atr * 2.5:
                continue
            bullish = pole_move > 0
            max_body = min(FLAG_BODY_MAX_BARS, n - pole_end - 1)
            for body_len in range(FLAG_BODY_MIN_BARS, max_body + 1):
                body_end = pole_end + body_len
                body_swings = [s for s in swings if pole_end <= s.index <= body_end]
                bh = [s for s in body_swings if s.kind == "high"]
                bl = [s for s in body_swings if s.kind == "low"]
                if len(bh) < 2 or len(bl) < 2:
                    continue
                a_high, c_high = bh[0], bh[-1]
                b_low, d_low = bl[0], bl[-1]
                if not (a_high.index < c_high.index and b_low.index < d_low.index):
                    continue
                body_height = max(s.price for s in bh) - min(s.price for s in bl)
                pole_height = abs(pole_move)
                if body_height > pole_height * 0.55 or body_height < atr * 0.2:
                    continue

                upper = PatternLine(a_high.index, a_high.price, c_high.index, c_high.price, "upper_bound")
                lower = PatternLine(b_low.index, b_low.price, d_low.index, d_low.price, "lower_bound")
                span_u = max(1, upper.end_idx - upper.start_idx)
                span_l = max(1, lower.end_idx - lower.start_idx)
                slope_top = (upper.end_price - upper.start_price) / span_u
                slope_bot = (lower.end_price - lower.start_price) / span_l

                converging = c_high.price < a_high.price and d_low.price > b_low.price
                max_abs = max(abs(slope_top), abs(slope_bot), atr * 1e-6)
                parallel = abs(slope_top - slope_bot) / max_abs <= FLAG_PARALLEL_SLOPE_RATIO

                # флаг: против импульса; вымпел: сжатие
                against_pole = (bullish and slope_top <= atr * 0.02) or (not bullish and slope_top >= -atr * 0.02)
                if converging and body_len <= PENNANT_BODY_MAX_BARS:
                    kind = "pennant"
                elif parallel and against_pole:
                    kind = "flag"
                else:
                    continue

                trigger = _line_value(upper, body_end) if bullish else _line_value(lower, body_end)
                status = _status_from_break(bars, bullish=bullish, trigger=trigger)
                # Цель = высота флагштока от точки пробоя (measured move)
                target = (
                    trigger + pole_height * TARGET_POLE_FACTOR
                    if bullish
                    else trigger - pole_height * TARGET_POLE_FACTOR
                )
                # Стоп — за экстремум полотнища (не «ATR от линии»)
                body_seg = bars[pole_end : body_end + 1]
                flag_low = min(b.low for b in body_seg) if body_seg else _line_value(lower, body_end)
                flag_high = max(b.high for b in body_seg) if body_seg else _line_value(upper, body_end)
                stop = (flag_low - atr * 0.05) if bullish else (flag_high + atr * 0.05)

                vol_c, vol_b, vol_s, vol_note = _volume_facts(
                    bars,
                    impulse_start=pole_start,
                    impulse_end=pole_end,
                    body_start=pole_end,
                    body_end=body_end,
                    status=status,
                )
                # Вымпел без сжатия объёма — слабее; флаг терпимее
                if kind == "pennant" and not vol_c and status == "forming":
                    continue

                conf, breakdown = _score_geometry(
                    0.58,
                    pole=0.14,
                    compress=0.12 if kind == "pennant" else 0.08,
                    channel=0.10 if kind == "flag" else 0.04,
                    against=0.06 if against_pole else 0.0,
                    volume=0.10 * vol_s,
                    breakout=0.14 if status == "confirmed" else 0.0,
                    vol_confirm=0.08 if (status == "confirmed" and vol_b) else 0.0,
                )
                if conf < MIN_PATTERN_CONFIDENCE:
                    continue
                entry_mode = "breakout" if status == "confirmed" else "wait"
                psycho = (
                    "продолжение тренда после паузы (флагшток→полотнище)"
                    if kind == "flag"
                    else "пауза в импульсе (вымпел) — пробой по тренду"
                )
                if vol_note:
                    psycho = f"{psycho} · {vol_note}"
                out.append(
                    ChartPattern(
                        kind=kind,
                        subtype="continuation",
                        status=status,
                        points=(
                            PatternPoint(pole_start, bars[pole_start].open, "pole_start"),
                            PatternPoint(pole_end, bars[pole_end].close, "pole_end"),
                            PatternPoint(a_high.index, a_high.price, "A"),
                            PatternPoint(b_low.index, b_low.price, "B"),
                            PatternPoint(c_high.index, c_high.price, "C"),
                            PatternPoint(d_low.index, d_low.price, "D"),
                        ),
                        lines=(upper, lower),
                        zone_top=max(a_high.price, c_high.price),
                        zone_bottom=min(b_low.price, d_low.price),
                        neckline=None,
                        pole_height=pole_height,
                        target_price=target,
                        stop_price=stop,
                        confidence=conf,
                        score_breakdown=breakdown,
                        source_rule=f"buyhold:{kind}_ABCD+volume",
                        label_ru=PATTERN_LABELS_RU[kind],
                        direction="bullish" if bullish else "bearish",
                        volume_contracted=vol_c,
                        volume_breakout=vol_b,
                        volume_score=vol_s,
                        entry_mode=entry_mode,
                        psychology_note=psycho,
                    )
                )
    return _suppress_overlaps(out)[:1]


def _pre_pattern_impulse(
    bars: list[KlineBar],
    pattern_start: int,
    *,
    lookback: int = 20,
) -> float:
    """Высота импульса до фигуры (для клина: цель ≈ импульс до входа в клин)."""
    if pattern_start <= 2:
        return 0.0
    start = max(0, pattern_start - lookback)
    seg = bars[start:pattern_start]
    if len(seg) < 3:
        return 0.0
    return max(b.high for b in seg) - min(b.low for b in seg)


def _detect_triangles_wedges(
    swings: list[SwingPoint],
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    """Треугольники A-B-C-D и клинья по BuyHold.
    Цель треугольника = ширина основания от точки пробоя.
    Нисходящий клин на бычьем рынке = продолжение вверх; цель ≈ импульс до клина.
    """
    out: list[ChartPattern] = []
    if len(swings) < TRIANGLE_MIN_SWINGS:
        return out
    recent = swings[-6:]
    highs = [s for s in recent if s.kind == "high"]
    lows = [s for s in recent if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return out

    # A/C = highs, B/D = lows (как на картинках)
    a_high, c_high = highs[-2], highs[-1]
    b_low, d_low = lows[-2], lows[-1]
    span = max(c_high.index, d_low.index) - min(a_high.index, b_low.index)
    if span < 8:
        return out

    high_falling = c_high.price < a_high.price * 0.999
    low_rising = d_low.price > b_low.price * 1.001
    high_flat = _pct_diff(a_high.price, c_high.price) < 0.45
    low_flat = _pct_diff(b_low.price, d_low.price) < 0.45
    high_rising = c_high.price > a_high.price * 1.001
    low_falling = d_low.price < b_low.price * 0.999

    upper = PatternLine(a_high.index, a_high.price, c_high.index, c_high.price, "upper_bound")
    lower = PatternLine(b_low.index, b_low.price, d_low.index, d_low.price, "lower_bound")
    # основание = вертикальная ширина в начале фигуры
    base_width = max(a_high.price, c_high.price) - min(b_low.price, d_low.price)
    if base_width < atr * 0.8:
        return out

    kind = ""
    direction = "neutral"
    if high_falling and low_rising:
        kind = "triangle_symmetric"
    elif low_rising and high_flat:
        kind = "triangle_ascending"
        direction = "bullish"
    elif high_falling and low_flat:
        kind = "triangle_descending"
        direction = "bearish"
    elif high_rising and low_rising:
        kind = "wedge_rising"
    elif high_falling and low_falling:
        kind = "wedge_falling"
    else:
        return out

    pattern_start = min(a_high.index, b_low.index)
    prior = _prior_trend_bias(bars, pattern_start)
    if kind == "wedge_rising":
        # медвежий рынок → продолжение вниз; бычий → разворот вниз
        subtype = "continuation" if prior == "bearish" else "reversal"
        direction = "bearish"
    elif kind == "wedge_falling":
        # бычий рынок → продолжение вверх (картинка); медвежий → разворот вверх
        subtype = "continuation" if prior == "bullish" else "reversal"
        direction = "bullish"
    else:
        subtype = "continuation"
        if kind == "triangle_symmetric" and prior in {"bullish", "bearish"}:
            direction = prior

    end_idx = max(c_high.index, d_low.index)
    upper_now = _line_value(upper, end_idx)
    lower_now = _line_value(lower, end_idx)
    impulse = _pre_pattern_impulse(bars, pattern_start)

    # клин: цель ≈ импульс до фигуры; треугольник: ширина основания от пробоя
    measure = impulse if kind.startswith("wedge") and impulse >= atr * 1.5 else base_width

    bullish_break = bars[-1].close > upper_now
    bearish_break = bars[-1].close < lower_now
    if bullish_break:
        status = "confirmed"
        breakout_px = upper_now
        target = breakout_px + measure * TARGET_TRIANGLE_FACTOR
        direction = "bullish"
    elif bearish_break:
        status = "confirmed"
        breakout_px = lower_now
        target = breakout_px - measure * TARGET_TRIANGLE_FACTOR
        direction = "bearish"
    else:
        status = "forming"
        if direction == "bullish" or (kind.startswith("wedge") and direction == "bullish"):
            target = upper_now + measure * TARGET_TRIANGLE_FACTOR
        elif direction == "bearish":
            target = lower_now - measure * TARGET_TRIANGLE_FACTOR
        else:
            target = upper_now + measure * TARGET_TRIANGLE_FACTOR

    conf, breakdown = _score_geometry(
        0.58,
        convergence=0.14,
        context=0.12 if kind.startswith("wedge") else 0.08,
        breakout=0.14 if status == "confirmed" else 0.0,
    )
    vol_c, vol_b, vol_s, vol_note = _volume_facts(
        bars,
        impulse_start=max(0, pattern_start - 10),
        impulse_end=pattern_start,
        body_start=pattern_start,
        body_end=end_idx,
        status=status,
    )
    if vol_s > 0:
        conf = min(1.0, conf + 0.06 * vol_s)
        breakdown["volume"] = round(0.06 * vol_s, 3)
    if conf < MIN_PATTERN_CONFIDENCE:
        return out
    label = PATTERN_LABELS_RU.get(kind, kind)
    if kind.startswith("wedge"):
        label = f"{label} ({'прод.' if subtype == 'continuation' else 'разв.'})"
    if kind == "wedge_rising":
        psycho = "восх. клин: обе линии вверх → чаще медвежий разворот/сброс слабых лонгов"
    elif kind == "wedge_falling":
        psycho = "нисх. клин: обе линии вниз → чаще бычий разворот/сброс слабых шортов"
    else:
        psycho = "треугольник: накопление → пробой по направлению"
    if subtype == "continuation":
        psycho += " · в контексте тренда = продолжение"
    if vol_note:
        psycho = f"{psycho} · {vol_note}"
    out.append(
        ChartPattern(
            kind=kind,
            subtype=subtype if kind.startswith("wedge") else "continuation",
            status=status,
            points=(
                PatternPoint(a_high.index, a_high.price, "A"),
                PatternPoint(b_low.index, b_low.price, "B"),
                PatternPoint(c_high.index, c_high.price, "C"),
                PatternPoint(d_low.index, d_low.price, "D"),
            ),
            lines=(upper, lower),
            zone_top=max(a_high.price, c_high.price, upper_now),
            zone_bottom=min(b_low.price, d_low.price, lower_now),
            neckline=None,
            pole_height=measure,
            target_price=target,
            stop_price=lower_now - atr * 0.2 if direction != "bearish" else upper_now + atr * 0.2,
            confidence=conf,
            score_breakdown=breakdown,
            source_rule=f"buyhold:{kind}_ABCD+volume",
            label_ru=label,
            direction=direction,
            volume_contracted=vol_c,
            volume_breakout=vol_b,
            volume_score=vol_s,
            entry_mode="breakout" if status == "confirmed" else "wait",
            psychology_note=psycho,
        )
    )
    return out


def _detect_false_breakout(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    """По картинкам BuyHold:
    1) пробой уровня → не удержались → reclaim;
    2) сильнее, если возвратный импульс поглощает пробойный;
    3) вход после обновления локального экстремума после reclaim;
    4) только у пика/дна движения.
    """
    out: list[ChartPattern] = []
    if len(bars) < 20 or len(swings) < 3:
        return out
    n = len(bars)
    swing_highs = [s for s in swings if s.kind == "high"]
    swing_lows = [s for s in swings if s.kind == "low"]
    if not swing_highs or not swing_lows:
        return out

    # --- ложный пробой поддержки (бычий) ---
    if _is_near_range_extreme(bars, side="bullish"):
        support = min(s.price for s in swing_lows[-4:])
        # ищем бар с low < support, затем close обратно выше
        fake_idx = None
        reclaim_idx = None
        for i in range(max(5, n - 18), n):
            if bars[i].low < support * 0.999:
                fake_idx = i
                break
        if fake_idx is not None:
            for j in range(fake_idx, min(n, fake_idx + 4)):
                if bars[j].close > support:
                    reclaim_idx = j
                    break
        if fake_idx is not None and reclaim_idx is not None:
            breakout_size = support - bars[fake_idx].low
            reclaim_body = abs(bars[reclaim_idx].close - bars[reclaim_idx].open)
            absorbed = reclaim_body >= breakout_size * 0.85
            # локальный max после reclaim — триггер Buy (картинка 3)
            post = bars[reclaim_idx: min(n, reclaim_idx + 8)]
            local_high = max(b.high for b in post) if post else bars[reclaim_idx].high
            local_high_idx = reclaim_idx + max(range(len(post)), key=lambda k: post[k].high) if post else reclaim_idx
            status = _status_from_break(bars, bullish=True, trigger=local_high)
            conf, breakdown = _score_geometry(
                0.62,
                reclaim=0.16,
                absorb=0.12 if absorbed else 0.04,
                peak=0.10,
                breakout=0.12 if status == "confirmed" else 0.0,
            )
            if conf >= MIN_PATTERN_CONFIDENCE:
                out.append(
                    ChartPattern(
                        kind="false_breakout",
                        subtype="reversal",
                        status=status,
                        points=(
                            PatternPoint(fake_idx, bars[fake_idx].low, "fake_low"),
                            PatternPoint(reclaim_idx, bars[reclaim_idx].close, "reclaim"),
                            PatternPoint(local_high_idx, local_high, "buy_trigger"),
                        ),
                        lines=(PatternLine(max(0, fake_idx - 8), support, n - 1, support, "level"),),
                        zone_top=support,
                        zone_bottom=bars[fake_idx].low,
                        neckline=PatternLine(max(0, fake_idx - 8), support, n - 1, support, "level"),
                        pole_height=breakout_size,
                        target_price=local_high + (local_high - bars[fake_idx].low),
                        stop_price=bars[fake_idx].low - atr * 0.2,
                        confidence=conf,
                        score_breakdown=breakdown,
                        source_rule="buyhold:false_breakout_support",
                        label_ru=PATTERN_LABELS_RU["false_breakout"] + " (подд.)",
                        direction="bullish",
                    )
                )

    # --- ложный пробой сопротивления (медвежий) ---
    if _is_near_range_extreme(bars, side="bearish"):
        resistance = max(s.price for s in swing_highs[-4:])
        fake_idx = None
        reclaim_idx = None
        for i in range(max(5, n - 18), n):
            if bars[i].high > resistance * 1.001:
                fake_idx = i
                break
        if fake_idx is not None:
            for j in range(fake_idx, min(n, fake_idx + 4)):
                if bars[j].close < resistance:
                    reclaim_idx = j
                    break
        if fake_idx is not None and reclaim_idx is not None:
            breakout_size = bars[fake_idx].high - resistance
            reclaim_body = abs(bars[reclaim_idx].close - bars[reclaim_idx].open)
            absorbed = reclaim_body >= breakout_size * 0.85
            post = bars[reclaim_idx: min(n, reclaim_idx + 8)]
            local_low = min(b.low for b in post) if post else bars[reclaim_idx].low
            local_low_idx = reclaim_idx + min(range(len(post)), key=lambda k: post[k].low) if post else reclaim_idx
            status = _status_from_break(bars, bullish=False, trigger=local_low)
            conf, breakdown = _score_geometry(
                0.62,
                reclaim=0.16,
                absorb=0.12 if absorbed else 0.04,
                peak=0.10,
                breakout=0.12 if status == "confirmed" else 0.0,
            )
            if conf >= MIN_PATTERN_CONFIDENCE:
                out.append(
                    ChartPattern(
                        kind="false_breakout",
                        subtype="reversal",
                        status=status,
                        points=(
                            PatternPoint(fake_idx, bars[fake_idx].high, "fake_high"),
                            PatternPoint(reclaim_idx, bars[reclaim_idx].close, "reclaim"),
                            PatternPoint(local_low_idx, local_low, "sell_trigger"),
                        ),
                        lines=(PatternLine(max(0, fake_idx - 8), resistance, n - 1, resistance, "level"),),
                        zone_top=bars[fake_idx].high,
                        zone_bottom=resistance,
                        neckline=PatternLine(max(0, fake_idx - 8), resistance, n - 1, resistance, "level"),
                        pole_height=breakout_size,
                        target_price=local_low - (bars[fake_idx].high - local_low),
                        stop_price=bars[fake_idx].high + atr * 0.2,
                        confidence=conf,
                        score_breakdown=breakdown,
                        source_rule="buyhold:false_breakout_resistance",
                        label_ru=PATTERN_LABELS_RU["false_breakout"] + " (сопр.)",
                        direction="bearish",
                    )
                )
    return out[:1]


def _detect_one_two_three(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    """Сперандео по картинке:
    Бычий после даунтренда: т.1 = коррекционный High, т.2 = не обновили Low, вход = пробой т.1.
    Медвежий зеркально.
    """
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return out
    n = len(bars)

    # Бычий 1-2-3 (после даунтренда)
    for i in range(len(highs) - 1):
        p1 = highs[i]
        if _prior_trend_bias(bars, p1.index, lookback=20) != "bearish":
            continue
        prior_lows = [l for l in lows if l.index < p1.index]
        if not prior_lows:
            continue
        abs_low = min(prior_lows, key=lambda s: s.price)
        later_lows = [l for l in lows if l.index > p1.index]
        if not later_lows:
            continue
        p2 = later_lows[0]
        # не удалось обновить минимум всего движения
        if p2.price <= abs_low.price * 1.001:
            continue
        if p2.index - p1.index < 3:
            continue
        trigger = p1.price
        status = _status_from_break(bars, bullish=True, trigger=trigger)
        # стоп: за т.2 (агрессивно) или за abs low (консервативно) — берём т.2
        stop = p2.price - atr * 0.15
        target = trigger + (trigger - p2.price)
        conf, breakdown = _score_geometry(
            0.62,
            structure=0.16,
            failed_low=0.14,
            context=0.08,
            breakout=0.14 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="one_two_three",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(p1.index, p1.price, "point1"),
                    PatternPoint(p2.index, p2.price, "point2"),
                    PatternPoint(n - 1, bars[-1].close, "point3"),
                ),
                lines=(PatternLine(p1.index, trigger, n - 1, trigger, "trigger"),),
                zone_top=trigger,
                zone_bottom=p2.price,
                neckline=PatternLine(p1.index, trigger, n - 1, trigger, "trigger"),
                pole_height=trigger - p2.price,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:one_two_three_bull",
                label_ru=PATTERN_LABELS_RU["one_two_three"] + " ↑",
                direction="bullish",
            )
        )
        break

    # Медвежий 1-2-3 (после аптренда)
    for i in range(len(lows) - 1):
        p1 = lows[i]
        if _prior_trend_bias(bars, p1.index, lookback=20) != "bullish":
            continue
        prior_highs = [h for h in highs if h.index < p1.index]
        if not prior_highs:
            continue
        abs_high = max(prior_highs, key=lambda s: s.price)
        later_highs = [h for h in highs if h.index > p1.index]
        if not later_highs:
            continue
        p2 = later_highs[0]
        if p2.price >= abs_high.price * 0.999:
            continue
        if p2.index - p1.index < 3:
            continue
        trigger = p1.price
        status = _status_from_break(bars, bullish=False, trigger=trigger)
        stop = p2.price + atr * 0.15
        target = trigger - (p2.price - trigger)
        conf, breakdown = _score_geometry(
            0.62,
            structure=0.16,
            failed_high=0.14,
            context=0.08,
            breakout=0.14 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="one_two_three",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(p1.index, p1.price, "point1"),
                    PatternPoint(p2.index, p2.price, "point2"),
                    PatternPoint(n - 1, bars[-1].close, "point3"),
                ),
                lines=(PatternLine(p1.index, trigger, n - 1, trigger, "trigger"),),
                zone_top=p2.price,
                zone_bottom=trigger,
                neckline=PatternLine(p1.index, trigger, n - 1, trigger, "trigger"),
                pole_height=p2.price - trigger,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:one_two_three_bear",
                label_ru=PATTERN_LABELS_RU["one_two_three"] + " ↓",
                direction="bearish",
            )
        )
        break
    return out[:1]


def _detect_expanding_triangle(
    swings: list[SwingPoint],
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    """Расходящийся треугольник (BuyHold).

    HH↑ + LL↓ у пика/дна → неопределённость → разворот.
    Отработка: импульсный пробой границы против тренда + предпочтительно ретест.
    """
    out: list[ChartPattern] = []
    from .pattern_specs import EXPANDING_MIN_SWINGS

    if len(swings) < EXPANDING_MIN_SWINGS or len(bars) < 16:
        return out
    recent = swings[-8:]
    highs = [s for s in recent if s.kind == "high"]
    lows = [s for s in recent if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return out
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    # расходятся: хаи растут, лои падают
    if not (h2.price > h1.price * 1.001 and l2.price < l1.price * 0.999):
        return out
    if h2.index - h1.index < 4 or l2.index - l1.index < 4:
        return out
    upper = PatternLine(h1.index, h1.price, h2.index, h2.price, "upper_bound")
    lower = PatternLine(l1.index, l1.price, l2.index, l2.price, "lower_bound")
    width = max(h1.price, h2.price) - min(l1.price, l2.price)
    if width < atr * 1.2:
        return out
    start_idx = min(h1.index, l1.index)
    end_idx = max(h2.index, l2.index)
    prior = _prior_trend_bias(bars, start_idx)
    if prior not in {"bullish", "bearish"}:
        return out

    # Живые границы на текущем баре
    last_i = len(bars) - 1
    upper_now = _line_value(upper, last_i)
    lower_now = _line_value(lower, last_i)
    close = bars[-1].close

    # Статья: на росте — пробой нижней; на падении — верхней
    break_bullish = prior == "bearish"  # пробой вверх → LONG
    boundary = upper_now if break_bullish else lower_now
    boundary_line = upper if break_bullish else lower
    direction = "bullish" if break_bullish else "bearish"

    beyond = (
        close > boundary * 1.0005 if break_bullish else close < boundary * 0.9995
    )

    # Импульсный пробой: ищем бар после формирования фигуры
    search_from = max(end_idx, last_i - 12)
    body_vol = _avg_volume(bars, start_idx, end_idx)
    impulse_ok = False
    vol_spike = False
    break_bar_i: int | None = None
    for i in range(search_from, last_i + 1):
        b = bars[i]
        bound_i = _line_value(boundary_line, i)
        closed_beyond = (
            b.close > bound_i * 1.0005 if break_bullish else b.close < bound_i * 0.9995
        )
        if not closed_beyond:
            continue
        body = abs(b.close - b.open)
        rng = b.high - b.low
        candle_impulse = body >= atr * 0.85 or rng >= atr * 1.15
        # направление свечи в сторону пробоя
        dir_ok = (b.close > b.open) if break_bullish else (b.close < b.open)
        vol = float(getattr(b, "volume", 0) or 0)
        spike = bool(body_vol > 0 and vol >= body_vol * VOLUME_BREAKOUT_SPIKE)
        if candle_impulse and dir_ok:
            impulse_ok = True
            break_bar_i = i
            if spike:
                vol_spike = True
            # берём самый свежий импульсный бар
    # если импульс был раньше, а сейчас цена за границей — ок
    if beyond and break_bar_i is None:
        # слабый уход за границу без импульсной свечи
        impulse_ok = False

    vol_c, vol_b_generic, vol_s, vol_note = _volume_facts(
        bars,
        impulse_start=max(0, start_idx - 8),
        impulse_end=start_idx,
        body_start=start_idx,
        body_end=end_idx,
        status="confirmed" if beyond else "forming",
    )
    volume_breakout = bool(vol_spike or (beyond and vol_b_generic and impulse_ok))

    # Статус / режим входа (BuyHold: импульс + ретест)
    status = "forming"
    entry_mode = "wait"
    if beyond and impulse_ok:
        status = "confirmed"
        # ретест: цена вернулась к пробитой границе
        retest_tol = 0.65  # %
        near_boundary = abs(close - boundary) / max(boundary, 1e-9) * 100.0 <= retest_tol
        # после пробоя был возврат к линии (high/low касался)
        retest_touch = False
        if break_bar_i is not None and break_bar_i < last_i:
            for j in range(break_bar_i + 1, last_i + 1):
                bj = bars[j]
                bnd = _line_value(boundary_line, j)
                if break_bullish:
                    # ретест сверху: low подходит к бывшей сопротивлению
                    if bj.low <= bnd * 1.004 and bj.close >= bnd * 0.997:
                        retest_touch = True
                        break
                else:
                    if bj.high >= bnd * 0.996 and bj.close <= bnd * 1.003:
                        retest_touch = True
                        break
        if near_boundary or retest_touch:
            entry_mode = "retest"
        else:
            entry_mode = "breakout"
    elif beyond and not impulse_ok:
        # за границей, но без импульса — не confirmed (как на скрине: ждём импульс)
        status = "forming"
        entry_mode = "wait"

    if break_bullish:
        target = (
            close + width * TARGET_TRIANGLE_FACTOR
            if status == "confirmed"
            else boundary + width * TARGET_TRIANGLE_FACTOR
        )
        stop = lower_now - atr * 0.25
    else:
        target = (
            close - width * TARGET_TRIANGLE_FACTOR
            if status == "confirmed"
            else boundary - width * TARGET_TRIANGLE_FACTOR
        )
        stop = upper_now + atr * 0.25

    conf, breakdown = _score_geometry(
        0.58,
        divergence=0.16,
        context=0.12,
        impulse=0.14 if impulse_ok else 0.0,
        volume=0.08 if volume_breakout else (0.04 if vol_c else 0.0),
        breakout=0.12 if status == "confirmed" else 0.0,
        retest=0.08 if entry_mode == "retest" else 0.0,
    )
    if conf < MIN_PATTERN_CONFIDENCE:
        return out

    # Psychology как в статье
    if prior == "bullish":
        psycho = (
            "линии расходятся у пика роста — неопределённость; "
            "разворот вниз после импульсного пробоя нижней границы"
        )
        if status == "forming" and not beyond:
            psycho = (
                "расходящийся треугольник на пике роста — ждать импульсный пробой "
                "нижней границы, вход предпочтительно на ретесте"
            )
        elif beyond and not impulse_ok:
            psycho = (
                "цена ниже нижней границы, но пробой не импульсный — "
                "BuyHold: не брать, ждать импульс/ретест"
            )
        elif entry_mode == "retest":
            psycho = (
                "импульсный пробой нижней границы → ретест пробитой поддержки "
                "как сопротивления — классический SHORT"
            )
        elif entry_mode == "breakout":
            psycho = (
                "импульсный пробой нижней границы подтверждён — "
                "консервативно ждать ретест, агрессивно от пробоя"
            )
    else:
        psycho = (
            "линии расходятся у дна падения — неопределённость; "
            "разворот вверх после импульсного пробоя верхней границы"
        )
        if status == "forming" and not beyond:
            psycho = (
                "расходящийся треугольник на дне — ждать импульсный пробой "
                "верхней границы, вход предпочтительно на ретесте"
            )
        elif beyond and not impulse_ok:
            psycho = (
                "цена выше верхней границы, но пробой не импульсный — "
                "ждать импульс/ретест"
            )
        elif entry_mode == "retest":
            psycho = (
                "импульсный пробой верхней границы → ретест — классический LONG"
            )
        elif entry_mode == "breakout":
            psycho = (
                "импульсный пробой верхней границы — "
                "консервативно ретест, агрессивно от пробоя"
            )
    if vol_note and entry_mode != "wait":
        psycho = f"{psycho} · {vol_note}"
    elif volume_breakout:
        psycho = f"{psycho} · объём на пробое ↑"

    # neckline = пробиваемая граница (для gate / foresight / отрисовки цели)
    neck = PatternLine(
        boundary_line.start_idx,
        boundary_line.start_price,
        last_i,
        boundary,
        "break_bound",
    )

    out.append(
        ChartPattern(
            kind="expanding_triangle",
            subtype="reversal",
            status=status,
            points=(
                PatternPoint(h1.index, h1.price, "high1"),
                PatternPoint(h2.index, h2.price, "high2"),
                PatternPoint(l1.index, l1.price, "low1"),
                PatternPoint(l2.index, l2.price, "low2"),
            ),
            lines=(upper, lower),
            zone_top=max(h1.price, h2.price, upper_now),
            zone_bottom=min(l1.price, l2.price, lower_now),
            neckline=neck,
            pole_height=width,
            target_price=target,
            stop_price=stop,
            confidence=conf,
            score_breakdown=breakdown,
            source_rule="buyhold:expanding_triangle+impulse+retest",
            label_ru=PATTERN_LABELS_RU["expanding_triangle"],
            direction=direction,
            volume_contracted=vol_c,
            volume_breakout=volume_breakout,
            volume_score=vol_s + (0.35 if volume_breakout else 0.0),
            entry_mode=entry_mode,
            psychology_note=psycho[:220],
        )
    )
    return out


def _quad_price(i0: int, p0: float, i1: int, p1: float, i2: int, p2: float, idx: int) -> float:
    """Квадратичная интерполяция цены по трём точкам."""
    if idx == i0:
        return p0
    if idx == i1:
        return p1
    if idx == i2:
        return p2
    d0 = (idx - i1) * (idx - i2)
    d1 = (idx - i0) * (idx - i2)
    d2 = (idx - i0) * (idx - i1)
    n0 = (i0 - i1) * (i0 - i2) or 1e-9
    n1 = (i1 - i0) * (i1 - i2) or 1e-9
    n2 = (i2 - i0) * (i2 - i1) or 1e-9
    return p0 * d0 / n0 + p1 * d1 / n1 + p2 * d2 / n2


def _detect_cup_with_handle(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    n = len(bars)
    if n < CUP_MIN_BARS + 6:
        return out

    lookback_start = max(0, n - int(n * 0.9))
    segment = bars[lookback_start:]
    if len(segment) < CUP_MIN_BARS:
        return out

    cup_bottom_idx = min(range(len(segment)), key=lambda j: segment[j].low)
    cup_bottom_i = lookback_start + cup_bottom_idx
    if cup_bottom_idx < 8 or cup_bottom_idx > len(segment) - 8:
        return out

    left_seg = segment[: cup_bottom_idx + 1]
    right_seg = segment[cup_bottom_idx:]
    if len(left_seg) < 6 or len(right_seg) < 8:
        return out

    left_rim_idx = lookback_start + max(range(len(left_seg)), key=lambda j: left_seg[j].high)
    right_rim_idx = lookback_start + cup_bottom_idx + max(range(len(right_seg)), key=lambda j: right_seg[j].high)
    if right_rim_idx <= left_rim_idx + 6:
        return out

    left_rim_price = bars[left_rim_idx].high
    right_rim_price = bars[right_rim_idx].high
    cup_bottom_price = bars[cup_bottom_i].low
    if _pct_diff(left_rim_price, right_rim_price) > CUP_RIM_TOLERANCE_PCT:
        return out

    rim_level = (left_rim_price + right_rim_price) / 2.0
    cup_depth = rim_level - cup_bottom_price
    if cup_depth < atr * 1.2:
        return out

    handle_end = min(n - 1, right_rim_idx + CUP_HANDLE_MAX_BARS)
    handle_bars = bars[right_rim_idx: handle_end + 1]
    if len(handle_bars) < 4:
        return out
    handle_low = min(b.low for b in handle_bars[1:])
    if handle_low <= cup_bottom_price * 1.001:
        return out

    handle_idx = right_rim_idx + 1 + min(
        range(len(handle_bars) - 1),
        key=lambda j: handle_bars[j + 1].low,
    )
    trigger = max(left_rim_price, right_rim_price)
    target = trigger + cup_depth * TARGET_HS_FACTOR
    stop = min(cup_bottom_price, handle_low) - atr * 0.25
    status = _status_from_break(bars, bullish=True, trigger=trigger)
    conf, breakdown = _score_geometry(
        0.56,
        roundness=0.14,
        handle=0.12,
        breakout=0.15 if status == "confirmed" else 0.0,
    )
    out.append(
        ChartPattern(
            kind="cup_handle",
            subtype="continuation",
            status=status,
            points=(
                PatternPoint(left_rim_idx, left_rim_price, "cup_left_rim"),
                PatternPoint(cup_bottom_i, cup_bottom_price, "cup_bottom"),
                PatternPoint(right_rim_idx, right_rim_price, "cup_right_rim"),
                PatternPoint(handle_idx, handle_low, "handle_low"),
            ),
            lines=(
                PatternLine(left_rim_idx, trigger, n - 1, trigger, "rim"),
            ),
            zone_top=trigger,
            zone_bottom=cup_bottom_price,
            neckline=PatternLine(left_rim_idx, trigger, n - 1, trigger, "rim"),
            pole_height=cup_depth,
            target_price=target,
            stop_price=stop,
            confidence=conf,
            score_breakdown=breakdown,
            source_rule="buyhold:cup_handle",
            label_ru=PATTERN_LABELS_RU["cup_handle"],
            direction="bullish",
        )
    )

    # Перевёрнутая чашка (медвежья) — зеркально по high/low сегмента
    cup_top_idx = max(range(len(segment)), key=lambda j: segment[j].high)
    cup_top_i = lookback_start + cup_top_idx
    if 8 <= cup_top_idx <= len(segment) - 8:
        left_seg_l = segment[: cup_top_idx + 1]
        right_seg_l = segment[cup_top_idx:]
        left_rim_l_idx = lookback_start + min(range(len(left_seg_l)), key=lambda j: left_seg_l[j].low)
        right_rim_l_idx = lookback_start + cup_top_idx + min(range(len(right_seg_l)), key=lambda j: right_seg_l[j].low)
        if right_rim_l_idx > left_rim_l_idx + 6:
            left_rim_l = bars[left_rim_l_idx].low
            right_rim_l = bars[right_rim_l_idx].low
            cup_top_price = bars[cup_top_i].high
            if _pct_diff(left_rim_l, right_rim_l) <= CUP_RIM_TOLERANCE_PCT:
                rim_l = (left_rim_l + right_rim_l) / 2.0
                cup_depth_inv = cup_top_price - rim_l
                if cup_depth_inv >= atr * 1.2:
                    handle_end_inv = min(n - 1, right_rim_l_idx + CUP_HANDLE_MAX_BARS)
                    handle_bars_inv = bars[right_rim_l_idx: handle_end_inv + 1]
                    if len(handle_bars_inv) >= 4:
                        handle_high = max(b.high for b in handle_bars_inv[1:])
                        if handle_high < cup_top_price * 0.999:
                            handle_idx_inv = right_rim_l_idx + 1 + max(
                                range(len(handle_bars_inv) - 1),
                                key=lambda j: handle_bars_inv[j + 1].high,
                            )
                            trigger_inv = min(left_rim_l, right_rim_l)
                            target_inv = trigger_inv - cup_depth_inv * TARGET_HS_FACTOR
                            stop_inv = max(cup_top_price, handle_high) + atr * 0.25
                            status_inv = _status_from_break(bars, bullish=False, trigger=trigger_inv)
                            conf_inv, breakdown_inv = _score_geometry(
                                0.56,
                                roundness=0.14,
                                handle=0.12,
                                breakout=0.15 if status_inv == "confirmed" else 0.0,
                            )
                            out.append(
                                ChartPattern(
                                    kind="inverse_cup_handle",
                                    subtype="continuation",
                                    status=status_inv,
                                    points=(
                                        PatternPoint(left_rim_l_idx, left_rim_l, "cup_left_rim"),
                                        PatternPoint(cup_top_i, cup_top_price, "cup_top"),
                                        PatternPoint(right_rim_l_idx, right_rim_l, "cup_right_rim"),
                                        PatternPoint(handle_idx_inv, handle_high, "handle_high"),
                                    ),
                                    lines=(
                                        PatternLine(left_rim_l_idx, trigger_inv, n - 1, trigger_inv, "rim"),
                                    ),
                                    zone_top=cup_top_price,
                                    zone_bottom=trigger_inv,
                                    neckline=PatternLine(left_rim_l_idx, trigger_inv, n - 1, trigger_inv, "rim"),
                                    pole_height=cup_depth_inv,
                                    target_price=target_inv,
                                    stop_price=stop_inv,
                                    confidence=conf_inv,
                                    score_breakdown=breakdown_inv,
                                    source_rule="buyhold:inverse_cup_handle",
                                    label_ru=PATTERN_LABELS_RU["inverse_cup_handle"],
                                    direction="bearish",
                                )
                            )
    return out


def _detect_rounded_saucer(
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    """Округлое дно / вершина (блюдце) — чаша без ручки по BuyHold."""
    out: list[ChartPattern] = []
    n = len(bars)
    if n < ROUNDED_MIN_BARS + 4:
        return out
    lookback_start = max(0, n - int(n * 0.85))
    segment = bars[lookback_start:]
    if len(segment) < ROUNDED_MIN_BARS:
        return out

    # --- Округлое дно ---
    bottom_idx = min(range(len(segment)), key=lambda j: segment[j].low)
    bottom_i = lookback_start + bottom_idx
    if 6 <= bottom_idx <= len(segment) - 5:
        left = segment[: bottom_idx + 1]
        right = segment[bottom_idx:]
        if len(left) >= 5 and len(right) >= 5:
            left_rim_i = lookback_start + max(range(len(left)), key=lambda j: left[j].high)
            right_rim_i = lookback_start + bottom_idx + max(
                range(len(right)), key=lambda j: right[j].high,
            )
            if right_rim_i > left_rim_i + 5:
                left_p = bars[left_rim_i].high
                right_p = bars[right_rim_i].high
                bottom_p = bars[bottom_i].low
                if _pct_diff(left_p, right_p) <= ROUNDED_RIM_TOLERANCE_PCT:
                    rim = (left_p + right_p) / 2.0
                    depth = rim - bottom_p
                    if depth >= atr * 1.1:
                        # плавность: середина не должна быть V-spike слишком острым
                        mid_span = bars[left_rim_i: right_rim_i + 1]
                        if mid_span:
                            mid_low = min(b.low for b in mid_span)
                            if mid_low <= bottom_p * 1.004:
                                trigger = max(left_p, right_p)
                                status = _status_from_break(bars, bullish=True, trigger=trigger)
                                target = trigger + depth * TARGET_HS_FACTOR
                                stop = bottom_p - atr * 0.25
                                conf, breakdown = _score_geometry(
                                    0.52,
                                    roundness=0.16,
                                    breakout=0.14 if status == "confirmed" else 0.0,
                                )
                                out.append(
                                    ChartPattern(
                                        kind="rounded_bottom",
                                        subtype="reversal",
                                        status=status,
                                        points=(
                                            PatternPoint(left_rim_i, left_p, "rim_left"),
                                            PatternPoint(bottom_i, bottom_p, "saucer_low"),
                                            PatternPoint(right_rim_i, right_p, "rim_right"),
                                        ),
                                        lines=(
                                            PatternLine(left_rim_i, trigger, n - 1, trigger, "rim"),
                                        ),
                                        zone_top=trigger,
                                        zone_bottom=bottom_p,
                                        neckline=PatternLine(left_rim_i, trigger, n - 1, trigger, "rim"),
                                        pole_height=depth,
                                        target_price=target,
                                        stop_price=stop,
                                        confidence=conf,
                                        score_breakdown=breakdown,
                                        source_rule="buyhold:rounded_bottom",
                                        label_ru=PATTERN_LABELS_RU["rounded_bottom"],
                                        direction="bullish",
                                        entry_mode="breakout" if status == "confirmed" else "wait",
                                        psychology_note="плавное дно без резких рывков — разворот после накопления",
                                    )
                                )

    # --- Округлая вершина ---
    top_idx = max(range(len(segment)), key=lambda j: segment[j].high)
    top_i = lookback_start + top_idx
    if 6 <= top_idx <= len(segment) - 5:
        left = segment[: top_idx + 1]
        right = segment[top_idx:]
        if len(left) >= 5 and len(right) >= 5:
            left_rim_i = lookback_start + min(range(len(left)), key=lambda j: left[j].low)
            right_rim_i = lookback_start + top_idx + min(
                range(len(right)), key=lambda j: right[j].low,
            )
            if right_rim_i > left_rim_i + 5:
                left_p = bars[left_rim_i].low
                right_p = bars[right_rim_i].low
                top_p = bars[top_i].high
                if _pct_diff(left_p, right_p) <= ROUNDED_RIM_TOLERANCE_PCT:
                    rim = (left_p + right_p) / 2.0
                    depth = top_p - rim
                    if depth >= atr * 1.1:
                        trigger = min(left_p, right_p)
                        status = _status_from_break(bars, bullish=False, trigger=trigger)
                        target = trigger - depth * TARGET_HS_FACTOR
                        stop = top_p + atr * 0.25
                        conf, breakdown = _score_geometry(
                            0.52,
                            roundness=0.16,
                            breakout=0.14 if status == "confirmed" else 0.0,
                        )
                        out.append(
                            ChartPattern(
                                kind="rounded_top",
                                subtype="reversal",
                                status=status,
                                points=(
                                    PatternPoint(left_rim_i, left_p, "rim_left"),
                                    PatternPoint(top_i, top_p, "saucer_high"),
                                    PatternPoint(right_rim_i, right_p, "rim_right"),
                                ),
                                lines=(
                                    PatternLine(left_rim_i, trigger, n - 1, trigger, "rim"),
                                ),
                                zone_top=top_p,
                                zone_bottom=trigger,
                                neckline=PatternLine(left_rim_i, trigger, n - 1, trigger, "rim"),
                                pole_height=depth,
                                target_price=target,
                                stop_price=stop,
                                confidence=conf,
                                score_breakdown=breakdown,
                                source_rule="buyhold:rounded_top",
                                label_ru=PATTERN_LABELS_RU["rounded_top"],
                                direction="bearish",
                                entry_mode="breakout" if status == "confirmed" else "wait",
                                psychology_note="плавная вершина — распределение, разворот вниз",
                            )
                        )
    return out


def _detect_baskerville(
    bars: list[KlineBar],
    hs_patterns: list[ChartPattern],
    atr: float,
) -> list[ChartPattern]:
    """Собака Баскервилей: ложная отработка ГиП с возвратом за шею."""
    out: list[ChartPattern] = []
    if len(bars) < 20:
        return out
    recent = bars[-25:]
    close = bars[-1].close

    for hs in hs_patterns:
        if hs.kind == "head_shoulders" and hs.neckline:
            neck = _line_value(hs.neckline, len(bars) - 1)
            pattern_high = hs.zone_top or max(p.price for p in hs.points)
            broke_down = any(b.close < neck * 0.998 for b in recent[:-2])
            reclaimed = close > neck * 1.001
            if not broke_down or not reclaimed:
                continue
            trigger = pattern_high
            status = "confirmed" if close >= trigger * 0.999 else "forming"
            target = trigger + (hs.pole_height or atr * 3) * TARGET_HS_FACTOR
            stop = neck - atr * 0.3
            conf, breakdown = _score_geometry(0.66, trap=0.18, reclaim=0.15, breakout=0.14 if status == "confirmed" else 0.0)
            if conf < MIN_PATTERN_CONFIDENCE:
                continue
            out.append(
                ChartPattern(
                    kind="baskerville_bullish",
                    subtype="reversal",
                    status=status,
                    points=hs.points + (PatternPoint(len(bars) - 1, close, "reclaim"),),
                    lines=hs.lines,
                    zone_top=pattern_high,
                    zone_bottom=neck,
                    neckline=hs.neckline,
                    pole_height=hs.pole_height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule="buyhold:baskerville",
                    label_ru=PATTERN_LABELS_RU["baskerville_bullish"],
                    direction="bullish",
                )
            )

        if hs.kind == "inverse_head_shoulders" and hs.neckline:
            neck = _line_value(hs.neckline, len(bars) - 1)
            pattern_low = hs.zone_bottom or min(p.price for p in hs.points)
            broke_up = any(b.close > neck * 1.002 for b in recent[:-2])
            reclaimed = close < neck * 0.999
            if not broke_up or not reclaimed:
                continue
            trigger = pattern_low
            status = "confirmed" if close <= trigger * 1.001 else "forming"
            target = trigger - (hs.pole_height or atr * 3) * TARGET_HS_FACTOR
            stop = neck + atr * 0.3
            conf, breakdown = _score_geometry(0.66, trap=0.18, reclaim=0.15, breakout=0.14 if status == "confirmed" else 0.0)
            if conf < MIN_PATTERN_CONFIDENCE:
                continue
            out.append(
                ChartPattern(
                    kind="baskerville_bearish",
                    # статья: после ложной перевёрнутой ГиП — продолжение нисходящего
                    subtype="continuation",
                    status=status,
                    points=hs.points + (PatternPoint(len(bars) - 1, close, "reclaim"),),
                    lines=hs.lines,
                    zone_top=neck,
                    zone_bottom=pattern_low,
                    neckline=hs.neckline,
                    pole_height=hs.pole_height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule="buyhold:baskerville",
                    label_ru=PATTERN_LABELS_RU["baskerville_bearish"],
                    direction="bearish",
                )
            )
    return out


def _detect_three_indians(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    """Строго по Рашке: H3 ≈ H1 + 1.272×(H2−H1) (и зеркально для лоев)."""
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    for i in range(len(highs) - 2):
        h1, h2, h3 = highs[i], highs[i + 1], highs[i + 2]
        if h2.index - h1.index < THREE_INDIANS_MIN_BARS or h3.index - h2.index < THREE_INDIANS_MIN_BARS:
            continue
        if h2.price <= h1.price:
            continue
        expected = h1.price + THREE_INDIANS_FIB * (h2.price - h1.price)
        if expected <= 0:
            continue
        # формула 1.272 ИЛИ касание трендовой H1→H2 (как на картинке)
        trend_proj = _line_value(PatternLine(h1.index, h1.price, h2.index, h2.price, "t"), h3.index)
        near_fib = abs(h3.price - expected) / expected * 100.0 <= THREE_INDIANS_TOLERANCE_PCT
        near_line = abs(h3.price - trend_proj) / max(trend_proj, 1e-9) * 100.0 <= THREE_INDIANS_TOLERANCE_PCT
        if not (near_fib or near_line):
            continue
        if h3.price <= h2.price * 0.998:
            continue
        if h3.price - h1.price < atr * 0.8:
            continue
        trend = PatternLine(h1.index, h1.price, h3.index, h3.price, "trend")
        target = h3.price - (h3.price - h1.price) * 0.618
        stop = h3.price + atr * 0.25
        status = "forming"
        if bars[-1].close < _line_value(trend, len(bars) - 1):
            status = "confirmed"
        fit = max(
            1.0 - abs(h3.price - expected) / expected,
            1.0 - abs(h3.price - trend_proj) / max(trend_proj, 1e-9),
        )
        conf, breakdown = _score_geometry(
            0.58,
            fib_fit=0.20 * max(0.0, fit),
            sequence=0.12,
            breakout=0.12 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="three_indians",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(h1.index, h1.price, "peak1"),
                    PatternPoint(h2.index, h2.price, "peak2"),
                    PatternPoint(h3.index, h3.price, "peak3"),
                ),
                lines=(trend,),
                zone_top=h3.price,
                zone_bottom=h1.price,
                neckline=trend,
                pole_height=h3.price - h1.price,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown={**breakdown, "expected_h3": round(expected, 8)},
                source_rule="buyhold:three_indians_fib_1.272",
                label_ru=PATTERN_LABELS_RU["three_indians"] + " ↓",
                direction="bearish",
            )
        )

    for i in range(len(lows) - 2):
        l1, l2, l3 = lows[i], lows[i + 1], lows[i + 2]
        if l2.index - l1.index < THREE_INDIANS_MIN_BARS or l3.index - l2.index < THREE_INDIANS_MIN_BARS:
            continue
        if l2.price >= l1.price:
            continue
        expected = l1.price - THREE_INDIANS_FIB * (l1.price - l2.price)
        if expected <= 0:
            continue
        trend_proj = _line_value(PatternLine(l1.index, l1.price, l2.index, l2.price, "t"), l3.index)
        near_fib = abs(l3.price - expected) / expected * 100.0 <= THREE_INDIANS_TOLERANCE_PCT
        near_line = abs(l3.price - trend_proj) / max(trend_proj, 1e-9) * 100.0 <= THREE_INDIANS_TOLERANCE_PCT
        if not (near_fib or near_line):
            continue
        if l3.price >= l2.price * 1.002:
            continue
        if l1.price - l3.price < atr * 0.8:
            continue
        trend = PatternLine(l1.index, l1.price, l3.index, l3.price, "trend")
        target = l3.price + (l1.price - l3.price) * 0.618
        stop = l3.price - atr * 0.25
        status = "forming"
        if bars[-1].close > _line_value(trend, len(bars) - 1):
            status = "confirmed"
        fit = max(
            1.0 - abs(l3.price - expected) / expected,
            1.0 - abs(l3.price - trend_proj) / max(trend_proj, 1e-9),
        )
        conf, breakdown = _score_geometry(
            0.58,
            fib_fit=0.20 * max(0.0, fit),
            sequence=0.12,
            breakout=0.12 if status == "confirmed" else 0.0,
        )
        if conf < MIN_PATTERN_CONFIDENCE:
            continue
        out.append(
            ChartPattern(
                kind="three_indians",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(l1.index, l1.price, "trough1"),
                    PatternPoint(l2.index, l2.price, "trough2"),
                    PatternPoint(l3.index, l3.price, "trough3"),
                ),
                lines=(trend,),
                zone_top=l1.price,
                zone_bottom=l3.price,
                neckline=trend,
                pole_height=l1.price - l3.price,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown={**breakdown, "expected_l3": round(expected, 8)},
                source_rule="buyhold:three_indians_fib_1.272",
                label_ru=PATTERN_LABELS_RU["three_indians"] + " ↑",
                direction="bullish",
            )
        )
    return out[-1:]


def _detect_diamond(
    swings: list[SwingPoint],
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swings) < DIAMOND_MIN_SWINGS:
        return out
    recent = swings[-8:]
    if len(recent) < DIAMOND_MIN_SWINGS:
        return out

    highs = [s for s in recent if s.kind == "high"]
    lows = [s for s in recent if s.kind == "low"]
    if len(highs) < 3 or len(lows) < 3:
        return out

    mid = len(recent) // 2
    first, second = recent[:mid], recent[mid:]
    fh = [s for s in first if s.kind == "high"]
    fl = [s for s in first if s.kind == "low"]
    sh = [s for s in second if s.kind == "high"]
    sl = [s for s in second if s.kind == "low"]
    if len(fh) < 2 or len(fl) < 2 or len(sh) < 2 or len(sl) < 2:
        return out

    expanding = fh[-1].price > fh[0].price and fl[-1].price < fl[0].price
    contracting = sh[-1].price < sh[0].price and sl[-1].price > sl[0].price
    if not expanding or not contracting:
        return out

    upper = PatternLine(fh[0].index, fh[0].price, sh[-1].index, sh[-1].price, "upper_bound")
    lower = PatternLine(fl[0].index, fl[0].price, sl[-1].index, sl[-1].price, "lower_bound")
    width = max(s.price for s in highs) - min(s.price for s in lows)
    if width < atr * 1.2:
        return out

    end_idx = recent[-1].index
    upper_now = _line_value(upper, end_idx)
    lower_now = _line_value(lower, end_idx)
    close = bars[-1].close
    if close < lower_now:
        direction = "bearish"
        status = "confirmed"
        target = close - width * TARGET_TRIANGLE_FACTOR
    elif close > upper_now:
        direction = "bullish"
        status = "confirmed"
        target = close + width * TARGET_TRIANGLE_FACTOR
    else:
        direction = "neutral"
        status = "forming"
        target = close + width * 0.5

    conf, breakdown = _score_geometry(0.58, expansion=0.14, contraction=0.14, breakout=0.12 if status == "confirmed" else 0.0)
    if conf < MIN_PATTERN_CONFIDENCE:
        return out
    out.append(
        ChartPattern(
            kind="diamond",
            subtype="reversal",
            status=status,
            points=tuple(PatternPoint(s.index, s.price, s.kind) for s in recent[-4:]),
            lines=(upper, lower),
            zone_top=max(s.price for s in highs),
            zone_bottom=min(s.price for s in lows),
            neckline=None,
            pole_height=width,
            target_price=target,
            stop_price=upper_now + atr * 0.2 if direction == "bearish" else lower_now - atr * 0.2,
            confidence=conf,
            score_breakdown=breakdown,
            source_rule="buyhold:diamond",
            label_ru=PATTERN_LABELS_RU["diamond"],
            direction=direction,
        )
    )
    return out


def pattern_location_ok(
    pattern: ChartPattern | None,
    *,
    side: str,
    price: float,
    tol_pct: float = 0.75,
) -> bool:
    """Цена у границы/шеи; confirmed предпочтительнее; без объёма на пробое — жёстче."""
    if pattern is None or pattern.confidence < MIN_TRADE_PATTERN_CONFIDENCE:
        return False
    side = side.lower()
    if side == "long" and pattern.direction == "bearish":
        return False
    if side == "short" and pattern.direction == "bullish":
        return False
    if pattern.status != "confirmed":
        # forming — не локация для ENTRY (только WATCH на уровне)
        return False
    want = "bullish" if side == "long" else "bearish"
    if pattern.direction != want:
        return False
    # Флаг/вымпел/клин/расходящийся △: без всплеска объёма на пробое требуем ближе к уровню
    need_tight = (
        pattern.kind in {
            "flag", "pennant", "wedge_rising", "wedge_falling",
            "triple_bottom", "triple_top", "expanding_triangle",
        }
        and not pattern.volume_breakout
    )
    # BuyHold: расходящийся △ — ENTRY только после импульса; wait запрещён
    if pattern.kind == "expanding_triangle":
        if (pattern.entry_mode or "") == "wait":
            return False
        if not pattern.volume_breakout and (pattern.entry_mode or "") != "retest":
            # без объёма/импульса — только если уже на ретесте у границы
            need_tight = True
    use_tol = tol_pct * (0.55 if need_tight else 1.0)
    if pattern.kind == "expanding_triangle" and (pattern.entry_mode or "") == "retest":
        use_tol = min(use_tol, 0.70)

    levels: list[float] = []
    if pattern.neckline:
        levels.append(_line_value(pattern.neckline, pattern.neckline.end_idx))
    if pattern.zone_top is not None:
        levels.append(pattern.zone_top)
    if pattern.zone_bottom is not None:
        levels.append(pattern.zone_bottom)
    for lv in levels:
        if lv <= 0:
            continue
        if abs(price - lv) / lv * 100.0 <= use_tol:
            return True
    # confirmed breakout: допускаем вход чуть за шеей в сторону пробоя
    if pattern.neckline and pattern.status == "confirmed":
        neck = _line_value(pattern.neckline, pattern.neckline.end_idx)
        if side == "long" and price >= neck * 0.998 and price <= neck * 1.012:
            return True
        if side == "short" and price <= neck * 1.002 and price >= neck * 0.988:
            return True
    return False


def _detect_triple_top_bottom(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    """Тройное дно / тройная вершина по классике материалов.

    Дно: предшествующий downtrend, 3≈равных лоя, сопротивление между ними,
    объём часто падает к 3-му дну, пробой сопротивления вверх → цель = высота.
    """
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    # --- Тройное дно ---
    if len(lows) >= 3 and highs:
        for i in range(len(lows) - 2):
            l1, l2, l3 = lows[i], lows[i + 1], lows[i + 2]
            if l2.index - l1.index < TRIPLE_MIN_BARS_BETWEEN:
                continue
            if l3.index - l2.index < TRIPLE_MIN_BARS_BETWEEN:
                continue
            if l3.index - l1.index > TRIPLE_MAX_SPAN_BARS:
                continue
            if max(
                _pct_diff(l1.price, l2.price),
                _pct_diff(l2.price, l3.price),
                _pct_diff(l1.price, l3.price),
            ) > TRIPLE_EXTREMUM_TOLERANCE_PCT:
                continue
            if _prior_trend_bias(bars, l1.index) != "bearish":
                continue
            peaks = [s for s in highs if l1.index < s.index < l3.index]
            if len(peaks) < 2:
                continue
            resist = max(p.price for p in peaks)
            support = (l1.price + l2.price + l3.price) / 3.0
            height = resist - support
            if height < atr * 1.0:
                continue
            status = _status_from_break(bars, bullish=True, trigger=resist)
            # объём: сравниваем окна вокруг трёх доньев
            v1 = _avg_volume(bars, max(0, l1.index - 1), l1.index + 1)
            v3 = _avg_volume(bars, max(0, l3.index - 1), l3.index + 1)
            vol_fade = bool(v1 > 0 and v3 > 0 and v3 <= v1 * VOLUME_CONTRACT_RATIO)
            vol_c, vol_b, vol_s, vol_note = _volume_facts(
                bars,
                impulse_start=max(0, l1.index - 12),
                impulse_end=l1.index,
                body_start=l1.index,
                body_end=l3.index,
                status=status,
            )
            vol_c = vol_c or vol_fade
            conf, breakdown = _score_geometry(
                0.56,
                symmetry=0.14,
                spacing=0.10,
                context=0.10,
                volume=0.10 * (0.5 + 0.5 * vol_s + (0.2 if vol_fade else 0)),
                breakout=0.14 if status == "confirmed" else 0.0,
                vol_confirm=0.08 if (status == "confirmed" and vol_b) else 0.0,
            )
            if conf < MIN_PATTERN_CONFIDENCE:
                continue
            target = resist + height * TARGET_HS_FACTOR
            stop = support - atr * 0.2
            # консервативный ретест: после пробоя цена у шеи
            px = bars[-1].close
            entry_mode = "wait"
            if status == "confirmed":
                if abs(px - resist) / resist * 100.0 <= 0.6:
                    entry_mode = "retest"
                else:
                    entry_mode = "breakout"
            psycho = (
                "разворот down→up: 3 равных дна, крупные трясут пассажиров у поддержки"
            )
            if vol_note:
                psycho = f"{psycho} · {vol_note}"
            out.append(
                ChartPattern(
                    kind="triple_bottom",
                    subtype="reversal",
                    status=status,
                    points=(
                        PatternPoint(l1.index, l1.price, "trough1"),
                        PatternPoint(l2.index, l2.price, "trough2"),
                        PatternPoint(l3.index, l3.price, "trough3"),
                        PatternPoint(peaks[-1].index, resist, "resistance"),
                    ),
                    lines=(
                        PatternLine(l1.index, support, l3.index, support, "support"),
                        PatternLine(peaks[0].index, resist, peaks[-1].index, resist, "neckline"),
                    ),
                    zone_top=resist,
                    zone_bottom=support,
                    neckline=PatternLine(peaks[0].index, resist, peaks[-1].index, resist, "neckline"),
                    pole_height=height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule="classic:triple_bottom+volume",
                    label_ru=PATTERN_LABELS_RU["triple_bottom"],
                    direction="bullish",
                    volume_contracted=vol_c,
                    volume_breakout=vol_b,
                    volume_score=vol_s,
                    entry_mode=entry_mode,
                    psychology_note=psycho,
                )
            )

    # --- Тройная вершина (зеркало) ---
    if len(highs) >= 3 and lows:
        for i in range(len(highs) - 2):
            h1, h2, h3 = highs[i], highs[i + 1], highs[i + 2]
            if h2.index - h1.index < TRIPLE_MIN_BARS_BETWEEN:
                continue
            if h3.index - h2.index < TRIPLE_MIN_BARS_BETWEEN:
                continue
            if h3.index - h1.index > TRIPLE_MAX_SPAN_BARS:
                continue
            if max(
                _pct_diff(h1.price, h2.price),
                _pct_diff(h2.price, h3.price),
                _pct_diff(h1.price, h3.price),
            ) > TRIPLE_EXTREMUM_TOLERANCE_PCT:
                continue
            if _prior_trend_bias(bars, h1.index) != "bullish":
                continue
            valleys = [s for s in lows if h1.index < s.index < h3.index]
            if len(valleys) < 2:
                continue
            support = min(v.price for v in valleys)
            resist = (h1.price + h2.price + h3.price) / 3.0
            height = resist - support
            if height < atr * 1.0:
                continue
            status = _status_from_break(bars, bullish=False, trigger=support)
            v1 = _avg_volume(bars, max(0, h1.index - 1), h1.index + 1)
            v3 = _avg_volume(bars, max(0, h3.index - 1), h3.index + 1)
            vol_fade = bool(v1 > 0 and v3 > 0 and v3 <= v1 * VOLUME_CONTRACT_RATIO)
            vol_c, vol_b, vol_s, vol_note = _volume_facts(
                bars,
                impulse_start=max(0, h1.index - 12),
                impulse_end=h1.index,
                body_start=h1.index,
                body_end=h3.index,
                status=status,
            )
            vol_c = vol_c or vol_fade
            conf, breakdown = _score_geometry(
                0.56,
                symmetry=0.14,
                spacing=0.10,
                context=0.10,
                volume=0.10 * (0.5 + 0.5 * vol_s + (0.2 if vol_fade else 0)),
                breakout=0.14 if status == "confirmed" else 0.0,
                vol_confirm=0.08 if (status == "confirmed" and vol_b) else 0.0,
            )
            if conf < MIN_PATTERN_CONFIDENCE:
                continue
            target = support - height * TARGET_HS_FACTOR
            stop = resist + atr * 0.2
            px = bars[-1].close
            entry_mode = "wait"
            if status == "confirmed":
                if abs(px - support) / support * 100.0 <= 0.6:
                    entry_mode = "retest"
                else:
                    entry_mode = "breakout"
            psycho = "разворот up→down: 3 равных вершины, выброс слабых лонгов у сопротивления"
            if vol_note:
                psycho = f"{psycho} · {vol_note}"
            out.append(
                ChartPattern(
                    kind="triple_top",
                    subtype="reversal",
                    status=status,
                    points=(
                        PatternPoint(h1.index, h1.price, "peak1"),
                        PatternPoint(h2.index, h2.price, "peak2"),
                        PatternPoint(h3.index, h3.price, "peak3"),
                        PatternPoint(valleys[-1].index, support, "support"),
                    ),
                    lines=(
                        PatternLine(h1.index, resist, h3.index, resist, "resistance"),
                        PatternLine(valleys[0].index, support, valleys[-1].index, support, "neckline"),
                    ),
                    zone_top=resist,
                    zone_bottom=support,
                    neckline=PatternLine(valleys[0].index, support, valleys[-1].index, support, "neckline"),
                    pole_height=height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule="classic:triple_top+volume",
                    label_ru=PATTERN_LABELS_RU["triple_top"],
                    direction="bearish",
                    volume_contracted=vol_c,
                    volume_breakout=vol_b,
                    volume_score=vol_s,
                    entry_mode=entry_mode,
                    psychology_note=psycho,
                )
            )
    return _suppress_overlaps(out)[:1]


def detect_chart_patterns(
    bars: list[KlineBar],
    *,
    min_confidence: float = MIN_PATTERN_CONFIDENCE,
    enabled: bool = True,
    max_patterns: int | None = None,
) -> list[ChartPattern]:
    """Строгий поиск: только фигуры выше порога, без пересекающихся дублей."""
    if not enabled or len(bars) < 24:
        return []
    swings = find_pattern_swings(bars)
    atr = compute_atr(bars)
    found: list[ChartPattern] = []
    hs_patterns = _detect_head_shoulders(bars, swings, atr)
    found.extend(_detect_double_top_bottom(bars, swings, atr))
    found.extend(_detect_triple_top_bottom(bars, swings, atr))
    found.extend(hs_patterns)
    found.extend(_detect_baskerville(bars, hs_patterns, atr))
    found.extend(_detect_three_indians(bars, swings, atr))
    found.extend(_detect_diamond(swings, bars, atr))
    found.extend(_detect_flag_pennant(bars, atr))
    found.extend(_detect_triangles_wedges(swings, bars, atr))
    found.extend(_detect_expanding_triangle(swings, bars, atr))
    found.extend(_detect_rectangle(bars, atr))
    found.extend(_detect_false_breakout(bars, swings, atr))
    found.extend(_detect_one_two_three(bars, swings, atr))
    if CUP_ENABLED:
        found.extend(_detect_cup_with_handle(bars, swings, atr))
        found.extend(_detect_rounded_saucer(bars, atr))

    limit = MAX_REPORT_PATTERNS if max_patterns is None else max(1, int(max_patterns))
    strong = [p for p in found if p.confidence >= min_confidence]
    return _suppress_overlaps(strong)[:limit]


def pick_primary_pattern(patterns: list[ChartPattern]) -> ChartPattern | None:
    if not patterns:
        return None
    # приоритет: confirmed → выше confidence → свежее
    return max(
        patterns,
        key=lambda p: (
            1 if p.status == "confirmed" else 0,
            p.confidence,
            p.points[-1].index if p.points else 0,
        ),
    )


def format_chart_pattern_compact(pattern: ChartPattern | None) -> str:
    if not pattern:
        return ""
    status = "✓" if pattern.status == "confirmed" else "…"
    target = f" → {pattern.target_price:.5g}" if pattern.target_price else ""
    return f"{pattern.label_ru} {status}{target}"
