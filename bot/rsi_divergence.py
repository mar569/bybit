"""RSI + calculative divergence (TradingView-style, Wilder + pivots).

Professional rules (Wilder / Cardwell / TV «Calculate Divergence»):
- Regular Bull: price LL + RSI HL → weakening sell momentum (reversal-up cue)
- Regular Bear: price HH + RSI LH → weakening buy momentum (reversal-down cue)
- Hidden Bull:  price HL + RSI LL → continuation in uptrend (Cardwell positive reversal family)
- Hidden Bear:  price LH + RSI HH → continuation in downtrend

Pivots confirmed with left/right bars (no repaint of unfinished swings).
Divergence is confluence, not a standalone entry — combine with structure/triggers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .bybit_klines import KlineBar

RsiDivKind = Literal[
    "regular_bull",
    "regular_bear",
    "hidden_bull",
    "hidden_bear",
]


@dataclass(frozen=True)
class RsiPivot:
    index: int
    price: float
    rsi: float
    kind: Literal["high", "low"]


@dataclass(frozen=True)
class RsiDivergence:
    kind: RsiDivKind
    label: str  # Bull / Bear
    label_ru: str
    # pivot A (older) → pivot B (newer / signal)
    idx_a: int
    idx_b: int
    price_a: float
    price_b: float
    rsi_a: float
    rsi_b: float
    strength: float  # 0..1
    bars_between: int
    rsi_delta: float

    @property
    def is_bullish(self) -> bool:
        return self.kind in {"regular_bull", "hidden_bull"}

    @property
    def is_regular(self) -> bool:
        return self.kind in {"regular_bull", "regular_bear"}

    @property
    def is_hidden(self) -> bool:
        return self.kind in {"hidden_bull", "hidden_bear"}


@dataclass
class RsiDivergenceResult:
    rsi: list[float] = field(default_factory=list)
    rsi_sma: list[float] = field(default_factory=list)
    pivots: list[RsiPivot] = field(default_factory=list)
    divergences: list[RsiDivergence] = field(default_factory=list)
    last: RsiDivergence | None = None
    rsi_last: float = 50.0
    summary: str = ""
    bias: str = "neutral"  # long | short | neutral

    @property
    def active(self) -> bool:
        return self.last is not None


def compute_rsi_wilder(closes: list[float], period: int = 14) -> list[float]:
    """Wilder RSI via RMA (same as TradingView ta.rsi)."""
    n = len(closes)
    out = [50.0] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains[i] = max(ch, 0.0)
        losses[i] = max(-ch, 0.0)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    if avg_loss <= 1e-12:
        out[period] = 100.0
    else:
        out[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss <= 1e-12:
            out[i] = 100.0
        else:
            out[i] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    # fill early bars with first valid
    for i in range(period):
        out[i] = out[period]
    return out


def compute_sma(values: list[float], period: int = 14) -> list[float]:
    n = len(values)
    out = [values[0] if values else 50.0] * n
    if n < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, n):
        s += values[i] - values[i - period]
        out[i] = s / period
    for i in range(period - 1):
        out[i] = out[period - 1]
    return out


def _find_pivots(
    values: list[float],
    *,
    left: int,
    right: int,
    kind: Literal["high", "low"],
) -> list[int]:
    """Confirmed pivots: value[i] extreme vs left+right neighbors (TV ta.pivothigh/low)."""
    n = len(values)
    out: list[int] = []
    if n < left + right + 1:
        return out
    for i in range(left, n - right):
        v = values[i]
        if kind == "high":
            if any(values[i - k] >= v for k in range(1, left + 1)):
                continue
            if any(values[i + k] > v for k in range(1, right + 1)):
                continue
            out.append(i)
        else:
            if any(values[i - k] <= v for k in range(1, left + 1)):
                continue
            if any(values[i + k] < v for k in range(1, right + 1)):
                continue
            out.append(i)
    return out


def _pivot_strength(div: RsiDivergence, *, rsi_ob: float = 70.0, rsi_os: float = 30.0) -> float:
    """0..1 conviction: RSI extreme + delta + distance between swings."""
    score = 0.35
    delta = abs(div.rsi_delta)
    score += min(0.30, delta / 20.0 * 0.30)
    if div.kind == "regular_bull" and min(div.rsi_a, div.rsi_b) <= rsi_os + 8:
        score += 0.20
    if div.kind == "regular_bear" and max(div.rsi_a, div.rsi_b) >= rsi_ob - 8:
        score += 0.20
    if div.is_hidden:
        score += 0.05
    # prefer not-too-close swings
    if 4 <= div.bars_between <= 60:
        score += 0.10
    return max(0.0, min(1.0, score))


def detect_rsi_divergences(
    bars: list[KlineBar],
    *,
    rsi_period: int = 14,
    pivot_left: int = 5,
    pivot_right: int = 5,
    min_bars_between: int = 5,
    max_bars_between: int = 80,
    min_rsi_delta: float = 1.5,
    include_hidden: bool = True,
    max_divergences: int = 8,
) -> RsiDivergenceResult:
    """Detect regular (+ optional hidden) RSI divergences on confirmed pivots."""
    result = RsiDivergenceResult()
    if len(bars) < rsi_period + pivot_left + pivot_right + 8:
        return result

    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    rsi = compute_rsi_wilder(closes, rsi_period)
    rsi_sma = compute_sma(rsi, rsi_period)
    result.rsi = rsi
    result.rsi_sma = rsi_sma
    result.rsi_last = float(rsi[-1])

    # Price pivots on high/low (TV compare price swings vs RSI at those bars)
    price_highs = _find_pivots(highs, left=pivot_left, right=pivot_right, kind="high")
    price_lows = _find_pivots(lows, left=pivot_left, right=pivot_right, kind="low")

    pivots: list[RsiPivot] = []
    for i in price_highs:
        pivots.append(RsiPivot(index=i, price=highs[i], rsi=rsi[i], kind="high"))
    for i in price_lows:
        pivots.append(RsiPivot(index=i, price=lows[i], rsi=rsi[i], kind="low"))
    pivots.sort(key=lambda p: p.index)
    result.pivots = pivots

    divergences: list[RsiDivergence] = []

    def _pair_ok(i0: int, i1: int) -> bool:
        gap = i1 - i0
        return min_bars_between <= gap <= max_bars_between

    # Regular / Hidden Bear — compare successive price highs; RSI at same bars
    for a, b in zip(price_highs, price_highs[1:]):
        if not _pair_ok(a, b):
            continue
        # Prefer that RSI also had highs near these bars (within pivot_right)
        pa, pb = highs[a], highs[b]
        ra, rb = rsi[a], rsi[b]
        rsi_delta = rb - ra
        if abs(rsi_delta) < min_rsi_delta:
            continue
        kind: RsiDivKind | None = None
        label_ru = ""
        if pa < pb and ra > rb:
            kind = "regular_bear"
            label_ru = "медвежья дивергенция RSI (HH цены + LH RSI)"
        elif include_hidden and pa > pb and ra < rb:
            kind = "hidden_bear"
            label_ru = "скрытая медвежья (LH цены + HH RSI) — продолжение вниз"
        if kind is None:
            continue
        divergences.append(
            RsiDivergence(
                kind=kind,
                label="Bear",
                label_ru=label_ru,
                idx_a=a,
                idx_b=b,
                price_a=pa,
                price_b=pb,
                rsi_a=ra,
                rsi_b=rb,
                strength=0.0,
                bars_between=b - a,
                rsi_delta=rsi_delta,
            )
        )

    # Regular / Hidden Bull — successive price lows
    for a, b in zip(price_lows, price_lows[1:]):
        if not _pair_ok(a, b):
            continue
        pa, pb = lows[a], lows[b]
        ra, rb = rsi[a], rsi[b]
        rsi_delta = rb - ra
        if abs(rsi_delta) < min_rsi_delta:
            continue
        kind = None
        label_ru = ""
        if pa > pb and ra < rb:
            kind = "regular_bull"
            label_ru = "бычья дивергенция RSI (LL цены + HL RSI)"
        elif include_hidden and pa < pb and ra > rb:
            kind = "hidden_bull"
            label_ru = "скрытая бычья (HL цены + LL RSI) — продолжение вверх"
        if kind is None:
            continue
        divergences.append(
            RsiDivergence(
                kind=kind,
                label="Bull",
                label_ru=label_ru,
                idx_a=a,
                idx_b=b,
                price_a=pa,
                price_b=pb,
                rsi_a=ra,
                rsi_b=rb,
                strength=0.0,
                bars_between=b - a,
                rsi_delta=rsi_delta,
            )
        )

    # Strength + keep most recent max_divergences
    scored: list[RsiDivergence] = []
    for d in divergences:
        s = _pivot_strength(d)
        scored.append(
            RsiDivergence(
                kind=d.kind,
                label=d.label,
                label_ru=d.label_ru,
                idx_a=d.idx_a,
                idx_b=d.idx_b,
                price_a=d.price_a,
                price_b=d.price_b,
                rsi_a=d.rsi_a,
                rsi_b=d.rsi_b,
                strength=s,
                bars_between=d.bars_between,
                rsi_delta=d.rsi_delta,
            )
        )
    scored.sort(key=lambda x: x.idx_b)
    # drop overlapping same-direction too close
    filtered: list[RsiDivergence] = []
    for d in scored:
        if filtered and d.label == filtered[-1].label and d.idx_b - filtered[-1].idx_b < 3:
            if d.strength >= filtered[-1].strength:
                filtered[-1] = d
            continue
        filtered.append(d)
    result.divergences = filtered[-max_divergences:]
    result.last = result.divergences[-1] if result.divergences else None

    if result.last:
        last = result.last
        # Freshness: only bias if last pivot is recent
        age = len(bars) - 1 - last.idx_b
        if age <= pivot_right + 12:
            if last.is_bullish:
                result.bias = "long"
            else:
                result.bias = "short"
        result.summary = (
            f"RSI {result.rsi_last:.0f} · {last.label} · {last.label_ru} "
            f"(str {last.strength:.0%})"
        )
    else:
        result.summary = f"RSI {result.rsi_last:.0f} · дивергенций нет"

    return result


def rsi_divergence_flow_adjust(result: RsiDivergenceResult | None) -> tuple[int, int, list[str]]:
    """Return (cont_delta, corr_delta, notes) for market flow confluence."""
    if result is None or result.last is None:
        return 0, 0, []
    last = result.last
    age_ok = True  # bias already gated in detect
    if result.bias == "neutral":
        # still show note but weaker
        pass
    notes: list[str] = []
    cont = corr = 0
    w = int(8 + last.strength * 14)  # 8..22
    if last.kind == "regular_bull":
        cont += w
        notes.append(f"RSI Bull (regular) · {last.strength:.0%}")
    elif last.kind == "regular_bear":
        corr += w
        notes.append(f"RSI Bear (regular) · {last.strength:.0%}")
    elif last.kind == "hidden_bull":
        cont += max(6, w - 4)
        notes.append(f"RSI Bull (hidden cont.) · {last.strength:.0%}")
    elif last.kind == "hidden_bear":
        corr += max(6, w - 4)
        notes.append(f"RSI Bear (hidden cont.) · {last.strength:.0%}")
    if result.bias == "neutral":
        cont = cont // 2
        corr = corr // 2
    if not age_ok:
        return 0, 0, notes
    return cont, corr, notes


def format_rsi_divergence_html(result: RsiDivergenceResult | None) -> str:
    if result is None or not result.last:
        if result and result.rsi_last:
            return f"📉 <b>RSI</b> {result.rsi_last:.0f}"
        return ""
    last = result.last
    color_emoji = "🟢" if last.is_bullish else "🔴"
    kind = "regular" if last.is_regular else "hidden"
    return (
        f"{color_emoji} <b>RSI {last.label}</b> ({kind}) · "
        f"RSI {result.rsi_last:.0f} · {last.label_ru}"
    )
