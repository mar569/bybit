"""Пуш «разбор как у Ed» в чат анализов (отдельно от быстрого ENTRY)."""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Signal
    from .ta_analysis import TAAnalysisResult

from .range_breakdown_retest import get_rbr_from_ta, should_push_trader_deep_analysis
from .ta_analysis import fmt_price


def build_trader_deep_analysis_html(
    signal: "Signal",
    ta: "TAAnalysisResult",
) -> str:
    sym = (signal.symbol or "").upper()
    parts: list[str] = [f"📐 <b>Разбор {escape(sym)}</b> · {(signal.exchange or 'bybit').title()}"]

    rbr = get_rbr_from_ta(ta)
    if rbr:
        parts.append(f"<b>Сценарий:</b> {escape(str(rbr.get('label_ru', '')))}")
        parts.append(escape(str(rbr.get("story_ru", ""))[:520]))
        el, eh = rbr.get("entry_lo"), rbr.get("entry_hi")
        if el and eh:
            parts.append(f"Вход: <b>{fmt_price(float(el))}–{fmt_price(float(eh))}</b>")
        if rbr.get("stop"):
            parts.append(f"Стоп: <b>{fmt_price(float(rbr['stop']))}</b>")
        tgs = rbr.get("targets") or []
        if tgs:
            parts.append("Цели: " + " → ".join(fmt_price(float(t)) for t in tgs[:3]))

    html_brief = str(getattr(ta, "human_trade_brief_html", "") or "").strip()
    if html_brief:
        parts.append(html_brief)
    else:
        rep = str(getattr(ta, "scenario_report_html", "") or "").strip()
        if rep:
            parts.append(rep)

    iv = int(getattr(ta, "analysis_interval_minutes", 0) or 0)
    if iv >= 15:
        parts.append(f"<i>График и уровни: {iv}m · история сетапа.</i>")

    link = (signal.link or "").strip()
    if link:
        parts.append(f'<a href="{escape(link)}">CoinGlass / пара</a>')
    return "\n\n".join(parts)


def should_push_trader_deep_analysis_for_settings(ta: object, *, enabled: bool) -> bool:
    return bool(enabled and should_push_trader_deep_analysis(ta))
