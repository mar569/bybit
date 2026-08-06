"""MACD(12,26,9) для нефти — блок SHORT против бычьего импульса."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class OilMacd:
    macd: float
    signal: float
    hist: float
    hist_prev: float
    bias: str  # bull | bear | neutral
    cross: str  # bull_cross | bear_cross | none
    line_ru: str


def _ema(values: list[float], period: int) -> list[float]:
    if not values or period <= 1:
        return list(values)
    k = 2.0 / (period + 1.0)
    out: list[float] = []
    ema = values[0]
    for i, v in enumerate(values):
        if i == 0:
            ema = v
        else:
            ema = v * k + ema * (1.0 - k)
        out.append(ema)
    return out


def compute_oil_macd(
    bars: Sequence[object] | None,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> OilMacd | None:
    """Классический MACD по close. Нужно ≥ slow+signal баров."""
    if not bars or len(bars) < slow + signal + 2:
        return None
    closes: list[float] = []
    for b in bars:
        try:
            c = float(getattr(b, "close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            closes.append(c)
    if len(closes) < slow + signal + 2:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [a - b for a, b in zip(ema_fast, ema_slow)]
    sig_line = _ema(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, sig_line)]
    if len(hist) < 2:
        return None

    m = macd_line[-1]
    s = sig_line[-1]
    h = hist[-1]
    hp = hist[-2]

    cross = "none"
    if hp <= 0 < h:
        cross = "bull_cross"
    elif hp >= 0 > h:
        cross = "bear_cross"

    if cross == "bull_cross" or (h > 0 and m > s and h >= hp):
        bias = "bull"
    elif cross == "bear_cross" or (h < 0 and m < s and h <= hp):
        bias = "bear"
    elif h > 0 and m > s:
        bias = "bull"
    elif h < 0 and m < s:
        bias = "bear"
    else:
        bias = "neutral"

    if bias == "bull":
        line = f"MACD↑ hist {h:+.3f}" + (" · bull cross" if cross == "bull_cross" else "")
    elif bias == "bear":
        line = f"MACD↓ hist {h:+.3f}" + (" · bear cross" if cross == "bear_cross" else "")
    else:
        line = f"MACD нейтрален hist {h:+.3f}"

    return OilMacd(
        macd=m,
        signal=s,
        hist=h,
        hist_prev=hp,
        bias=bias,
        cross=cross,
        line_ru=line,
    )


def macd_blocks_side(macd: OilMacd | None, side: str) -> bool:
    """True если MACD запрещает открывать сторону против себя."""
    if macd is None:
        return False
    side_l = (side or "").lower()
    if side_l in {"short", "sell"} and macd.bias == "bull":
        return True
    if side_l in {"long", "buy"} and macd.bias == "bear":
        return True
    return False


def trend_blocks_counter_trade(
    bars: Sequence[object] | None,
    *,
    side: str,
    interval_minutes: int = 5,
) -> tuple[bool, str]:
    """Блок SHORT в восходящем импульсе / LONG в нисходящем.

    Жёстче пороги: нефть 0.25%/30м уже достаточно, чтобы не шортить «по сделке».
    """
    side_l = (side or "").lower()
    if side_l not in {"long", "short", "buy", "sell"}:
        return False, ""
    try:
        from .oil_entry_filters import measure_recent_move

        move = measure_recent_move(
            bars,
            interval_minutes=interval_minutes,
            priced_in_30m_pct=0.25,
            priced_in_60m_pct=0.45,
        )
        macd = compute_oil_macd(bars)
        notes: list[str] = []

        if move is not None:
            if side_l in {"short", "sell"} and (
                move.move_30m_pct >= 0.25 or move.move_60m_pct >= 0.45
            ):
                notes.append(
                    f"импульс ↑{move.move_30m_pct:+.2f}%/30м — SHORT запрещён"
                )
            if side_l in {"long", "buy"} and (
                move.move_30m_pct <= -0.25 or move.move_60m_pct <= -0.45
            ):
                notes.append(
                    f"импульс ↓{move.move_30m_pct:+.2f}%/30м — LONG запрещён"
                )

        if macd is not None and macd_blocks_side(macd, side_l):
            notes.append(f"{macd.line_ru} — против {side_l.upper()}")

        if notes:
            return True, notes[0]
    except Exception:
        return False, ""
    return False, ""
