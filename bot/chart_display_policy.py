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


def ed_chart_composite_enabled() -> bool:
    """PNG: паттерны + SMC + уровни + канал на одном графике (не один «режим»)."""
    if ed_chart_single_canvas_enabled():
        return False
    if not ed_pdf_chart_style_enabled():
        return False
    return _env_on("ED_CHART_COMPOSITE", default="1")


def ed_chart_single_canvas_enabled() -> bool:
    """Один PNG-пайплайн (chart_ed_canvas): уровни через LabelBoard, без TV/legacy слоёв."""
    return _env_on("ED_CHART_SINGLE_CANVAS", default="1")


def ed_chart_trader_canvas_enabled() -> bool:
    """Единый PNG: полная разметка трейдера (swing, паттерны, SMC/ICT, зоны) через composite."""
    if not ed_chart_single_canvas_enabled():
        return False
    return _env_on("ED_CHART_TRADER_CANVAS", default="1")


def ed_chart_evidence_canvas_enabled() -> bool:
    """PNG: один алгоритм — рисуем только подтверждённое TA (паттерн/SMC/канал), без дублей."""
    if not ed_chart_single_canvas_enabled():
        return False
    return _env_on("ED_CHART_EVIDENCE", default="1")


def ed_chart_manual_clean_enabled() -> bool:
    """Устар.: минимальный PNG (только если ED_CHART_EVIDENCE=0)."""
    return _env_on("ED_CHART_MANUAL_CLEAN", default="0")


def ed_chart_scenario_path_enabled() -> bool:
    """Пунктир «слив/отскок» на PNG — по умолчанию выкл (сценарий в подписи Telegram)."""
    return _env_on("ED_CHART_SCENARIO_PATH", default="0")


def ed_manual_chart_full_history_enabled() -> bool:
    """Ручной /ta: зум = вся загруженная история (импульс/дамп не «обрезать» сверху)."""
    return _env_on("ED_MANUAL_CHART_FULL_HISTORY", default="1")


def ed_chart_rich_manual_layers_enabled() -> bool:
    """На PNG: зоны, канал, ключевые уровни TA (не только 5 линий)."""
    return _env_on("ED_CHART_RICH_MANUAL", default="1")


def chart_pdf_setup_hint_enabled() -> bool:
    """Одна строка на PNG: что означает разметка (не дубль Telegram)."""
    if not ed_pdf_chart_style_enabled():
        return False
    if ed_chart_evidence_canvas_enabled() or ed_chart_manual_clean_enabled():
        return _env_on("ED_CHART_PDF_HINT", default="0")
    return _env_on("ED_CHART_PDF_HINT", default="1")


def ed_playbook_minimal_copy_enabled() -> bool:
    """Без «На графике…», дублей verdict/score, лишних простыней в Telegram."""
    if not ed_playbook_v3_enabled():
        return False
    return _env_on("ED_PLAYBOOK_MINIMAL_COPY", default="1")


def ed_telegram_candle_narrative_enabled() -> bool:
    """Пересказ цвета последних свечей в Telegram (по умолчанию выкл — это на PNG)."""
    return _env_on("ED_TELEGRAM_CANDLE_NARRATIVE", default="0")


def ed_playbook_intel_panel_enabled() -> bool:
    """Блок 📊 INTEL (OI/CVD/Liq) в подписи — по умолчанию выкл."""
    if ed_playbook_minimal_copy_enabled():
        return _env_on("ED_PLAYBOOK_INTEL", default="0")
    return _env_on("ED_PLAYBOOK_INTEL", default="0")


def ed_chart_visual_only() -> bool:
    """Без подписей цен, план-текста и label board на PNG."""
    return _env_on("ED_CHART_VISUAL_ONLY", default="1")


def ed_signal_chart_pa_analysis_enabled() -> bool:
    """Сигналы: PA/EMA/уровни как на нефтяном PNG (не trader+evidence canvas)."""
    return _env_on("ED_SIGNAL_CHART_PA", default="1")


def ed_manual_ta_pa_chart_enabled() -> bool:
    """Ручной /ta и Ed→анализ: тот же PA-слой, что у сигналов (без trader canvas)."""
    return _env_on("ED_MANUAL_TA_PA", default="1")


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
