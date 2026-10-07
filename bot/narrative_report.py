"""Единый профессиональный текст разбора (alert / analysis / manual)."""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult


def _bullet_block(title: str, items: Sequence[str], *, limit: int = 5) -> str:
    lines = [str(item).strip() for item in items if str(item).strip()]
    if not lines:
        return ""
    body = "\n".join(f"• {escape(line)}" for line in lines[:limit])
    return f"<b>{escape(title)}</b>\n{body}"


def _present_for_signal(present: list[str], narrative: str) -> list[str]:
    """Без дублей TF из 📖-строки; фигура — только если ещё уместна."""
    stack = narrative or ""
    out: list[str] = []
    for line in present:
        s = str(line).strip()
        if not s:
            continue
        low = s.lower()
        if "фигура" in low:
            out.append(s)
            continue
        tf_heads = ("M5", "M10", "M15", "H1", "H4", "W1", "D1")
        if any(s.startswith(f"{h}:") for h in tf_heads):
            head = s.split(":", 1)[0]
            if f"{head}:" in stack or head in stack:
                continue
        if s.startswith("HH+HL") or s.startswith("LH+LL") or "диапазон / смесь" in s:
            if "→" in stack:
                continue
        out.append(s)
    return out[:6]


def format_reading_block_html(
    ta: TAAnalysisResult,
    *,
    style: str = "situational",
) -> str:
    """Разбор для сигнала: situational (проза) или evidence (списки)."""
    style = (style or "situational").lower()
    sit = str(getattr(ta, "situational_brief_html", "") or "").strip()
    if style in {"situational", "hybrid"} and sit:
        parts = [sit]
        if style == "hybrid":
            narrative = str(getattr(ta, "reading_narrative", "") or "").strip()
            raw_present = getattr(ta, "reading_present", None) or []
            present = _present_for_signal(list(raw_present), narrative)
            absent = getattr(ta, "reading_absent", None) or []
            yes = _bullet_block("Ещё факты", present, limit=3)
            if yes:
                parts.append(yes)
            if absent:
                no = _bullet_block("Нет", absent, limit=2)
                if no:
                    parts.append(no)
        return "\n".join(parts)

    narrative = str(getattr(ta, "reading_narrative", "") or "").strip()
    raw_present = getattr(ta, "reading_present", None) or []
    present = _present_for_signal(list(raw_present), narrative)
    if getattr(ta, "reading_accept_pattern", True) is False:
        present = [p for p in present if "фигура" not in p.lower()]
    absent = getattr(ta, "reading_absent", None) or []
    live = str(getattr(ta, "reading_live_scenario", "") or "").strip()
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()

    parts: list[str] = []
    if narrative:
        parts.append(f"📖 {escape(narrative[:420])}")
    if seek and seek not in narrative:
        parts.append(f"🎯 {escape(seek[:200])}")
    yes = _bullet_block("Есть", present, limit=6)
    no = _bullet_block("Нет / не подтверждено", absent, limit=5)
    if yes:
        parts.append(yes)
    if no:
        parts.append(no)
    if live and live not in {"range"}:
        parts.append(f"<i>Сценарий:</i> {escape(live)}")
    return "\n".join(parts)


def format_professional_signal_html(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    headline: str = "",
    compact: bool = False,
) -> str:
    """Полный разбор для Telegram HTML: чтение → вердикт → план → участие."""
    from .ta_analysis import (
        format_flow_direction_label,
        ta_plain_forecast_line,
        ta_signal_forecast_summary_line,
        ta_what_to_do_line,
    )

    parts: list[str] = []
    if headline:
        parts.append(headline)
    elif symbol:
        parts.append(f"<b>{escape(symbol.upper())}</b>")

    reading = format_reading_block_html(ta)
    if reading:
        parts.append(reading)

    verdict = str(getattr(ta, "verdict", "WAIT") or "WAIT")
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    reason = str(getattr(ta, "verdict_reason", "") or "").strip()
    parts.append(f"<b>{escape(verdict)}</b> {conf}/10" + (f" — {escape(reason[:160])}" if reason else ""))

    has_reading = bool(getattr(ta, "reading_narrative", "") or getattr(ta, "reading_present", None))
    plain = ta_plain_forecast_line(ta)
    if plain and not compact and not has_reading:
        parts.append(plain)
    basis = ta_signal_forecast_summary_line(ta)
    if basis and not compact and basis not in (plain or "") and not has_reading:
        parts.append(basis)
    plan = ta_what_to_do_line(ta, ready=verdict in {"LONG", "SHORT"})
    if plan and not compact:
        parts.append(plan)

    flow = format_flow_direction_label(ta)
    if flow and not compact:
        parts.append(f"🧭 {flow}")

    btc = str(getattr(ta, "btc_context", "") or "").strip()
    if btc and not compact:
        parts.append(f"₿ {escape(btc[:120])}")

    participation = getattr(ta, "market_participation_lines", None) or []
    if participation and not compact:
        body = "\n".join(escape(str(line)) for line in participation[:5])
        parts.append(f"📊 <b>Поток</b>\n{body}")

    return "\n\n".join(part for part in parts if part)


def format_alert_reading_compact(ta: TAAnalysisResult, *, symbol: str = "") -> str:
    """Короткий alert: 6–10 строк без дублирования HTML-тегов narrative."""
    return format_professional_signal_html(ta, symbol=symbol, compact=True)
