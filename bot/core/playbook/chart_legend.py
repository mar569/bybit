"""Concrete «what to see on chart» copy (replaces vague footer)."""
from __future__ import annotations

from html import escape
from typing import Any

from ...ta_analysis import fmt_price


def chart_legend_html(rbr: dict[str, Any] | None, *, phase: str = "") -> str:
    if not rbr:
        return (
            "<b>На графике:</b> уровни структуры и паттерн (если есть). "
            "<b>Сделки нет</b> — только наблюдение до триггера."
        )
    ph = (phase or str(rbr.get("phase") or "")).strip()
    floor = fmt_price(float(rbr.get("range_bottom") or 0))
    ceil = fmt_price(float(rbr.get("range_top") or 0))
    if ph == "fade_top":
        return (
            f"<b>На графике:</b> зелёная линия — <b>пол</b> {escape(floor)}, "
            f"жёлтая — <b>потолок</b> {escape(ceil)}; жёлтая зона — отказ у потолка. "
            f"<b>Входа нет</b> — ждём отказ и только потом сценарий вниз."
        )
    if ph == "await_break":
        return (
            f"<b>На графике:</b> серая зона — боковик {escape(floor)}–{escape(ceil)}. "
            f"<b>Входа нет</b> — нужен close ниже пола {escape(floor)}, затем retest к полу."
        )
    if ph == "retest":
        el = fmt_price(float(rbr.get("entry_lo") or 0))
        eh = fmt_price(float(rbr.get("entry_hi") or 0))
        return (
            f"<b>На графике:</b> пол {escape(floor)}; зона retest {escape(el)}–{escape(eh)} (синие линии). "
            f"Шорт только после отказа в retest — не в импульс."
        )
    if ph == "broken":
        return (
            f"<b>На графике:</b> цена под полом {escape(floor)}. "
            f"<b>Market off</b> — ждём откат к полу (retest) или продолжение без догонялки."
        )
    return (
        f"<b>На графике:</b> диапазон {escape(floor)}–{escape(ceil)}. "
        f"Сделка только по триггеру на уровнях."
    )
