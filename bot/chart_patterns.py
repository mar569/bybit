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
    FLAG_POLE_MAX_BARS,
    FLAG_POLE_MIN_BARS,
    CUP_HANDLE_MAX_BARS,
    CUP_MIN_BARS,
    CUP_RIM_TOLERANCE_PCT,
    DIAMOND_MIN_SWINGS,
    HEAD_SHOULDER_TOLERANCE_PCT,
    PATTERN_LABELS_RU,
    TARGET_HS_FACTOR,
    TARGET_POLE_FACTOR,
    TARGET_TRIANGLE_FACTOR,
    THREE_INDIANS_MIN_BARS,
    TRIANGLE_MIN_SWINGS,
    WEDGE_MIN_SWINGS,
    MIN_PATTERN_CONFIDENCE,
    MIN_TRADE_PATTERN_CONFIDENCE,
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


def _score_geometry(base: float, **parts: float) -> tuple[float, dict[str, float]]:
    breakdown = {k: round(v, 3) for k, v in parts.items()}
    total = base * 0.35 + sum(parts.values())
    return min(1.0, max(0.0, total)), breakdown


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
            if _pct_diff(h1.price, h2.price) > DOUBLE_EXTREMUM_TOLERANCE_PCT:
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
                0.55,
                symmetry=0.18 if _pct_diff(h1.price, h2.price) < 0.35 else 0.10,
                spacing=0.12,
                neckline=0.10,
                breakout=0.15 if status == "confirmed" else 0.0,
            )
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
            if _pct_diff(l1.price, l2.price) > DOUBLE_EXTREMUM_TOLERANCE_PCT:
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
                0.55,
                symmetry=0.18 if _pct_diff(l1.price, l2.price) < 0.35 else 0.10,
                spacing=0.12,
                neckline=0.10,
                breakout=0.15 if status == "confirmed" else 0.0,
            )
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
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 3 or len(lows) < 2:
        return out

    for i in range(len(highs) - 2):
        ls, head, rs = highs[i], highs[i + 1], highs[i + 2]
        if head.price <= ls.price or head.price <= rs.price:
            continue
        if _pct_diff(ls.price, rs.price) > HEAD_SHOULDER_TOLERANCE_PCT:
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
        if height < atr:
            continue
        target = neck_now - height * TARGET_HS_FACTOR
        stop = head.price + atr * 0.2
        status = _status_from_break(bars, bullish=False, trigger=neck_now)
        conf, breakdown = _score_geometry(
            0.58,
            shoulders=0.15 if _pct_diff(ls.price, rs.price) < 1.5 else 0.08,
            head=0.12,
            neckline=0.10,
            breakout=0.15 if status == "confirmed" else 0.0,
        )
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
                source_rule="buyhold:head_shoulders",
                label_ru=PATTERN_LABELS_RU["head_shoulders"],
                direction="bearish",
            )
        )

    for i in range(len(lows) - 2):
        ls, head, rs = lows[i], lows[i + 1], lows[i + 2]
        if head.price >= ls.price or head.price >= rs.price:
            continue
        if _pct_diff(ls.price, rs.price) > HEAD_SHOULDER_TOLERANCE_PCT:
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
        if height < atr:
            continue
        target = neck_now + height * TARGET_HS_FACTOR
        stop = head.price - atr * 0.2
        status = _status_from_break(bars, bullish=True, trigger=neck_now)
        conf, breakdown = _score_geometry(
            0.58,
            shoulders=0.15 if _pct_diff(ls.price, rs.price) < 1.5 else 0.08,
            head=0.12,
            neckline=0.10,
            breakout=0.15 if status == "confirmed" else 0.0,
        )
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
                source_rule="buyhold:inverse_head_shoulders",
                label_ru=PATTERN_LABELS_RU["inverse_head_shoulders"],
                direction="bullish",
            )
        )
    return out


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
    out: list[ChartPattern] = []
    n = len(bars)
    if n < 20:
        return out

    search_from = max(10, n - 80)
    for pole_start in range(search_from, n - FLAG_BODY_MIN_BARS - FLAG_POLE_MIN_BARS):
        for pole_len in range(FLAG_POLE_MIN_BARS, FLAG_POLE_MAX_BARS + 1):
            pole_end = pole_start + pole_len
            if pole_end >= n - FLAG_BODY_MIN_BARS:
                break
            pole_move = bars[pole_end].close - bars[pole_start].open
            if abs(pole_move) < atr * 2.2:
                continue
            bullish = pole_move > 0
            body_end = min(n - 1, pole_end + FLAG_BODY_MAX_BARS)
            bounds = _fit_bounds(bars, pole_end, body_end)
            if not bounds:
                continue
            _, _, top, bottom = bounds
            body_height = top - bottom
            if body_height > abs(pole_move) * 0.65 or body_height < atr * 0.25:
                continue
            if body_end - pole_end < FLAG_BODY_MIN_BARS:
                continue

            upper = PatternLine(pole_end, top, body_end, top * 0.55 + bottom * 0.45, "upper_bound")
            lower = PatternLine(pole_end, bottom, body_end, top * 0.45 + bottom * 0.55, "lower_bound")
            slope_top = (upper.end_price - upper.start_price) / max(1, upper.end_idx - upper.start_idx)
            slope_bot = (lower.end_price - lower.start_price) / max(1, lower.end_idx - lower.start_idx)
            converging = abs(slope_top - slope_bot) > atr * 0.02
            kind = "pennant" if converging else "flag"
            if bullish and slope_top > 0.0001:
                continue
            if not bullish and slope_top < -0.0001:
                continue

            pole_height = abs(pole_move)
            trigger = top if bullish else bottom
            target = (
                trigger + pole_height * TARGET_POLE_FACTOR
                if bullish
                else trigger - pole_height * TARGET_POLE_FACTOR
            )
            stop = bottom - atr * 0.2 if bullish else top + atr * 0.2
            status = _status_from_break(bars, bullish=bullish, trigger=trigger)
            conf, breakdown = _score_geometry(
                0.52,
                pole=0.18,
                channel=0.12,
                breakout=0.15 if status == "confirmed" else 0.0,
            )
            out.append(
                ChartPattern(
                    kind=kind,
                    subtype="continuation",
                    status=status,
                    points=(
                        PatternPoint(pole_start, bars[pole_start].open, "pole_start"),
                        PatternPoint(pole_end, bars[pole_end].close, "pole_end"),
                        PatternPoint(body_end, trigger, "break"),
                    ),
                    lines=(upper, lower),
                    zone_top=top,
                    zone_bottom=bottom,
                    neckline=None,
                    pole_height=pole_height,
                    target_price=target,
                    stop_price=stop,
                    confidence=conf,
                    score_breakdown=breakdown,
                    source_rule=f"buyhold:{kind}",
                    label_ru=PATTERN_LABELS_RU[kind],
                    direction="bullish" if bullish else "bearish",
                )
            )
    return out[-3:]


def _detect_triangles_wedges(
    swings: list[SwingPoint],
    bars: list[KlineBar],
    atr: float,
) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swings) < TRIANGLE_MIN_SWINGS:
        return out
    recent = swings[-6:]
    highs = [s for s in recent if s.kind == "high"]
    lows = [s for s in recent if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return out

    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    span = max(h2.index, l2.index) - min(h1.index, l1.index)
    if span < 8:
        return out

    high_falling = h2.price < h1.price * 0.999
    low_rising = l2.price > l1.price * 1.001
    high_flat = _pct_diff(h1.price, h2.price) < 0.45
    low_flat = _pct_diff(l1.price, l2.price) < 0.45

    upper = PatternLine(h1.index, h1.price, h2.index, h2.price, "upper_bound")
    lower = PatternLine(l1.index, l1.price, l2.index, l2.price, "lower_bound")
    base_width = max(h1.price, h2.price) - min(l1.price, l2.price)
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
    else:
        same_slope_up = h2.price > h1.price and l2.price > l1.price
        same_slope_down = h2.price < h1.price and l2.price < l1.price
        if same_slope_up:
            kind = "wedge_rising"
        elif same_slope_down:
            kind = "wedge_falling"
        else:
            return out

    end_idx = max(h2.index, l2.index)
    upper_now = _line_value(upper, end_idx)
    lower_now = _line_value(lower, end_idx)
    trigger_up = upper_now
    trigger_down = lower_now
    bullish_break = bars[-1].close > trigger_up
    bearish_break = bars[-1].close < trigger_down
    if bullish_break:
        status = "confirmed"
        target = bars[-1].close + base_width * TARGET_TRIANGLE_FACTOR
        direction = "bullish"
    elif bearish_break:
        status = "confirmed"
        target = bars[-1].close - base_width * TARGET_TRIANGLE_FACTOR
        direction = "bearish"
    else:
        status = "forming"
        target = (
            upper_now + base_width * TARGET_TRIANGLE_FACTOR
            if direction == "bullish"
            else lower_now - base_width * TARGET_TRIANGLE_FACTOR
        )

    conf, breakdown = _score_geometry(
        0.50,
        convergence=0.15,
        swings=0.10,
        breakout=0.15 if status == "confirmed" else 0.0,
    )
    out.append(
        ChartPattern(
            kind=kind,
            subtype="continuation" if kind.startswith("triangle") else "reversal",
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
            neckline=None,
            pole_height=base_width,
            target_price=target,
            stop_price=lower_now - atr * 0.2 if direction != "bearish" else upper_now + atr * 0.2,
            confidence=conf,
            score_breakdown=breakdown,
            source_rule=f"buyhold:{kind}",
            label_ru=PATTERN_LABELS_RU.get(kind, kind),
            direction=direction,
        )
    )
    return out


def _detect_false_breakout(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(bars) < 15 or len(swings) < 3:
        return out
    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]
    if not highs or not lows:
        return out
    resistance = max(highs[-3:])
    support = min(lows[-3:])
    lookback = bars[-12:]
    max_h = max(b.high for b in lookback)
    min_l = min(b.low for b in lookback)
    close = bars[-1].close

    if max_h > resistance * 1.001 and close < resistance:
        target = support
        stop = max_h + atr * 0.15
        conf, breakdown = _score_geometry(0.54, reclaim=0.18, impulse=0.12)
        out.append(
            ChartPattern(
                kind="false_breakout",
                subtype="reversal",
                status="confirmed",
                points=(
                    PatternPoint(len(bars) - 1, close, "reclaim"),
                    PatternPoint(len(bars) - 3, max_h, "fake_high"),
                ),
                lines=(
                    PatternLine(len(bars) - 12, resistance, len(bars) - 1, resistance, "level"),
                ),
                zone_top=max_h,
                zone_bottom=resistance,
                neckline=PatternLine(len(bars) - 12, resistance, len(bars) - 1, resistance, "level"),
                pole_height=max_h - resistance,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:false_breakout",
                label_ru=PATTERN_LABELS_RU["false_breakout"] + " (сопр.)",
                direction="bearish",
            )
        )

    if min_l < support * 0.999 and close > support:
        target = resistance
        stop = min_l - atr * 0.15
        conf, breakdown = _score_geometry(0.54, reclaim=0.18, impulse=0.12)
        out.append(
            ChartPattern(
                kind="false_breakout",
                subtype="reversal",
                status="confirmed",
                points=(
                    PatternPoint(len(bars) - 1, close, "reclaim"),
                    PatternPoint(len(bars) - 3, min_l, "fake_low"),
                ),
                lines=(
                    PatternLine(len(bars) - 12, support, len(bars) - 1, support, "level"),
                ),
                zone_top=support,
                zone_bottom=min_l,
                neckline=PatternLine(len(bars) - 12, support, len(bars) - 1, support, "level"),
                pole_height=support - min_l,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:false_breakout",
                label_ru=PATTERN_LABELS_RU["false_breakout"] + " (подд.)",
                direction="bullish",
            )
        )
    return out


def _detect_one_two_three(
    bars: list[KlineBar],
    swings: list[SwingPoint],
    atr: float,
) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return out

  # Bearish 1-2-3 after uptrend
    for i in range(len(lows) - 1):
        p1 = lows[i]
        later_highs = [h for h in highs if h.index > p1.index]
        if not later_highs:
            continue
        p2 = later_highs[0]
        prior_highs = [h for h in highs if h.index < p2.index]
        if prior_highs and p2.price >= max(h.price for h in prior_highs) * 0.998:
            continue
        later_lows = [l for l in lows if l.index > p2.index]
        if not later_lows:
            continue
        p3 = later_lows[0]
        if p3.price >= p1.price:
            continue
        trigger = p1.price
        status = _status_from_break(bars, bullish=False, trigger=trigger)
        target = p3.price - (p2.price - p3.price)
        stop = p2.price + atr * 0.2
        conf, breakdown = _score_geometry(0.53, sequence=0.18, breakout=0.12 if status == "confirmed" else 0.0)
        out.append(
            ChartPattern(
                kind="one_two_three",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(p1.index, p1.price, "point1"),
                    PatternPoint(p2.index, p2.price, "point2"),
                    PatternPoint(p3.index, p3.price, "point3"),
                ),
                lines=(PatternLine(p1.index, trigger, len(bars) - 1, trigger, "trigger"),),
                zone_top=p2.price,
                zone_bottom=p3.price,
                neckline=PatternLine(p1.index, trigger, len(bars) - 1, trigger, "trigger"),
                pole_height=p2.price - p1.price,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:one_two_three",
                label_ru=PATTERN_LABELS_RU["one_two_three"] + " ↓",
                direction="bearish",
            )
        )
        break

    # Bullish 1-2-3 after downtrend
    for i in range(len(highs) - 1):
        p1 = highs[i]
        later_lows = [l for l in lows if l.index > p1.index]
        if not later_lows:
            continue
        p2 = later_lows[0]
        prior_lows = [l for l in lows if l.index < p2.index]
        if prior_lows and p2.price <= min(l.price for l in prior_lows) * 1.002:
            continue
        later_highs = [h for h in highs if h.index > p2.index]
        if not later_highs:
            continue
        p3 = later_highs[0]
        if p3.price <= p1.price:
            continue
        trigger = p1.price
        status = _status_from_break(bars, bullish=True, trigger=trigger)
        target = p3.price + (p3.price - p2.price)
        stop = p2.price - atr * 0.2
        conf, breakdown = _score_geometry(0.53, sequence=0.18, breakout=0.12 if status == "confirmed" else 0.0)
        out.append(
            ChartPattern(
                kind="one_two_three",
                subtype="reversal",
                status=status,
                points=(
                    PatternPoint(p1.index, p1.price, "point1"),
                    PatternPoint(p2.index, p2.price, "point2"),
                    PatternPoint(p3.index, p3.price, "point3"),
                ),
                lines=(PatternLine(p1.index, trigger, len(bars) - 1, trigger, "trigger"),),
                zone_top=p3.price,
                zone_bottom=p2.price,
                neckline=PatternLine(p1.index, trigger, len(bars) - 1, trigger, "trigger"),
                pole_height=p1.price - p2.price,
                target_price=target,
                stop_price=stop,
                confidence=conf,
                score_breakdown=breakdown,
                source_rule="buyhold:one_two_three",
                label_ru=PATTERN_LABELS_RU["one_two_three"] + " ↑",
                direction="bullish",
            )
        )
        break
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
            conf, breakdown = _score_geometry(0.60, trap=0.18, reclaim=0.15, breakout=0.12 if status == "confirmed" else 0.0)
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
            conf, breakdown = _score_geometry(0.60, trap=0.18, reclaim=0.15, breakout=0.12 if status == "confirmed" else 0.0)
            out.append(
                ChartPattern(
                    kind="baskerville_bearish",
                    subtype="reversal",
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
    out: list[ChartPattern] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    for i in range(len(highs) - 2):
        h1, h2, h3 = highs[i], highs[i + 1], highs[i + 2]
        if h2.index - h1.index < THREE_INDIANS_MIN_BARS or h3.index - h2.index < THREE_INDIANS_MIN_BARS:
            continue
        if h2.price <= h1.price or h3.price <= h2.price:
            continue
        if h3.price - h1.price < atr * 0.6:
            continue
        trend = PatternLine(h1.index, h1.price, h3.index, h3.price, "trend")
        target = h3.price - (h3.price - h1.price) * 0.618
        stop = h3.price + atr * 0.25
        status = "forming"
        if bars[-1].close < _line_value(trend, len(bars) - 1):
            status = "confirmed"
        conf, breakdown = _score_geometry(0.54, sequence=0.16, trend=0.10, breakout=0.12 if status == "confirmed" else 0.0)
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
                score_breakdown=breakdown,
                source_rule="buyhold:three_indians",
                label_ru=PATTERN_LABELS_RU["three_indians"] + " ↓",
                direction="bearish",
            )
        )

    for i in range(len(lows) - 2):
        l1, l2, l3 = lows[i], lows[i + 1], lows[i + 2]
        if l2.index - l1.index < THREE_INDIANS_MIN_BARS or l3.index - l2.index < THREE_INDIANS_MIN_BARS:
            continue
        if l2.price >= l1.price or l3.price >= l2.price:
            continue
        if l1.price - l3.price < atr * 0.6:
            continue
        trend = PatternLine(l1.index, l1.price, l3.index, l3.price, "trend")
        target = l3.price + (l1.price - l3.price) * 0.618
        stop = l3.price - atr * 0.25
        status = "forming"
        if bars[-1].close > _line_value(trend, len(bars) - 1):
            status = "confirmed"
        conf, breakdown = _score_geometry(0.54, sequence=0.16, trend=0.10, breakout=0.12 if status == "confirmed" else 0.0)
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
                score_breakdown=breakdown,
                source_rule="buyhold:three_indians",
                label_ru=PATTERN_LABELS_RU["three_indians"] + " ↑",
                direction="bullish",
            )
        )
    return out[-2:]


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
    """Цена у границы/шеи/триггера фигуры (для Trade Decision Gate)."""
    if pattern is None or pattern.confidence < MIN_TRADE_PATTERN_CONFIDENCE:
        return False
    side = side.lower()
    if side == "long" and pattern.direction == "bearish":
        return False
    if side == "short" and pattern.direction == "bullish":
        return False
    if pattern.status == "confirmed" and pattern.direction in {"bullish", "bearish"}:
        want = "bullish" if side == "long" else "bearish"
        if pattern.direction == want:
            return True

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
        if abs(price - lv) / lv * 100.0 <= tol_pct:
            return True
    return False


def detect_chart_patterns(
    bars: list[KlineBar],
    *,
    min_confidence: float = MIN_PATTERN_CONFIDENCE,
    enabled: bool = True,
) -> list[ChartPattern]:
    if not enabled or len(bars) < 24:
        return []
    swings = find_pattern_swings(bars)
    atr = compute_atr(bars)
    found: list[ChartPattern] = []
    hs_patterns = _detect_head_shoulders(bars, swings, atr)
    found.extend(_detect_double_top_bottom(bars, swings, atr))
    found.extend(hs_patterns)
    found.extend(_detect_baskerville(bars, hs_patterns, atr))
    found.extend(_detect_cup_with_handle(bars, swings, atr))
    found.extend(_detect_three_indians(bars, swings, atr))
    found.extend(_detect_diamond(swings, bars, atr))
    found.extend(_detect_flag_pennant(bars, atr))
    found.extend(_detect_triangles_wedges(swings, bars, atr))
    found.extend(_detect_false_breakout(bars, swings, atr))
    found.extend(_detect_one_two_three(bars, swings, atr))

    deduped: list[ChartPattern] = []
    seen: set[str] = set()
    for pat in sorted(found, key=lambda p: (-p.confidence, p.points[-1].index if p.points else 0)):
        key = f"{pat.kind}:{pat.points[0].index if pat.points else 0}"
        if key in seen:
            continue
        seen.add(key)
        if pat.confidence >= min_confidence:
            deduped.append(pat)
    return deduped[:5]


def pick_primary_pattern(patterns: list[ChartPattern]) -> ChartPattern | None:
    if not patterns:
        return None
    confirmed = [p for p in patterns if p.status == "confirmed"]
    pool = confirmed or patterns
    return max(pool, key=lambda p: (p.confidence, 1 if p.status == "confirmed" else 0))


def format_chart_pattern_compact(pattern: ChartPattern | None) -> str:
    if not pattern:
        return ""
    status = "✓" if pattern.status == "confirmed" else "…"
    target = f" → {pattern.target_price:.5g}" if pattern.target_price else ""
    return f"{pattern.label_ru} {status}{target}"
