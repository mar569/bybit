"""Детерминированный «AI-style» блок на каждый alert — без LLM, из reading + gates."""
from __future__ import annotations

from html import escape

from .ta_analysis import TAAnalysisResult


def format_signal_ai_reading_snippet(ta: TAAnalysisResult, *, symbol: str = "") -> str:
    """Кратко: человеческий разбор → HTF/LTF — follow-up после сигнала."""
    lines: list[str] = []
    head = f"🤖 <b>Reading</b>"
    if symbol:
        head += f" · {escape(symbol.upper())}"
    lines.append(head)

    brief_html = str(getattr(ta, "human_trade_brief_html", "") or "").strip()
    if brief_html:
        lines.append(brief_html)
    else:
        brief = str(getattr(ta, "human_trade_brief", "") or "").strip()
        if brief:
            lines.append(f"💬 {escape(brief[:520])}")
        else:
            stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
            if stack:
                lines.append(f"🧭 {escape(stack[:180])}")
            narrative = str(getattr(ta, "reading_narrative", "") or "").strip()
            if narrative:
                lines.append(f"📖 {escape(narrative[:280])}")

    plan = str(getattr(ta, "htf_ltf_plan_html", "") or "").strip()
    if plan:
        lines.append(plan[:420])

    absent = [str(x) for x in (getattr(ta, "reading_absent", None) or []) if str(x).strip()]
    if absent and not brief_html and not getattr(ta, "human_trade_brief", ""):
        lines.append(
            "<b>Не подтверждено:</b> "
            + escape("; ".join(absent[:3]))
        )

    fib = str(getattr(ta, "fib_status", "") or "")
    if fib and fib not in {"ready", ""}:
        lines.append(f"Fib: <i>{escape(fib)}</i> — {escape((getattr(ta, 'fib_reject_reason', '') or '')[:80])}")

    note = str(getattr(ta, "verdict_scenario_note", "") or "").strip()
    if note and note not in (getattr(ta, "human_trade_brief", "") or ""):
        lines.append(f"⚠️ {escape(note[:240])}")

    return "\n".join(lines)
