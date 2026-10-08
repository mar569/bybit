"""Короткие метки на PNG (без простыни цен): ПОЛ, TP, паттерн, сценарий."""
from __future__ import annotations

from html import escape

from .bybit_klines import KlineBar
from .chart_label_layout import LabelBoard
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult


def enrich_teaching_label_board(
    board: LabelBoard,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    mode: str,
) -> None:
    if not bars:
        return
    cur = float(getattr(ta, "current_price", 0) or bars[-1].close)
    from .plan_staleness import plan_is_stale

    rbr = get_rbr_from_ta(ta)
    if rbr:
        floor = float(rbr.get("range_bottom") or 0)
        ceil = float(rbr.get("range_top") or 0)
        phase = str(rbr.get("phase") or "")
        if floor > 0:
            board.add(floor, "ПОЛ", "#8b949e", side="right", priority=95, ref=cur, skip_if_reserved=False)
        if ceil > 0 and phase not in {"broken", "retest"}:
            board.add(ceil, "ПОТОЛОК", "#f0c040", side="right", priority=94, ref=cur, skip_if_reserved=False)
        el, eh = float(rbr.get("entry_lo") or 0), float(rbr.get("entry_hi") or 0)
        show_entry = el > 0 and eh > el and not plan_is_stale(ta)
        if show_entry and phase == "await_break" and floor > 0 and cur < floor * 0.996:
            show_entry = False
        if show_entry:
            mid = (el + eh) / 2.0
            if abs(cur - mid) / max(mid, 1e-12) <= 0.045:
                board.add(mid, "ВХОД", "#e3b341", side="right", priority=93, ref=cur, skip_if_reserved=False)

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if mode in {"range_wait", "observation"} and brk > 0 and brdn > 0:
        board.add(brk, "СОПР", "#f0c040", side="right", priority=88, ref=cur, skip_if_reserved=False)
        board.add(brdn, "ПОДД", "#3fb950", side="right", priority=88, ref=cur, skip_if_reserved=False)

    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        lv = float(smc.structure_break_level)
        from .chart_analysis_text import structure_break_label_ru

        kind = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"))
        board.add(lv, kind[:8].upper(), "#f0c040", side="left", priority=70, ref=cur, skip_if_reserved=False)


def primary_pattern_tag(ta: TAAnalysisResult) -> str:
    primary = getattr(ta, "primary_chart_pattern", None)
    if not primary:
        return ""
    name = str(getattr(primary, "label_ru", "") or getattr(primary, "kind", "") or "").strip()
    if not name:
        return ""
    return name[:48]


def story_banner_line(ta: TAAnalysisResult, *, mode: str) -> str:
    rbr = get_rbr_from_ta(ta)
    if rbr:
        phase = str(rbr.get("phase") or "")
        if phase == "fade_top":
            return "Вынос → отказ → шорт (не в импульс вверх)"
        if phase == "await_break":
            return "Ждём закреп под полом → retest → шорт"
        if phase == "retest":
            return "Retest пола — шорт после подтверждения"
    tag = primary_pattern_tag(ta)
    if tag:
        return tag
    side = str(getattr(ta, "action_priority", "") or "").lower()
    if mode == "range_wait":
        return "Коридор — вход только по триггеру на графике"
    if side == "short":
        return "Сценарий вниз — без догонялки"
    if side == "long":
        return "Сценарий вверх — без догонялки"
    return "Наблюдение — триггеры на графике"


def story_banner_html(ta: TAAnalysisResult, *, mode: str) -> str:
    return escape(story_banner_line(ta, mode=mode))
