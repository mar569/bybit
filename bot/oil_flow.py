"""Прокси order-flow по свечам нефти (не DOM ICE — объём + candle delta)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar


@dataclass(frozen=True)
class OilFlowProxy:
    """Сводка потока по последним свечам UKOUSD-прокси."""

    bias: str  # buy | sell | neutral
    session_volume: float
    recent_volume: float
    prev_volume: float
    volume_ratio: float  # recent / prev (1.0 = как раньше)
    delta_recent: float
    delta_session: float
    buy_share_pct: float  # 0–100 по recent
    bars_used: int
    lookback: int
    note_ru: str


def _bar_delta(bar: KlineBar) -> tuple[float, float, float]:
    """(delta, buy_vol, sell_vol) — классический OHLC-прокси."""
    vol = max(0.0, float(bar.volume or 0.0))
    hi = float(bar.high)
    lo = float(bar.low)
    cl = float(bar.close)
    op = float(bar.open)
    rng = hi - lo
    if vol <= 0:
        return 0.0, 0.0, 0.0
    if rng <= 1e-12:
        if cl >= op:
            return vol, vol, 0.0
        return -vol, 0.0, vol
    # Доля покупок ≈ близость close к high
    buy_share = max(0.0, min(1.0, (cl - lo) / rng))
    buy_vol = vol * buy_share
    sell_vol = vol * (1.0 - buy_share)
    delta = buy_vol - sell_vol
    return delta, buy_vol, sell_vol


def compute_oil_flow_proxy(
    bars: Sequence[KlineBar],
    *,
    lookback: int = 12,
    session_bars: int | None = None,
) -> OilFlowProxy | None:
    """Считает объём сессии и delta по последним N свечам."""
    if not bars or len(bars) < 4:
        return None
    lb = max(3, min(int(lookback), len(bars) // 2 if len(bars) >= 6 else len(bars)))
    sess_n = int(session_bars) if session_bars else min(len(bars), max(lb * 2, 24))
    sess_n = max(lb, min(sess_n, len(bars)))

    session = list(bars[-sess_n:])
    recent = list(bars[-lb:])
    prev = list(bars[-(lb * 2) : -lb]) if len(bars) >= lb * 2 else list(bars[:-lb])

    def _sum_vol(chunk: list[KlineBar]) -> float:
        return sum(max(0.0, float(b.volume or 0)) for b in chunk)

    def _sum_delta(chunk: list[KlineBar]) -> tuple[float, float, float]:
        d = buy = sell = 0.0
        for b in chunk:
            di, bu, se = _bar_delta(b)
            d += di
            buy += bu
            sell += se
        return d, buy, sell

    sess_vol = _sum_vol(session)
    recent_vol = _sum_vol(recent)
    prev_vol = _sum_vol(prev) if prev else 0.0
    delta_sess, _, _ = _sum_delta(session)
    delta_rec, buy_r, sell_r = _sum_delta(recent)
    tot_bs = buy_r + sell_r
    buy_share = (buy_r / tot_bs * 100.0) if tot_bs > 1e-9 else 50.0
    vol_ratio = (recent_vol / prev_vol) if prev_vol > 1e-9 else 1.0

    # Bias: delta + доля покупок
    if delta_rec > 0 and buy_share >= 55:
        bias = "buy"
    elif delta_rec < 0 and buy_share <= 45:
        bias = "sell"
    elif abs(delta_rec) > 0 and buy_share >= 58:
        bias = "buy"
    elif abs(delta_rec) > 0 and buy_share <= 42:
        bias = "sell"
    else:
        bias = "neutral"

    # Объём выше предыдущего окна усиливает формулировку
    if vol_ratio >= 1.35 and bias != "neutral":
        note = "объём вырос vs прошлое окно — поток сильнее"
    elif vol_ratio <= 0.7 and bias != "neutral":
        note = "объём сжался — сигнал потока слабее"
    elif bias == "neutral":
        note = "нет явного перевеса buy/sell"
    else:
        note = "умеренный перевес агрессора по свечам"

    return OilFlowProxy(
        bias=bias,
        session_volume=sess_vol,
        recent_volume=recent_vol,
        prev_volume=prev_vol,
        volume_ratio=round(vol_ratio, 2),
        delta_recent=delta_rec,
        delta_session=delta_sess,
        buy_share_pct=round(buy_share, 1),
        bars_used=sess_n,
        lookback=lb,
        note_ru=note,
    )


def _fmt_vol(v: float) -> str:
    av = abs(v)
    if av >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if av >= 1_000:
        return f"{v / 1_000:.1f}K"
    if av >= 10:
        return f"{v:.0f}"
    return f"{v:.2f}"


def format_oil_flow_block(flow: OilFlowProxy, *, compact: bool = False) -> str:
    """HTML-блок для дайджеста /oil."""
    mark = {"buy": "🟢", "sell": "🔴", "neutral": "⚪"}.get(flow.bias, "⚪")
    bias_ru = {"buy": "BUY", "sell": "SELL", "neutral": "NEUTRAL"}.get(
        flow.bias, flow.bias
    )
    if compact:
        note = (flow.note_ru or "").split(".")[0][:70]
        return (
            f"📡 {mark} {bias_ru} · Δ{_fmt_vol(flow.delta_recent)} · "
            f"vol x{flow.volume_ratio:g}"
            + (f" · {note}" if note else "")
        )
    delta_sign = "+" if flow.delta_recent >= 0 else ""
    sess_sign = "+" if flow.delta_session >= 0 else ""
    lines = [
        f"📡 <b>Поток</b> · {mark} <b>{bias_ru}</b>",
        f"• Δ recent <b>{delta_sign}{_fmt_vol(flow.delta_recent)}</b> · "
        f"buy {flow.buy_share_pct:g}% · vol x{flow.volume_ratio:g}",
        f"• {_esc(flow.note_ru)}",
    ]
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
