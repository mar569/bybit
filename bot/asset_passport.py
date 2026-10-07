"""Паспорт актива перед reading: характер, не шаблон."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .bybit_klines import KlineBar
from .symbol_tiers import DEFAULT_MAJOR_SYMBOLS, SymbolTier


@dataclass(frozen=True)
class AssetPassport:
    symbol: str
    tier: str
    character_ru: str
    btc_note: str
    recent_ru: str
    trade_bias_hint: str

    def to_lines(self) -> list[str]:
        lines = [f"{self.symbol}: {self.character_ru}"]
        if self.recent_ru:
            lines.append(self.recent_ru)
        if self.btc_note:
            lines.append(self.btc_note)
        return lines


def build_asset_passport(
    symbol: str,
    bars: Sequence[KlineBar],
    *,
    btc_bars: Sequence[KlineBar] | None = None,
    post_pump: bool = False,
    post_dump: bool = False,
    range_position: float | None = None,
) -> AssetPassport:
    sym = (symbol or "???").upper()
    if sym in DEFAULT_MAJOR_SYMBOLS or sym.startswith("BTC"):
        tier = SymbolTier.MAJOR.value
    elif sym.endswith("USDT") and len(sym) <= 11:
        tier = SymbolTier.STANDARD.value
    else:
        tier = SymbolTier.ALT.value

    if tier == SymbolTier.MAJOR.value:
        character = "мажор · опираться на HTF и BTC"
    elif tier == SymbolTier.STANDARD.value:
        character = "средний альт · HTF + связь с BTC"
    else:
        character = "тонкий альт · только confluence, без погони"

    recent = ""
    if post_pump:
        recent = "свежий импульс вверх — не market-long у хая"
    elif post_dump:
        recent = "свежий импульс вниз — не шорт в дно без свипа"
    elif range_position is not None:
        if range_position >= 0.85:
            recent = "у верхней границы range"
        elif range_position <= 0.15:
            recent = "у нижней границы range"
        else:
            recent = "середина range — нужна зона, не середина"

    btc_note = ""
    if btc_bars and len(btc_bars) >= 10 and len(bars) >= 10 and not sym.startswith("BTC"):
        b0 = float(btc_bars[-10].close)
        b1 = float(btc_bars[-1].close)
        a0 = float(bars[-10].close)
        a1 = float(bars[-1].close)
        if b0 > 0 and a0 > 0:
            btc_chg = (b1 - b0) / b0 * 100.0
            alt_chg = (a1 - a0) / a0 * 100.0
            if abs(btc_chg) >= 0.5:
                if alt_chg * btc_chg > 0:
                    btc_note = f"движение с BTC ({btc_chg:+.1f}% / {alt_chg:+.1f}%)"
                else:
                    btc_note = f"расхождение с BTC (BTC {btc_chg:+.1f}%, альт {alt_chg:+.1f}%)"

    hint = "neutral"
    if post_pump and (range_position or 0) > 0.7:
        hint = "wait_pullback"
    elif post_dump and (range_position or 1) < 0.3:
        hint = "wait_bounce"

    return AssetPassport(
        symbol=sym,
        tier=tier,
        character_ru=character,
        btc_note=btc_note,
        recent_ru=recent,
        trade_bias_hint=hint,
    )
