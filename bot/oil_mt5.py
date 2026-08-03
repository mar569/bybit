"""Свечи Bybit TradFi через MetaTrader 5 — ровно UKOUSD.s (Brent Crude Oil Cash).

Публичного API у Bybit для CFD UKOUSD.s нет. Единственный точный источник —
локальный терминал MT5, залогиненный в Bybit TradFi (как на вашем графике).

Требования (Windows):
  1) Установить MetaTrader 5 от Bybit TradFi и войти в аккаунт
  2) pip install MetaTrader5
  3) (опц.) MT5_TERMINAL_PATH / MT5_LOGIN / MT5_PASSWORD / MT5_SERVER в .env
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .bybit_klines import KlineBar

logger = logging.getLogger(__name__)

# Символы как в MT5 / Bybit TradFi UI
OIL_MT5_BRENT = "UKOUSD.s"  # Brent Crude Oil Cash
OIL_MT5_WTI = "USOIL"  # WTI cash (если есть в Market Watch)

_TIMEFRAME_MAP = {
    1: "TIMEFRAME_M1",
    3: "TIMEFRAME_M3",
    5: "TIMEFRAME_M5",
    15: "TIMEFRAME_M15",
    30: "TIMEFRAME_M30",
    60: "TIMEFRAME_H1",
}

# Последний успешный источник (для подписей в чате)
_last_source: str = ""


def get_oil_price_source() -> str:
    return _last_source


def set_oil_price_source(source: str) -> None:
    global _last_source
    _last_source = source


def mt5_available() -> bool:
    try:
        import MetaTrader5 as mt5  # noqa: F401
        return True
    except ImportError:
        return False


def _mt5_timeframe(mt5: Any, interval_minutes: int) -> int | None:
    name = _TIMEFRAME_MAP.get(int(interval_minutes))
    if not name:
        return None
    return int(getattr(mt5, name))


def _ensure_symbol_visible(mt5: Any, symbol: str) -> bool:
    info = mt5.symbol_info(symbol)
    if info is None:
        return False
    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            return False
    return True


def _initialize_mt5() -> Any | None:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        logger.debug("MetaTrader5 package not installed")
        return None

    path = (os.getenv("MT5_TERMINAL_PATH") or "").strip() or None
    login_raw = (os.getenv("MT5_LOGIN") or "").strip()
    password = (os.getenv("MT5_PASSWORD") or "").strip() or None
    server = (os.getenv("MT5_SERVER") or "").strip() or None

    kwargs: dict[str, Any] = {}
    if path:
        kwargs["path"] = path
    if login_raw and password and server:
        try:
            kwargs["login"] = int(login_raw)
        except ValueError:
            logger.warning("MT5_LOGIN must be numeric account id")
            return None
        kwargs["password"] = password
        kwargs["server"] = server

    ok = mt5.initialize(**kwargs) if kwargs else mt5.initialize()
    if not ok:
        err = mt5.last_error()
        logger.info("MT5 initialize failed: %s", err)
        return None
    return mt5


def fetch_mt5_oil_bars(
    symbol: str = OIL_MT5_BRENT,
    *,
    interval_minutes: int = 5,
    limit: int | None = None,
) -> list[KlineBar]:
    """Свечи ровно с MT5 символа (UKOUSD.s). Пустой список = недоступно."""
    mt5 = _initialize_mt5()
    if mt5 is None:
        return []

    try:
        tf = _mt5_timeframe(mt5, interval_minutes)
        if tf is None:
            logger.warning("MT5 unsupported interval %sm", interval_minutes)
            return []

        if not _ensure_symbol_visible(mt5, symbol):
            # иногда брокер отдаёт без .s
            alt = symbol.replace(".s", "") if symbol.endswith(".s") else f"{symbol}.s"
            if alt != symbol and _ensure_symbol_visible(mt5, alt):
                symbol = alt
            else:
                logger.info("MT5 symbol %s not in Market Watch", symbol)
                return []

        lim = min(max(int(limit or 200), 24), 1000)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, lim)
        if rates is None or len(rates) < 24:
            logger.info(
                "MT5 rates empty for %s: %s",
                symbol,
                mt5.last_error(),
            )
            return []

        bars: list[KlineBar] = []
        for row in rates:
            try:
                bars.append(
                    KlineBar(
                        open_time=float(row["time"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["tick_volume"]),
                    )
                )
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        bars.sort(key=lambda b: b.open_time)
        if len(bars) >= 24:
            set_oil_price_source(f"MT5 {symbol}")
            logger.info(
                "Oil bars from MT5 %s: %d bars, last=%.4f",
                symbol,
                len(bars),
                bars[-1].close,
            )
        return bars
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def fetch_mt5_oil_tick(symbol: str = OIL_MT5_BRENT) -> float | None:
    """Последний mid (bid+ask)/2 для UKOUSD.s."""
    mt5 = _initialize_mt5()
    if mt5 is None:
        return None
    try:
        if not _ensure_symbol_visible(mt5, symbol):
            alt = symbol.replace(".s", "") if symbol.endswith(".s") else f"{symbol}.s"
            if alt != symbol and _ensure_symbol_visible(mt5, alt):
                symbol = alt
            else:
                return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        bid = float(getattr(tick, "bid", 0) or 0)
        ask = float(getattr(tick, "ask", 0) or 0)
        if bid > 0 and ask > 0:
            set_oil_price_source(f"MT5 {symbol}")
            return (bid + ask) / 2.0
        last = float(getattr(tick, "last", 0) or 0)
        return last if last > 0 else None
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass
