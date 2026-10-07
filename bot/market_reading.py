"""Evidence-first market reading: what is on THIS asset, not a template overlay.

H4 / H1 / M15 / M5 describe direction. Patterns, channels, Fib and dual
forecasts stay off unless the structure actually prints them.
Price making a higher high on weaker volume is a warning, not a SHORT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .bybit_klines import KlineBar

WEAK_VOLUME_RATIO = 0.75
NEAR_ZONE_PCT = 0.028
STRONG_PATTERN = 0.72


@dataclass(frozen=True)
class TimeframeRead:
    tf_label: str
    structure: str  # bullish | bearish | range | unknown
    label_ru: str
    change_pct: float = 0.0


@dataclass(frozen=True)
class ParticipationDivergence:
    kind: str  # weak_high | weak_low
    idx_a: int
    idx_b: int
    price_a: float
    price_b: float
    volume_a: float
    volume_b: float
    volume_ratio: float
    oi_ratio: float | None = None
    label_ru: str = ""


@dataclass
class MarketReading:
    working: TimeframeRead
    mid: TimeframeRead | None = None
    htf: TimeframeRead | None = None
    macro: TimeframeRead | None = None
    weekly: TimeframeRead | None = None
    tf_stack: str = ""
    divergence: ParticipationDivergence | None = None
    present: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    live_scenario: str = "range"  # continuation | exhaustion | range | reversal
    seek_price: float | None = None
    seek_label: str = ""
    narrative: str = ""
    accept_pattern: bool = False
    accept_htf_pattern: bool = False
    accept_channel: bool = False
    accept_fib: bool = False
    accept_ob: bool = False
    draw_both_forecasts: bool = False


def _pct_change(bars: Sequence[KlineBar] | None, lookback: int = 12) -> float:
    if not bars or len(bars) < 3:
        return 0.0
    n = min(lookback, len(bars) - 1)
    start = float(bars[-1 - n].close)
    if start <= 0:
        return 0.0
    return (float(bars[-1].close) - start) / start * 100.0


def _structure_from_swings(swings: Sequence[Any]) -> TimeframeRead:
    highs = [s for s in swings[-8:] if getattr(s, "kind", "") == "high"]
    lows = [s for s in swings[-8:] if getattr(s, "kind", "") == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return TimeframeRead("?", "unknown", "мало структуры")
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price
    if hh and hl:
        return TimeframeRead("?", "bullish", "HH+HL вверх")
    if lh and ll:
        return TimeframeRead("?", "bearish", "LH+LL вниз")
    return TimeframeRead("?", "range", "диапазон / смесь")


def read_timeframe(
    bars: Sequence[KlineBar] | None,
    swings: Sequence[Any] | None,
    *,
    tf_label: str,
    lookback: int = 12,
) -> TimeframeRead:
    if not bars or len(bars) < 8:
        return TimeframeRead(tf_label, "unknown", f"{tf_label}: мало данных")
    change = _pct_change(bars, lookback)
    if swings and len(swings) >= 4:
        base = _structure_from_swings(swings)
    else:
        if change >= 1.2:
            base = TimeframeRead(tf_label, "bullish", f"{tf_label}: рост")
        elif change <= -1.2:
            base = TimeframeRead(tf_label, "bearish", f"{tf_label}: снижение")
        else:
            base = TimeframeRead(tf_label, "range", f"{tf_label}: без направления")
        return TimeframeRead(tf_label, base.structure, base.label_ru, change)
    return TimeframeRead(tf_label, base.structure, f"{tf_label}: {base.label_ru}", change)


def _local_volume(bars: Sequence[KlineBar], index: int, window: int = 1) -> float:
    lo = max(0, index - window)
    hi = min(len(bars), index + window + 1)
    return sum(max(0.0, float(getattr(bars[i], "volume", 0) or 0)) for i in range(lo, hi))


def _oi_near_time(oi_bars: Sequence[Any] | None, ts: float) -> float | None:
    if not oi_bars:
        return None
    best = None
    best_dt = 10**18
    for bar in oi_bars:
        t = float(getattr(bar, "open_time", 0) or 0)
        dt = abs(t - ts)
        if dt < best_dt:
            best_dt = dt
            best = float(getattr(bar, "oi_close", 0) or 0)
    if best is None or best <= 0 or best_dt > 3600 * 6:
        return None
    return best


def detect_participation_divergence(
    bars: Sequence[KlineBar],
    swings: Sequence[Any],
    *,
    oi_bars: Sequence[Any] | None = None,
    volume_ratio: float = WEAK_VOLUME_RATIO,
) -> ParticipationDivergence | None:
    """Higher high / lower low vs weaker participation at the later extreme."""
    if len(bars) < 20 or not swings:
        return None

    def _scan(kind: str) -> ParticipationDivergence | None:
        points = [s for s in swings if getattr(s, "kind", "") == kind]
        if len(points) < 2:
            return None
        a, b = points[-2], points[-1]
        if b.index - a.index < 3:
            return None
        if b.index >= len(bars) or a.index >= len(bars):
            return None
        vol_a = _local_volume(bars, a.index)
        vol_b = _local_volume(bars, b.index)
        if vol_a <= 0 or vol_b <= 0:
            return None
        ratio = vol_b / vol_a
        oi_a = _oi_near_time(oi_bars, float(bars[a.index].open_time))
        oi_b = _oi_near_time(oi_bars, float(bars[b.index].open_time))
        oi_ratio = (oi_b / oi_a) if oi_a and oi_b and oi_a > 0 else None

        if kind == "high":
            if b.price < a.price * 1.001:
                return None
            if ratio >= volume_ratio and (oi_ratio is None or oi_ratio >= volume_ratio):
                return None
            oi_bit = ""
            if oi_ratio is not None and oi_ratio < volume_ratio:
                oi_bit = f", OI {oi_ratio:.0%} от прошлого хая"
            return ParticipationDivergence(
                kind="weak_high",
                idx_a=a.index,
                idx_b=b.index,
                price_a=float(a.price),
                price_b=float(b.price),
                volume_a=vol_a,
                volume_b=vol_b,
                volume_ratio=ratio,
                oi_ratio=oi_ratio,
                label_ru=(
                    f"хай обновлён, объём {ratio:.0%} от предыдущего пика{oi_bit} "
                    "— участие не подтверждает рост"
                ),
            )
        if b.price > a.price * 0.999:
            return None
        if ratio >= volume_ratio and (oi_ratio is None or oi_ratio >= volume_ratio):
            return None
        return ParticipationDivergence(
            kind="weak_low",
            idx_a=a.index,
            idx_b=b.index,
            price_a=float(a.price),
            price_b=float(b.price),
            volume_a=vol_a,
            volume_b=vol_b,
            volume_ratio=ratio,
            oi_ratio=oi_ratio,
            label_ru=(
                f"лой обновлён, объём {ratio:.0%} от предыдущего дна "
                "— продажи без усиления участия"
            ),
        )

    weak_high = _scan("high")
    if weak_high is not None:
        return weak_high
    return _scan("low")


def _pattern_ok(
    pattern: Any | None,
    *,
    bars: Sequence[KlineBar] | None = None,
    current: float | None = None,
) -> bool:
    if pattern is None:
        return False
    conf = float(getattr(pattern, "confidence", 0) or 0)
    status = str(getattr(pattern, "status", "") or "")
    if conf < STRONG_PATTERN:
        return False
    if status == "invalidated":
        return False
    if bars:
        from .chart_patterns import pattern_relevant_now

        if not pattern_relevant_now(pattern, list(bars), current=current):
            return False
    return True


def _channel_ok(channel: Any | None, bars: Sequence[KlineBar]) -> bool:
    """Канал только если он «живёт» на истории, а не два свинга."""
    if channel is None or not bars:
        return False
    try:
        span = abs(int(getattr(channel, "upper_end_idx", 0)) - int(getattr(channel, "upper_start_idx", 0)))
    except (TypeError, ValueError):
        span = 0
    if span < 10:
        return False
    if len(bars) < 24:
        return False
    return True


def _tf_direction_phrase(tf: TimeframeRead | None) -> str:
    if tf is None or tf.structure == "unknown":
        return ""
    mood = {
        "bullish": "вверх",
        "bearish": "вниз",
        "range": "боковик",
    }.get(tf.structure, "")
    if mood:
        return f"{tf.tf_label} {mood}"
    return tf.label_ru


def compose_tf_stack(reading: MarketReading) -> str:
    parts: list[str] = []
    for tf in (reading.weekly, reading.macro, reading.htf, reading.mid, reading.working):
        phrase = _tf_direction_phrase(tf)
        if phrase and phrase not in parts:
            parts.append(phrase)
    return " → ".join(parts[:5])


def compose_human_narrative(reading: MarketReading, *, price: float = 0.0) -> str:
    """Одна фраза как у трейдера: стек ТФ + куда тянет цена сейчас."""
    stack = compose_tf_stack(reading)
    if reading.live_scenario == "exhaustion":
        tail = reading.seek_label or "импульс без участия — не догонять"
    elif reading.live_scenario == "reversal":
        tail = reading.seek_label or "разворот возможен после свипа и слома"
    elif reading.live_scenario == "continuation":
        tail = reading.seek_label or "логично продолжение по старшему ТФ"
    else:
        tail = reading.seek_label or "середина диапазона — ждать границу, не market"
    if reading.divergence is not None and reading.divergence.label_ru:
        tail = f"{tail}; {reading.divergence.label_ru}"
    if stack:
        return f"{stack}. {tail}"
    return tail


def _tf_label(minutes: int) -> str:
    if minutes >= 10080:
        return "W1"
    if minutes >= 240:
        return "H4"
    if minutes >= 60:
        return "H1"
    if minutes >= 15:
        return "M15"
    if minutes >= 5:
        return "M5"
    return f"{minutes}m"


def analyze_market_reading(
    bars: Sequence[KlineBar],
    swings: Sequence[Any],
    *,
    mid_bars: Sequence[KlineBar] | None = None,
    mid_swings: Sequence[Any] | None = None,
    htf_bars: Sequence[KlineBar] | None = None,
    htf_swings: Sequence[Any] | None = None,
    macro_bars: Sequence[KlineBar] | None = None,
    macro_swings: Sequence[Any] | None = None,
    weekly_bars: Sequence[KlineBar] | None = None,
    weekly_swings: Sequence[Any] | None = None,
    oi_bars: Sequence[Any] | None = None,
    pattern: Any | None = None,
    htf_pattern: Any | None = None,
    channel: Any | None = None,
    smc: Any | None = None,
    wave: Any | None = None,
    interval_minutes: int = 5,
    mid_interval_minutes: int = 15,
    htf_interval_minutes: int = 60,
    macro_interval_minutes: int = 240,
    weekly_interval_minutes: int = 10080,
    current: float | None = None,
    symbol: str = "",
    cvd_ratio: float | None = None,
) -> MarketReading:
    price = float(current or (bars[-1].close if bars else 0) or 0)
    working = read_timeframe(bars, swings, tf_label=_tf_label(interval_minutes))
    mid = read_timeframe(mid_bars, mid_swings, tf_label=_tf_label(mid_interval_minutes)) if mid_bars else None
    htf = read_timeframe(htf_bars, htf_swings, tf_label=_tf_label(htf_interval_minutes)) if htf_bars else None
    macro = read_timeframe(macro_bars, macro_swings, tf_label=_tf_label(macro_interval_minutes), lookback=8) if macro_bars else None
    weekly = (
        read_timeframe(weekly_bars, weekly_swings, tf_label=_tf_label(weekly_interval_minutes), lookback=6)
        if weekly_bars
        else None
    )
    if symbol:
        from .swing_extreme_memory import detect_participation_with_memory

        divergence = detect_participation_with_memory(
            symbol,
            interval_minutes,
            bars,
            swings,
            oi_bars=oi_bars,
            cvd_ratio=cvd_ratio,
        )
    else:
        divergence = detect_participation_divergence(bars, swings, oi_bars=oi_bars)

    present: list[str] = []
    absent: list[str] = []
    if working.structure in {"bullish", "bearish", "range"}:
        present.append(working.label_ru)
    if macro and macro.structure != "unknown":
        present.append(macro.label_ru)
    if htf and htf.structure != "unknown":
        present.append(htf.label_ru)
    elif htf_bars:
        absent.append("нет ясного H1/H4 направления")
    if mid and mid.structure != "unknown":
        present.append(mid.label_ru)
    if weekly and weekly.structure != "unknown":
        present.append(weekly.label_ru)
    elif weekly_bars:
        absent.append("недельный контекст слабый")

    accept_pattern = _pattern_ok(pattern, bars=bars, current=price)
    if accept_pattern:
        present.append(f"фигура {getattr(pattern, 'label_ru', '')}")
    else:
        absent.append("подтверждённой фигуры нет")
        accept_pattern = False

    accept_htf_pattern = _pattern_ok(htf_pattern, bars=htf_bars or bars, current=price)
    if accept_htf_pattern:
        present.append(f"HTF {getattr(htf_pattern, 'label_ru', '')}")
    else:
        absent.append("HTF-фигуры нет")

    accept_channel = _channel_ok(channel, bars)
    if accept_channel and htf and htf.structure in {"bullish", "bearish"}:
        if working.structure == "range":
            pass
        elif working.structure not in {htf.structure, "unknown"}:
            accept_channel = False
    if accept_channel:
        present.append(getattr(channel, "label", "канал"))
    else:
        absent.append("канала нет (или натянут)")

    nearby_ob = False
    if smc is not None and price > 0:
        for block in getattr(smc, "order_blocks", None) or []:
            if getattr(block, "mitigated", False):
                continue
            mid_p = (float(block.top) + float(block.bottom)) / 2.0
            if abs(mid_p - price) / price <= NEAR_ZONE_PCT:
                nearby_ob = True
                break
        if getattr(smc, "liquidity_sweep", False):
            present.append("свип ликвидности")
        else:
            absent.append("свипа нет")
        if getattr(smc, "structure_break", False):
            present.append("слом структуры")
        else:
            absent.append("слома структуры нет")
    accept_ob = nearby_ob
    if accept_ob:
        present.append("ордер-блок рядом")
    else:
        absent.append("свежего OB у цены нет")

    accept_fib = bool(wave is not None and getattr(wave, "has_confluence", False))
    if accept_fib:
        present.append("Fib совпадает с уровнем")
    else:
        absent.append("Fib без confluence — не используем")

    if divergence is not None:
        present.append(divergence.label_ru)

    htf_struct = (htf.structure if htf else working.structure)
    live = "range"
    seek_price = None
    seek_label = ""
    if divergence is not None and divergence.kind == "weak_high":
        live = "exhaustion"
        seek_label = "риск снятия хая — участие не подтвердило"
    elif divergence is not None and divergence.kind == "weak_low":
        live = "exhaustion"
        seek_label = "слабость продаж на новом лое"
    elif htf_struct == "bullish" and working.structure != "bearish":
        impulse_pct = _pct_change(bars, min(36, max(12, len(bars) - 1))) if bars else 0.0
        if impulse_pct >= 8.0:
            live = "exhaustion"
            seek_label = (
                f"импульс +{impulse_pct:.0f}% — не догонять, ждать откат или новую базу"
            )
        else:
            live = "continuation"
            seek_label = "стремится продолжить вверх по старшему ТФ"
    elif htf_struct == "bearish" and working.structure != "bullish":
        live = "continuation"
        seek_label = "стремится продолжить вниз по старшему ТФ"
    elif getattr(smc, "structure_break", False) and getattr(smc, "liquidity_sweep", False):
        live = "reversal"
        seek_label = "возможен разворот после свипа и слома"
    else:
        live = "range"
        seek_label = "середина/смесь — цена без ясной цели"

    draft = MarketReading(
        working=working,
        mid=mid,
        htf=htf,
        macro=macro,
        weekly=weekly,
        divergence=divergence,
        present=present[:8],
        absent=absent[:8],
        live_scenario=live,
        seek_price=seek_price,
        seek_label=seek_label,
        narrative="",
        accept_pattern=accept_pattern,
        accept_htf_pattern=accept_htf_pattern,
        accept_channel=accept_channel,
        accept_fib=accept_fib,
        accept_ob=accept_ob,
        draw_both_forecasts=False,
    )
    draft.tf_stack = compose_tf_stack(draft)
    draft.narrative = compose_human_narrative(draft, price=price)
    if live == "range" and divergence is None and not accept_pattern:
        draft.draw_both_forecasts = True
    else:
        draft.draw_both_forecasts = False
    return draft


def apply_reading_chart_gates(
    *,
    chart_patterns: list[Any],
    primary_chart_pattern: Any | None,
    htf_chart_patterns: list[Any],
    primary_htf_chart_pattern: Any | None,
    channel: Any | None,
    reading: MarketReading,
) -> tuple[list[Any], Any | None, list[Any], Any | None, Any | None]:
    """Strip unconfirmed layers before chart draw and TA payload."""
    cp = list(chart_patterns)
    primary = primary_chart_pattern
    htf_cp = list(htf_chart_patterns)
    htf_primary = primary_htf_chart_pattern
    ch = channel

    if not reading.accept_pattern:
        cp = []
        primary = None
    if not reading.accept_htf_pattern:
        htf_cp = []
        htf_primary = None
    if not reading.accept_channel:
        ch = None
    return cp, primary, htf_cp, htf_primary, ch


def trim_forecast_paths_for_reading(
    *,
    verdict: str,
    reading: MarketReading,
    correction_path: Any | None,
    continuation_path: Any | None,
    action_priority: str,
) -> tuple[Any | None, Any | None]:
    """One live path unless range explicitly allows both."""
    if reading.draw_both_forecasts:
        return correction_path, continuation_path
    if (verdict or "").upper() != "WAIT":
        return correction_path, continuation_path
    lean = (action_priority or "").lower()
    if lean == "long":
        return None, continuation_path
    if lean == "short":
        return correction_path, None
    if reading.live_scenario == "continuation" and reading.htf:
        if reading.htf.structure == "bullish":
            return None, continuation_path
        if reading.htf.structure == "bearish":
            return correction_path, None
    return correction_path if continuation_path is None else None, continuation_path


def apply_reading_to_verdict(
    *,
    verdict: str,
    reason: str,
    conf: int,
    reading: MarketReading,
) -> tuple[str, int, str]:
    """Weak participation at highs blocks chasing LONG; does not force SHORT."""
    if reading.live_scenario == "exhaustion" and verdict in {"LONG", "SHORT"}:
        note = reading.seek_label or "импульс без подтверждения участия"
        extra = f"{reason} · {note}" if reason else note
        return "WAIT", min(conf, 6), extra
    if reading.live_scenario == "range" and verdict in {"LONG", "SHORT"}:
        if not reading.accept_ob and not reading.accept_fib:
            note = "нет зоны confluence — только WAIT у границы"
            if note not in (reason or ""):
                extra = f"{reason} · {note}" if reason else note
                return "WAIT", min(conf, 7), extra
    div = reading.divergence
    if div is None:
        return verdict, conf, reason
    note = div.label_ru
    if div.kind == "weak_high" and verdict == "LONG":
        extra = f"{reason} · {note}" if reason else note
        return "WAIT", min(conf, 6), extra
    if div.kind == "weak_low" and verdict == "SHORT":
        extra = f"{reason} · {note}" if reason else note
        return "WAIT", min(conf, 6), extra
    if note and note not in (reason or ""):
        reason = f"{reason} · {note}" if reason else note
    return verdict, conf, reason
