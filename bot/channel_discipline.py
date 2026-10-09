"""Разделение ролей каналов: сигнал ≠ разбор ≠ слежение (без каши в одном чате)."""
from __future__ import annotations

import os
import time


def _on(name: str, *, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ed_channel_discipline_enabled() -> bool:
    """Один тип сообщения за событие; без хвостов AI/INTEL/deep после сигнала."""
    return _on("ED_CHANNEL_DISCIPLINE", default="1")


def signal_extra_messages_enabled() -> bool:
    """Второе/третье сообщение после алерта (snippet, situational AI, LLM)."""
    if ed_channel_discipline_enabled():
        return _on("ED_SIGNAL_EXTRA_MSG", default="0")
    return _on("ED_SIGNAL_EXTRA_MSG", default="1")


def signal_pro_analysis_chat_enabled() -> bool:
    if ed_channel_discipline_enabled():
        return _on("ED_SIGNAL_PRO_ANALYSIS", default="0")
    return True


def trader_deep_on_signal_enabled() -> bool:
    if ed_channel_discipline_enabled():
        return _on("ED_TRADER_DEEP_ON_SIGNAL", default="0")
    return True


def proactive_intel_on_watch_enabled() -> bool:
    if ed_channel_discipline_enabled():
        return _on("ED_PROACTIVE_INTEL", default="0")
    return True


def manual_ta_blocks_signal_noise(
    symbol: str,
    *,
    last_manual: dict[str, float] | None,
    window_sec: float = 1800,
) -> bool:
    """Недавний ручной /ta по символу — не слать авто-разбор сканера."""
    if not last_manual or not symbol:
        return False
    ts = last_manual.get(symbol.upper(), 0.0)
    return ts > 0 and (time.time() - ts) < window_sec
