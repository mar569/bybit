"""Ed UI: короткий текст в Telegram, на PNG — только графика (паттерны, зоны, SMC)."""
from __future__ import annotations

import os


def _env_on(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ed_minimal_voice_enabled() -> bool:
    """1–2 фразы без цифр OI/CVD/уровней в подписи."""
    return _env_on("ED_MINIMAL_VOICE", default="1")


def ed_chart_visual_only() -> bool:
    """Без подписей цен, план-текста и label board на PNG."""
    return _env_on("ED_CHART_VISUAL_ONLY", default="1")


def chart_box_labels_enabled() -> bool:
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_BOX_LABELS", default="0")


def chart_anno_text_enabled() -> bool:
    """Коридор «пол/сопр», story caption, «ход ↓» на PNG."""
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_ANNO_TEXT", default="0")


def chart_breakout_marker_labels_enabled() -> bool:
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_BREAKOUT_LABELS", default="0")


def signal_chart_tradingview_enabled() -> bool:
    """TV widget + наш overlay (если Playwright доступен). Иначе matplotlib PRO."""
    return _env_on("SIGNAL_CHART_TV", default="1")


def use_tradingview_chart_base(
    chart_source: str,
    *,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
) -> bool:
    """Скрин TradingView как подложка для сигналов / manual TA."""
    src = (chart_source or "").strip().lower()
    if src in {"matplotlib", "mpl", "annotated_only"}:
        return False
    if src in {"tv_annotated", "tv", "tradingview"}:
        return True
    if signal_chart or manual_ta_chart:
        return signal_chart_tradingview_enabled()
    return False
