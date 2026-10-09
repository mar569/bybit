"""Ed UI: короткий текст в Telegram, на PNG — только графика (паттерны, зоны, SMC)."""
from __future__ import annotations

import os


def _env_on(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def ed_playbook_v3_enabled() -> bool:
    """Единый PlaybookEngine для подписи и RBR WATCH gate."""
    return _env_on("ED_PLAYBOOK_V3", default="1")


def ed_chart_rich_analysis_enabled() -> bool:
    """Полный mpl-разбор (48ч, паттерны/тренды). Слои всё равно из ChartSpec — не отдельный «rich»-стек."""
    return _env_on("ED_CHART_RICH", default="1")


def ed_chart_visible_hours(default: int = 48) -> int | None:
    raw = os.environ.get("ED_CHART_VISIBLE_HOURS", "").strip()
    if not raw:
        return None
    try:
        return max(4, min(int(raw), 120))
    except ValueError:
        return None


def ed_chart_spec_layers_enabled() -> bool:
    """TV/mpl — один ChartSpec для всех символов (rich не отключает spec)."""
    if not ed_playbook_v3_enabled():
        return _env_on("ED_CHART_SPEC_LAYERS", default="0")
    return _env_on("ED_CHART_SPEC_LAYERS", default="1")


def ed_signal_legacy_reading_enabled() -> bool:
    """Scenario report / hybrid reading в авто-алертах (legacy)."""
    if ed_playbook_v3_enabled():
        return _env_on("ED_SIGNAL_LEGACY_READING", default="0")
    return _env_on("ED_SIGNAL_LEGACY_READING", default="1")


def ed_minimal_voice_enabled() -> bool:
    """1–2 фразы без цифр OI/CVD/уровней в подписи."""
    return _env_on("ED_MINIMAL_VOICE", default="1")


def ed_pdf_chart_style_enabled() -> bool:
    """PNG/TV как docs/.cursor_pdf_pages — паттерн или SMC, без RBR-каши."""
    return _env_on("ED_PDF_CHART_STYLE", default="1")


def ed_playbook_minimal_copy_enabled() -> bool:
    """Без «На графике…», дублей verdict/score, лишних простыней в Telegram."""
    if not ed_playbook_v3_enabled():
        return False
    return _env_on("ED_PLAYBOOK_MINIMAL_COPY", default="1")


def ed_chart_visual_only() -> bool:
    """Без подписей цен, план-текста и label board на PNG."""
    return _env_on("ED_CHART_VISUAL_ONLY", default="1")


def chart_teaching_tags_enabled() -> bool:
    """Короткие метки на PNG: R↑/R↓, TP/IN/SL, паттерн."""
    return _env_on("ED_CHART_TEACHING_TAGS", default="1")


def chart_story_banner_enabled() -> bool:
    """Нижняя простыня сценария на PNG (дублирует Telegram)."""
    return _env_on("ED_CHART_STORY_BANNER", default="0")


def chart_box_labels_enabled() -> bool:
    if ed_chart_visual_only():
        return False
    return _env_on("ED_CHART_BOX_LABELS", default="0")


def chart_trade_plan_on_chart_enabled() -> bool:
    """Блоки TP/SL/IN на PNG — только явно ED_CHART_TRADE_PLAN=1 (не с RBR-WATCH)."""
    return _env_on("ED_CHART_TRADE_PLAN", default="0")


def chart_entry_zone_tags_enabled() -> bool:
    """Метка «ВХОД» на горизонтали — только если явно включено."""
    if not chart_trade_plan_on_chart_enabled():
        return False
    return _env_on("ED_CHART_ENTRY_TAGS", default="0")


def chart_plan_glyphs_enabled() -> bool:
    """TP / SL / IN на блоке плана — без цен."""
    if not chart_trade_plan_on_chart_enabled():
        return False
    if not ed_chart_visual_only():
        return chart_box_labels_enabled()
    return _env_on("ED_CHART_PLAN_GLYPHS", default="0")


def chart_anno_text_enabled() -> bool:
    """Story caption на PNG — только если явно включён banner."""
    if chart_story_banner_enabled():
        return True
    if chart_teaching_tags_enabled():
        return False
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
