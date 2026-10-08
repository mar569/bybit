"""Единый контур: alert (WATCH/ENTRY) → график → analysis-канал."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Signal
    from .settings import ScannerSettings
    from .ta_analysis import TAAnalysisResult
    from .trade_decision_gate import TradeDecision


def reading_blocks_entry_long(ta: TAAnalysisResult) -> str:
    from .human_trade_brief import scanner_side_blocked_by_scenario

    block = scanner_side_blocked_by_scenario(ta, "long")
    if block:
        return block
    for line in getattr(ta, "reading_present", None) or []:
        low = str(line).lower()
        if "участие не подтверждает рост" in low or "хай обновлён" in low:
            return str(line)[:160]
    if getattr(ta, "reading_live_scenario", "") == "exhaustion" and (
        getattr(ta, "verdict", "") or ""
    ).upper() == "WAIT":
        seek = str(getattr(ta, "reading_seek_label", "") or "")
        if "участие" in seek.lower() or "хай" in seek.lower():
            return seek
    return ""


def reading_blocks_entry_short(ta: TAAnalysisResult) -> str:
    from .human_trade_brief import scanner_side_blocked_by_scenario

    block = scanner_side_blocked_by_scenario(ta, "short")
    if block:
        return block
    for line in getattr(ta, "reading_present", None) or []:
        if "продажи без усиления" in str(line):
            return str(line)
    for line in getattr(ta, "reading_absent", None) or []:
        if "кульминация squeeze" in str(line).lower():
            return "ранний SHORT — ждать пик объёма и liq шортов на хае"
    for line in getattr(ta, "reading_present", None) or []:
        if "добой short squeeze" in str(line).lower() or "ранний шорт опасен" in str(line).lower():
            return str(line)[:160]
    return ""


def setup_grade_ok_for_entry(ta: TAAnalysisResult, *, min_grade: str = "B") -> bool:
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    score = int(getattr(ta, "setup_score", 0) or 0)
    order = {"A": 3, "B": 2, "C": 1, "": 0, "D": 0}
    need = order.get(min_grade.upper(), 2)
    if order.get(grade, 0) >= need:
        return True
    return score >= 72 and bool(getattr(ta, "setup_ideal_ready", False))


def quality_tier_label(tier: str | None) -> str:
    from .signal_locale import quality_tier_html

    return quality_tier_html(tier)


def should_attach_signal_chart(
    ta: TAAnalysisResult | None,
    *,
    quality_tier: str | None,
    trade_decision: TradeDecision | None,
    settings: ScannerSettings,
) -> bool:
    if ta is None:
        return False
    if not getattr(settings, "signal_chart_enabled", True):
        return False
    tier = (quality_tier or "").lower()
    if tier == "watch":
        from .range_breakdown_retest import rbr_alert_eligible

        if rbr_alert_eligible(ta):
            return bool(getattr(settings, "signal_chart_enabled", True))
        return bool(getattr(settings, "signal_chart_on_watch", False))
    if tier != "entry":
        return False
    if trade_decision and (trade_decision.action or "").lower() != "entry":
        return False
    if setup_grade_ok_for_entry(ta, min_grade="B"):
        return True
    score = int(getattr(trade_decision, "setup_score", 0) or 0) if trade_decision else 0
    return score >= int(getattr(settings, "trade_decision_min_entry_score", 62))


def should_route_pro_analysis(
    ta: TAAnalysisResult | None,
    *,
    quality_tier: str | None,
    trade_decision: TradeDecision | None,
    settings: ScannerSettings,
) -> bool:
    if ta is None:
        return False
    if not getattr(settings, "analysis_enabled", True):
        return False
    tier = (quality_tier or "").lower()
    if tier != "entry":
        return False
    if trade_decision and (trade_decision.action or "").lower() != "entry":
        return False
    if getattr(settings, "signal_pro_to_analysis_chat", False):
        return True
    return setup_grade_ok_for_entry(ta, min_grade="B") and should_attach_signal_chart(
        ta,
        quality_tier=quality_tier,
        trade_decision=trade_decision,
        settings=settings,
    )


def professional_reading_snippet(
    ta: TAAnalysisResult,
    *,
    max_len: int = 380,
    reading_style: str = "situational",
) -> str:
    from .narrative_report import format_reading_block_html

    block = format_reading_block_html(ta, style=reading_style)
    if not block:
        narrative = str(getattr(ta, "reading_narrative", "") or "").strip()
        if narrative:
            return f"📖 {narrative[:max_len]}"
        return ""
    if len(block) > max_len:
        return block[: max_len - 3] + "..."
    return block


def scenario_body_html(
    ta: TAAnalysisResult | None,
    *,
    symbol: str = "",
    reading_style: str = "situational",
) -> str:
    """Разбор в alert: situational (проза) или legacy scenario report."""
    if ta is None:
        return ""
    style = (reading_style or "situational").lower()
    if style == "human":
        html = str(getattr(ta, "human_trade_brief_html", "") or "").strip()
        if html:
            return html
        from .human_trade_brief import build_human_trade_brief_html

        return build_human_trade_brief_html(ta, symbol=symbol) or ""
    if style == "hybrid":
        from .narrative_report import format_reading_block_html

        block = format_reading_block_html(ta, style="hybrid")
        if block:
            return block
    sit = str(getattr(ta, "situational_brief_html", "") or "").strip()
    if style != "evidence" and sit:
        return sit
    if style == "situational" and not sit:
        from .situational_brief import build_situational_brief_html

        sit = build_situational_brief_html(ta, symbol=symbol)
        if sit:
            return sit
    html = str(getattr(ta, "scenario_report_html", "") or "").strip()
    rich = bool(
        html and ("📖" in html or "<b>Есть</b>" in html or "режим" in html.lower() or "Вход" in html)
    )
    if not rich:
        from .scenario_report import build_scenario_report

        html = build_scenario_report(ta, symbol=symbol).to_html_compact()
    return html


def build_signal_alert_caption(
    header: str,
    ta: TAAnalysisResult | None,
    *,
    symbol: str = "",
    quality_tier: str | None = None,
    quality_html: str = "",
    action_line: str = "",
    compact: bool = True,
    reading_style: str = "situational",
) -> str:
    """Alert: шапка сканера → reading/scenario → поток → одна строка действия."""
    parts: list[str] = []
    tier = quality_tier_label(quality_tier)
    if tier and tier not in header:
        parts.append(tier)
    parts.append(header.strip())
    body = scenario_body_html(ta, symbol=symbol, reading_style=reading_style)
    if body and body not in header:
        parts.append(body)
    if quality_html.strip() and quality_html.strip() not in "\n\n".join(parts):
        parts.append(quality_html.strip())
    if action_line.strip():
        plain = action_line.strip()
        if plain not in "\n\n".join(parts):
            parts.append(plain)
    if ta is not None and not compact:
        btc = str(getattr(ta, "btc_context", "") or "").strip()
        if btc:
            parts.append(f"₿ {btc[:120]}")
    text = "\n\n".join(p for p in parts if p)
    if len(text) > 980:
        text = text[:977] + "…"
    from .signal_locale import polish_user_copy

    return polish_user_copy(text)


def merge_alert_caption(
    header: str,
    ta: TAAnalysisResult | None,
    *,
    ta_caption: str = "",
    quality_tier: str | None = None,
    compact: bool = True,
    reading_style: str = "situational",
) -> str:
    action = ""
    if ta_caption:
        scenario = scenario_body_html(ta, reading_style=reading_style)
        cap = ta_caption.strip()
        if scenario and cap in scenario:
            action = ""
        elif cap and cap not in (scenario or ""):
            action = cap
    return build_signal_alert_caption(
        header,
        ta,
        quality_tier=quality_tier,
        action_line=action,
        compact=compact,
        reading_style=reading_style,
    )


def apply_reading_to_trade_decision(
    side: str,
    ta: TAAnalysisResult,
    *,
    watch_allowed: bool,
    min_watch_score: int,
    setup_score: int,
) -> tuple[str, str] | None:
    """Returns (action, reason) override or None."""
    side = (side or "").lower()
    if side == "long":
        note = reading_blocks_entry_long(ta)
        if note and watch_allowed:
            return "watch", note[:160]
        if note:
            return "skip", note[:120]
    if side == "short":
        note = reading_blocks_entry_short(ta)
        if note and watch_allowed:
            return "watch", note[:160]
    absent = getattr(ta, "reading_absent", None) or []
    if side == "long" and any("слома структуры нет" in str(x) for x in absent):
        if (getattr(ta, "htf_bias", "") or "").lower() == "bearish" and watch_allowed:
            return "watch", "H4/H1 не поддерживают лонг — ждать структуру"
    return None
