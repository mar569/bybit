"""Фильтры качества входа UKOUSD: сессия, close-триггер, anti-chase."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .bybit_klines import KlineBar

try:
    from zoneinfo import ZoneInfo

    _MSK = ZoneInfo("Europe/Moscow")
except Exception:  # pragma: no cover
    _MSK = timezone(timedelta(hours=3), name="MSK")

# Bybit TradFi open hours (как oil_session)
_MON_OPEN_H = 1
_WEEKDAY_OPEN_H = 3


@dataclass(frozen=True)
class OilMoveSnapshot:
    """Сдвиг цены за 30м / 60м."""

    move_30m_pct: float
    move_60m_pct: float
    priced_in: bool  # уже «отыграно» — не chase
    note_ru: str


def _as_msk(now: datetime | None) -> datetime:
    now = now or datetime.now(tz=_MSK)
    if now.tzinfo is None:
        return now.replace(tzinfo=_MSK)
    return now.astimezone(_MSK)


def minutes_since_ukousd_open(*, now: datetime | None = None) -> float | None:
    """Минут с открытия текущей сессии; None если рынок закрыт."""
    from .oil_session import is_ukousd_session_open

    now = _as_msk(now)
    if not is_ukousd_session_open(now=now):
        return None
    wd = now.weekday()
    open_h = _MON_OPEN_H if wd == 0 else _WEEKDAY_OPEN_H
    opened = now.replace(hour=open_h, minute=0, second=0, microsecond=0)
    if now < opened:
        return None
    return (now - opened).total_seconds() / 60.0


def is_session_open_fragile(
    *,
    now: datetime | None = None,
    block_minutes: float = 20.0,
) -> bool:
    """True в первые N минут после открытия (тонкий стакан / гэп)."""
    mins = minutes_since_ukousd_open(now=now)
    if mins is None:
        return False
    return 0.0 <= mins < max(0.0, float(block_minutes))


def measure_recent_move(
    bars: Sequence[KlineBar] | None,
    *,
    interval_minutes: int = 5,
    priced_in_30m_pct: float = 0.8,
    priced_in_60m_pct: float = 1.2,
) -> OilMoveSnapshot | None:
    """Насколько цена уже уехала (новость «в цене»)."""
    if not bars or len(bars) < 6:
        return None
    im = max(5, int(interval_minutes))
    b30 = max(1, int(round(30 / im)))
    b60 = max(b30 + 1, int(round(60 / im)))
    px = float(bars[-1].close)
    p30 = float(bars[-1 - min(b30, len(bars) - 1)].close)
    p60 = float(bars[-1 - min(b60, len(bars) - 1)].close)
    if px <= 0 or p30 <= 0 or p60 <= 0:
        return None
    m30 = (px - p30) / p30 * 100.0
    m60 = (px - p60) / p60 * 100.0
    priced = abs(m30) >= priced_in_30m_pct or abs(m60) >= priced_in_60m_pct
    if priced:
        note = (
            f"цена уже {m30:+.2f}%/30м · {m60:+.2f}%/1ч — не chase, ждать откат/уровень"
        )
    elif abs(m30) >= 0.35 or abs(m60) >= 0.5:
        note = f"сдвиг {m30:+.2f}%/30м · {m60:+.2f}%/1ч"
    else:
        note = ""
    return OilMoveSnapshot(
        move_30m_pct=m30,
        move_60m_pct=m60,
        priced_in=priced,
        note_ru=note,
    )


def is_chase_for_side(
    side: str,
    move: OilMoveSnapshot | None,
    *,
    near_level: bool,
) -> bool:
    """Chase = уже сильный ход в сторону входа и цена НЕ у уровня."""
    if move is None or not move.priced_in or near_level:
        return False
    s = (side or "").lower()
    if s in {"long", "bullish", "open_long"}:
        return move.move_30m_pct >= 0.55 or move.move_60m_pct >= 0.9
    if s in {"short", "bearish", "open_short"}:
        return move.move_30m_pct <= -0.55 or move.move_60m_pct <= -0.9
    return False


def last_bar_closes_beyond(
    bars: Sequence[KlineBar] | None,
    *,
    side: str,
    level: float | None,
) -> bool:
    """Close последней свечи за уровнем (триггер пробоя)."""
    if not bars or level is None or level <= 0:
        return False
    close = float(bars[-1].close)
    s = (side or "").lower()
    if s in {"long", "bullish"}:
        return close > float(level)
    if s in {"short", "bearish"}:
        return close < float(level)
    return False


def oil_entry_block_reason(
    *,
    bars: Sequence[KlineBar] | None = None,
    side: str = "",
    near_level: bool = False,
    level: float | None = None,
    require_close_break: bool = False,
    interval_minutes: int = 5,
    now: datetime | None = None,
    session_block_minutes: float = 20.0,
    apply_session_filter: bool = True,
    apply_chase_filter: bool = True,
) -> str | None:
    """Почему вход сейчас плохой; None = можно."""
    if apply_session_filter and is_session_open_fragile(
        now=now, block_minutes=session_block_minutes,
    ):
        mins = minutes_since_ukousd_open(now=now) or 0.0
        return (
            f"открытие сессии ({mins:.0f}м) — тонкий стакан, ждать "
            f"{session_block_minutes:.0f}м"
        )

    move = measure_recent_move(bars, interval_minutes=interval_minutes)
    if apply_chase_filter and is_chase_for_side(side, move, near_level=near_level):
        return move.note_ru or "новость уже в цене — не chase"

    if require_close_break and level is not None:
        if not last_bar_closes_beyond(bars, side=side, level=level):
            return f"нет close за уровнем {level:.4g}"
    return None
