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


def manual_ta_skip_chart_when_no_entry() -> bool:
    """Ручной /ta: без PNG, если вход не готов (только текст)."""
    raw = os.environ.get("ED_MANUAL_TA_SKIP_CHART_IF_NO_ENTRY")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return True


def ed_entries_only_enabled(settings: object | None = None) -> bool:
    """Только готовые ENTRY в канал; без WATCH/«наблюдение»/RBR-слежения.

    ED_ENTRIES_ONLY=0 — смотреть settings (signal_telegram_entry_only, signal_watch_mode_enabled).
    По умолчанию (env не задан): включено.
    """
    raw = os.environ.get("ED_ENTRIES_ONLY")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if settings is not None:
        if bool(getattr(settings, "signal_telegram_entry_only", False)):
            return True
        if not bool(getattr(settings, "signal_watch_mode_enabled", True)):
            return True
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
