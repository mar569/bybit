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


def chart_teaching_tags_enabled() -> bool:
    """Короткие метки на PNG: ПОЛ, TP/IN/SL, BOS, паттерн — без цифр."""
    return _env_on("ED_CHART_TEACHING_TAGS", default="1")


def chart_box_labels_enabled() -> bool:
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_BOX_LABELS", default="0")


def chart_plan_glyphs_enabled() -> bool:
    """TP / SL / IN на блоке плана — без цен."""
    if not ed_chart_visual_only():
        return chart_box_labels_enabled()
    return _env_on("ED_CHART_PLAN_GLYPHS", default="1")


def chart_anno_text_enabled() -> bool:
    """Коридор «пол/сопр», story caption, «ход ↓» на PNG."""
    if chart_teaching_tags_enabled():
        return True
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_ANNO_TEXT", default="0")


def chart_breakout_marker_labels_enabled() -> bool:
    if chart_teaching_tags_enabled():
        return True
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_BREAKOUT_LABELS", default="0")


def signal_chart_tradingview_enabled() -> bool:
    from .chart_unified import tradingview_experimental_enabled

    return tradingview_experimental_enabled()


def use_tradingview_chart_base(
    chart_source: str,
    *,
    signal_chart: bool = False,
    manual_ta_chart: bool = False,
) -> bool:
    from .chart_unified import use_tradingview_experimental

    return use_tradingview_experimental(
        chart_source,
        signal_chart=signal_chart,
        manual_ta_chart=manual_ta_chart,
    )
