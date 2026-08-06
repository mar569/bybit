"""После flash: не входить сразу — ждать реакцию рынка 5–15м + 5м свечу."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class OilNewsReaction:
    """Ожидание подтверждения после срочной новости."""

    started_ts: float
    ready_ts: float
    expire_ts: float
    impact: str  # bullish | bearish
    title: str
    source: str = ""
    confirmed: bool | None = None  # None=wait, True/False after check
    note_ru: str = ""


def start_reaction(
    *,
    impact: str,
    title: str,
    source: str = "",
    wait_minutes: float = 10.0,
    expire_minutes: float = 45.0,
    now_ts: float | None = None,
) -> OilNewsReaction | None:
    if impact not in {"bullish", "bearish"}:
        return None
    t0 = now_ts if now_ts is not None else time.time()
    wait_m = max(5.0, min(20.0, float(wait_minutes)))
    exp_m = max(wait_m + 5.0, float(expire_minutes))
    return OilNewsReaction(
        started_ts=t0,
        ready_ts=t0 + wait_m * 60.0,
        expire_ts=t0 + exp_m * 60.0,
        impact=impact,
        title=(title or "")[:160],
        source=(source or "")[:60],
        note_ru=f"Ждём реакцию ~{wait_m:.0f}м (не первая свеча)",
    )


def reaction_blocks_entry(reaction: OilNewsReaction | None, *, now_ts: float | None = None) -> str | None:
    """Причина блока входа или None."""
    if reaction is None:
        return None
    t0 = now_ts if now_ts is not None else time.time()
    if t0 > reaction.expire_ts:
        return None
    if t0 < reaction.ready_ts:
        left = int((reaction.ready_ts - t0) / 60.0) + 1
        return f"⏳ Реакция на новость · ждать ещё ~{left}м"
    if reaction.confirmed is True:
        return None
    if reaction.confirmed is False:
        return "⏳ Реакция против новости · вход OFF"
    return "⏳ Реакция слабая · ждём закрытие 5м"


def confirm_reaction_with_bars(
    reaction: OilNewsReaction,
    bars: Sequence[Any] | None,
    *,
    now_ts: float | None = None,
    min_move_pct: float = 0.12,
) -> OilNewsReaction:
    """После wait: смотрим закрытие последней 5м vs направление новости."""
    t0 = now_ts if now_ts is not None else time.time()
    if t0 < reaction.ready_ts:
        return reaction
    if t0 > reaction.expire_ts:
        return reaction

    if not bars or len(bars) < 3:
        reaction.confirmed = None
        reaction.note_ru = "Нет баров для подтверждения"
        return reaction

    # Последняя закрытая свеча (если последняя «текущая» — берём -2)
    c1 = float(getattr(bars[-1], "close", 0) or 0)
    o1 = float(getattr(bars[-1], "open", 0) or 0)
    c0 = float(getattr(bars[-2], "close", 0) or 0)
    if c1 <= 0 or c0 <= 0:
        return reaction
    # Движение за последнюю завершённую свечу + короткий импульс 2 бара
    bar_pct = (c1 - o1) / o1 * 100.0 if o1 > 0 else 0.0
    impulse = (c1 - c0) / c0 * 100.0

    want_up = reaction.impact == "bullish"
    moved_with = (impulse >= min_move_pct and bar_pct >= 0) if want_up else (
        impulse <= -min_move_pct and bar_pct <= 0
    )
    moved_against = (impulse <= -min_move_pct) if want_up else (impulse >= min_move_pct)

    if moved_with:
        reaction.confirmed = True
        reaction.note_ru = f"5м подтвердила {'↑' if want_up else '↓'} ({impulse:+.2f}%)"
    elif moved_against:
        reaction.confirmed = False
        reaction.note_ru = f"5м против новости ({impulse:+.2f}%) — WAIT"
    else:
        reaction.confirmed = None
        reaction.note_ru = f"Реакция слабая ({impulse:+.2f}%) — ещё ждать"
    return reaction


def format_reaction_wait_note(reaction: OilNewsReaction) -> str:
    title = reaction.title[:120]
    nuance = ""
    try:
        from .oil_monitor import _is_hormuz_deal_condition

        if _is_hormuz_deal_condition((reaction.title or "").lower()):
            nuance = (
                "\n⚠️ Условие сделки (суда США/Израиля) — "
                "не читай как «сделку подписали → шорт»"
            )
    except Exception:
        pass
    return (
        f"⏳ <b>Не входить сразу</b>\n"
        f"<i>{reaction.note_ru}</i>\n"
        f"{title}"
        f"{nuance}"
    )
