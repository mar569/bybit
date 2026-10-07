"""Нейтральные обновления контекста — без «можно шорт / через 5 мин лонг»."""
from __future__ import annotations

from html import escape

from .ta_analysis import TAAnalysisResult, fmt_price


def format_scenario_context_html(
    *,
    symbol: str,
    exchange: str,
    note: str,
    price: float,
    ta: TAAnalysisResult | None = None,
) -> str:
    lines = [
        f"📎 <b>Контекст</b> · <b>{escape(symbol.upper())}</b> · {escape(exchange)}",
        f"Цена <b>{fmt_price(price)}</b>.",
        escape(note[:480]),
    ]
    if ta is not None:
        brief = str(getattr(ta, "human_trade_brief", "") or "").strip()
        if brief:
            lines.append(f"💬 {escape(brief[:400])}")
        act = str(getattr(ta, "scenario_engine_action", "") or "").upper()
        if act in {"LONG", "SHORT", "WATCH"}:
            lines.append(
                f"План C: <b>{act}</b> — без смены стороны, пока не обновим разбор TA."
            )
    lines.append(
        "<i>Не market-ордер: ждём подтверждение в основном ENTRY или manual TA.</i>"
    )
    return "\n\n".join(lines)
