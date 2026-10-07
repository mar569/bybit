"""BTC regime — фильтр для альтов: не лонгуем альты против сильного BTC dump."""
from __future__ import annotations

from typing import Sequence

from .bybit_klines import KlineBar


def btc_regime_label(
    btc_bars: Sequence[KlineBar] | None,
    *,
    lookback: int = 12,
    block_dump_pct: float = 0.35,
    block_pump_pct: float = 0.35,
) -> tuple[str, float, str]:
    """
    Returns (regime, change_pct, note_ru).
    regime: bullish | bearish | neutral
    """
    if not btc_bars or len(btc_bars) < 4:
        return "neutral", 0.0, ""
    n = min(lookback, len(btc_bars) - 1)
    start = float(btc_bars[-1 - n].close)
    end = float(btc_bars[-1].close)
    if start <= 0:
        return "neutral", 0.0, ""
    chg = (end - start) / start * 100.0
    if chg <= -block_dump_pct:
        return "bearish", chg, f"BTC {chg:+.2f}% — альт-LONG только от сильной зоны"
    if chg >= block_pump_pct:
        return "bullish", chg, f"BTC {chg:+.2f}% — альт-SHORT осторожнее, не в нож"
    return "neutral", chg, ""


def btc_blocks_alt_side(
    side: str,
    regime: str,
    *,
    symbol: str = "",
) -> str:
    sym = (symbol or "").upper()
    if sym.startswith("BTC"):
        return ""
    side = (side or "").lower()
    reg = (regime or "").lower()
    if reg == "bearish" and side == "long":
        return "BTC в минусе — не открываем альт-LONG без разворота BTC"
    if reg == "bullish" and side == "short":
        return "BTC в плюсе — не шортим альт без exhaustion/зоны"
    return ""
