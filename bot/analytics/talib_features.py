"""TA-Lib (https://github.com/TA-Lib/ta-lib-python) — единые RSI/MACD/BB/ATR/ADX/CDL.

Если `import talib` недоступен, функции возвращают пустой snapshot (бот работает как раньше).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_talib = None
_talib_import_attempted = False

CDL_RU: dict[str, str] = {
    "CDLHAMMER": "Молот",
    "CDLINVERTEDHAMMER": "Перев. молот",
    "CDLENGULFING": "Поглощение",
    "CDLMORNINGSTAR": "Утр. звезда",
    "CDLEVENINGSTAR": "Веч. звезда",
    "CDLDOJI": "Доджи",
    "CDLDRAGONFLYDOJI": "Доджи «стрекоза»",
    "CDLGRAVESTONEDOJI": "Доджи «надгробие»",
    "CDLSHOOTINGSTAR": "Падающая звезда",
    "CDLHANGINGMAN": "Висельник",
    "CDL3BLACKCROWS": "3 вороны",
    "CDL3WHITESOLDIERS": "3 солдата",
    "CDLHARAMI": "Харами",
    "CDLPIERCING": "Просвет",
    "CDLDARKCLOUDCOVER": "Тёмное облако",
}


@dataclass(frozen=True)
class TalibSnapshot:
    available: bool = False
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr_14: float | None = None
    adx_14: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_pct_b: float | None = None
    candle_patterns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_hist": self.macd_hist,
            "atr_14": self.atr_14,
            "adx_14": self.adx_14,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "bb_pct_b": self.bb_pct_b,
            "candle_patterns": list(self.candle_patterns),
            "notes": list(self.notes),
        }


def talib_available() -> bool:
    global _talib, _talib_import_attempted
    if _talib_import_attempted:
        return _talib is not None
    _talib_import_attempted = True
    try:
        import talib as tl  # type: ignore[import-untyped]

        _talib = tl
    except ImportError:
        _talib = None
        logger.debug("TA-Lib not installed — using internal indicators only")
    return _talib is not None


def _last_valid(arr: Any) -> float | None:
    if arr is None or len(arr) == 0:
        return None
    import math

    for v in reversed(arr):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f):
            continue
        return f
    return None


def _bars_ohlcv(bars: list[Any]) -> tuple[Any, Any, Any, Any, Any] | None:
    if len(bars) < 20:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    o = np.array([float(b.open) for b in bars], dtype=float)
    h = np.array([float(b.high) for b in bars], dtype=float)
    l = np.array([float(b.low) for b in bars], dtype=float)
    c = np.array([float(b.close) for b in bars], dtype=float)
    v = np.array([float(getattr(b, "volume", 0) or 0) for b in bars], dtype=float)
    return o, h, l, c, v


def compute_rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """RSI для дивергенций: TA-Lib если есть, иначе Wilder (TradingView)."""
    if talib_available() and len(closes) >= period + 5:
        import numpy as np

        arr = np.array(closes, dtype=float)
        rsi = _talib.RSI(arr, timeperiod=period)
        out = [float(x) if x == x else 50.0 for x in rsi]
        return out
    from ..rsi_divergence import compute_rsi_wilder

    return compute_rsi_wilder(closes, period=period)


def compute_talib_snapshot(bars: list[Any]) -> TalibSnapshot:
    if not talib_available():
        return TalibSnapshot(available=False)
    ohlcv = _bars_ohlcv(bars)
    if ohlcv is None:
        return TalibSnapshot(available=True)
    o, h, l, c, _v = ohlcv
    tl = _talib
    rsi = tl.RSI(c, timeperiod=14)
    macd, macd_sig, macd_hist = tl.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
    atr = tl.ATR(h, l, c, timeperiod=14)
    adx = tl.ADX(h, l, c, timeperiod=14)
    upper, middle, lower = tl.BBANDS(c, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)

    rsi_last = _last_valid(rsi)
    macd_last = _last_valid(macd)
    sig_last = _last_valid(macd_sig)
    hist_last = _last_valid(macd_hist)
    atr_last = _last_valid(atr)
    adx_last = _last_valid(adx)
    u_last = _last_valid(upper)
    m_last = _last_valid(middle)
    lo_last = _last_valid(lower)
    close_last = float(c[-1])

    bb_pct: float | None = None
    if u_last is not None and lo_last is not None and u_last > lo_last:
        bb_pct = (close_last - lo_last) / (u_last - lo_last)

    patterns: list[str] = []
    for name in CDL_RU:
        if not hasattr(tl, name):
            continue
        fn = getattr(tl, name)
        try:
            out = fn(o, h, l, c)
        except Exception:
            continue
        val = _last_valid(out)
        if val is None or abs(val) < 1e-9:
            continue
        label = CDL_RU[name]
        if val > 0:
            patterns.append(f"🟢 {label}")
        else:
            patterns.append(f"🔴 {label}")

    notes: list[str] = []
    if adx_last is not None and adx_last < 18:
        notes.append("ADX низкий — рынок без тренда, импульсные сигналы осторожнее")
    if bb_pct is not None:
        if bb_pct >= 0.95:
            notes.append("у верхней полосы BB — перегрев long")
        elif bb_pct <= 0.05:
            notes.append("у нижней полосы BB — перегрев short")
    if hist_last is not None and macd_last is not None and sig_last is not None:
        if hist_last > 0 and macd_last > sig_last:
            notes.append("MACD бычий")
        elif hist_last < 0 and macd_last < sig_last:
            notes.append("MACD медвежий")

    return TalibSnapshot(
        available=True,
        rsi_14=rsi_last,
        macd=macd_last,
        macd_signal=sig_last,
        macd_hist=hist_last,
        atr_14=atr_last,
        adx_14=adx_last,
        bb_upper=u_last,
        bb_middle=m_last,
        bb_lower=lo_last,
        bb_pct_b=bb_pct,
        candle_patterns=tuple(patterns[:4]),
        notes=tuple(notes[:4]),
    )

