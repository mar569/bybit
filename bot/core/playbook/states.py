from __future__ import annotations

from enum import Enum


class PlaybookState(str, Enum):
    OBSERVE = "observe"
    WATCH = "watch"
    ARMED = "armed"
    NO_TRADE = "no_trade"

    @property
    def badge_ru(self) -> str:
        return {
            PlaybookState.OBSERVE: "👁 Наблюдение",
            PlaybookState.WATCH: "📋 WATCH · без входа",
            PlaybookState.ARMED: "🎯 Retest · триггер",
            PlaybookState.NO_TRADE: "⛔ Без сделки",
        }[self]
