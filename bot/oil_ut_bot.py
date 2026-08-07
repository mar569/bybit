"""UT Bot Alerts (Pine v4) — ATR trailing Buy/Sell для UKOUSD.

Порт: QuantNomad / Derek Downey UT Bot Alerts.
Сигналы только на закрытой свече (exclude forming bar).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class OilUtBot:
    """Состояние UT Bot на последнем (закрытом) баре."""

    side: str  # long | short | flat
    buy: bool
    sell: bool
    trail: float
    src: float
    atr: float
    key_value: float
    atr_period: int
    # Полные ряды для графика (длина = число закрытых баров)
    trails: tuple[float, ...]
    positions: tuple[int, ...]  # 1 long, -1 short, 0 flat
    buy_flags: tuple[bool, ...]
    sell_flags: tuple[bool, ...]
    bar_bull: tuple[bool, ...]  # src > trail
    srcs: tuple[float, ...]
    line_ru: str


def _ohlc(bars: Sequence[object]) -> tuple[list[float], list[float], list[float], list[float]]:
    o: list[float] = []
    h: list[float] = []
    l: list[float] = []
    c: list[float] = []
    for b in bars:
        try:
            oo = float(getattr(b, "open", 0) or 0)
            hh = float(getattr(b, "high", 0) or 0)
            ll = float(getattr(b, "low", 0) or 0)
            cc = float(getattr(b, "close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if oo > 0 and hh > 0 and ll > 0 and cc > 0:
            o.append(oo)
            h.append(hh)
            l.append(ll)
            c.append(cc)
    return o, h, l, c


def _heikin_ashi_closes(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[float]:
    """HA close = (o+h+l+c)/4; HA open рекурсивно — для src как в Pine security(heikinashi)."""
    n = len(closes)
    if n == 0:
        return []
    ha_close = [(opens[i] + highs[i] + lows[i] + closes[i]) / 4.0 for i in range(n)]
    ha_open = [0.0] * n
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    return ha_close


def _wilder_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int,
) -> list[float]:
    """ATR как в TradingView atr() — RMA/Wilder. До прогрева — SMA TR."""
    n = len(closes)
    if n == 0:
        return []
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = [0.0] * n
    p = max(1, int(period))
    if n < p:
        # недостаточно — cumulative mean
        s = 0.0
        for i in range(n):
            s += tr[i]
            atr[i] = s / (i + 1)
        return atr
    s = sum(tr[:p])
    atr[p - 1] = s / p
    for i in range(p):
        if i < p - 1:
            atr[i] = sum(tr[: i + 1]) / (i + 1)
    for i in range(p, n):
        atr[i] = (atr[i - 1] * (p - 1) + tr[i]) / p
    return atr


def compute_oil_ut_bot(
    bars: Sequence[object] | None,
    *,
    key_value: float = 1.0,
    atr_period: int = 10,
    heikin_ashi: bool = False,
    exclude_forming: bool = True,
) -> OilUtBot | None:
    """Порт Pine UT Bot Alerts. exclude_forming=True — без последней незакрытой свечи."""
    if not bars:
        return None
    raw = list(bars)
    if exclude_forming and len(raw) >= 2:
        raw = raw[:-1]
    opens, highs, lows, closes = _ohlc(raw)
    need = max(15, int(atr_period) + 5)
    if len(closes) < need:
        return None

    srcs = (
        _heikin_ashi_closes(opens, highs, lows, closes)
        if heikin_ashi
        else list(closes)
    )
    atrs = _wilder_atr(highs, lows, closes, int(atr_period))
    a = float(key_value)
    n = len(srcs)

    trails = [0.0] * n
    positions = [0] * n
    buy_flags = [False] * n
    sell_flags = [False] * n
    bar_bull = [False] * n

    for i in range(n):
        n_loss = a * atrs[i]
        src = srcs[i]
        prev_trail = trails[i - 1] if i > 0 else 0.0
        prev_src = srcs[i - 1] if i > 0 else src

        # xATRTrailingStop := iff(...)
        if src > prev_trail and prev_src > prev_trail:
            trails[i] = max(prev_trail, src - n_loss)
        elif src < prev_trail and prev_src < prev_trail:
            trails[i] = min(prev_trail, src + n_loss)
        elif src > prev_trail:
            trails[i] = src - n_loss
        else:
            trails[i] = src + n_loss

        # pos — Pine сравнивает с trail[1] (prev_trail), не с текущим trail
        prev_pos = positions[i - 1] if i > 0 else 0
        if i > 0:
            if prev_src < prev_trail and src > prev_trail:
                positions[i] = 1
            elif prev_src > prev_trail and src < prev_trail:
                positions[i] = -1
            else:
                positions[i] = prev_pos
        else:
            positions[i] = 0

        # ema(src,1) ≡ src; crossover(a,b) = a[1]<=b[1] and a>b
        if i > 0:
            above = prev_src <= prev_trail and src > trails[i]
            below = prev_trail <= prev_src and trails[i] > src
        else:
            above = False
            below = False

        buy_flags[i] = src > trails[i] and above
        sell_flags[i] = src < trails[i] and below
        bar_bull[i] = src > trails[i]

    last = n - 1
    buy = buy_flags[last]
    sell = sell_flags[last]
    pos = positions[last]
    # Режим для гейта/цвета = положение относительно trail (как barbuy/barsell на TV)
    if buy:
        side = "long"
    elif sell:
        side = "short"
    elif bar_bull[last]:
        side = "long"
    elif srcs[last] < trails[last]:
        side = "short"
    elif pos == 1:
        side = "long"
    elif pos == -1:
        side = "short"
    else:
        side = "flat"

    if buy:
        line = f"UT Buy · trail {trails[last]:.2f} · ATR{atr_period}×{a:g}"
    elif sell:
        line = f"UT Sell · trail {trails[last]:.2f} · ATR{atr_period}×{a:g}"
    elif side == "long":
        line = f"UT long · trail {trails[last]:.2f}"
    elif side == "short":
        line = f"UT short · trail {trails[last]:.2f}"
    else:
        line = f"UT flat · trail {trails[last]:.2f}"

    return OilUtBot(
        side=side,
        buy=buy,
        sell=sell,
        trail=trails[last],
        src=srcs[last],
        atr=atrs[last],
        key_value=a,
        atr_period=int(atr_period),
        trails=tuple(trails),
        positions=tuple(positions),
        buy_flags=tuple(buy_flags),
        sell_flags=tuple(sell_flags),
        bar_bull=tuple(bar_bull),
        srcs=tuple(srcs),
        line_ru=line,
    )


def ut_blocks_side(ut: OilUtBot | None, side: str) -> bool:
    """True если UT запрещает сторону (long против short-режима и наоборот)."""
    if ut is None:
        return False
    side_l = (side or "").lower()
    if side_l in {"long", "buy"} and ut.side == "short":
        return True
    if side_l in {"short", "sell"} and ut.side == "long":
        return True
    return False


def format_oil_ut_alert(ut: OilUtBot, *, interval_minutes: int = 5) -> str:
    """Подпись к PNG — как метки Buy/Sell на TV."""
    if ut.buy:
        emoji = "🟢"
        title = "UT Buy"
        how = "На Bybit TradFi жми <b>Buy</b> по <b>UKOUSD.s</b> (Brent Cash)."
    elif ut.sell:
        emoji = "🔴"
        title = "UT Sell"
        how = "На Bybit TradFi жми <b>Sell</b> по <b>UKOUSD.s</b> (Brent Cash)."
    else:
        emoji = "⚪"
        title = "UT"
        how = "Нет нового flip."
    return "\n".join(
        [
            f"🛢 <b>{title}</b> {emoji} · UKOUSD · {interval_minutes}m",
            f"<b>{ut.line_ru}</b>",
            "",
            how,
            f"Цена: <b>{ut.src:.2f}</b> · trail: <b>{ut.trail:.2f}</b>",
            f"Key <b>{ut.key_value:g}</b> · ATR <b>{ut.atr_period}</b> · ATR≈{ut.atr:.3f}",
            "",
            "<i>UT Bot Alerts (как на TradingView). Не финсовет.</i>",
        ]
    )
