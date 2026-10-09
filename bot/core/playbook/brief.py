"""Telegram HTML from playbook fields."""
from __future__ import annotations

from html import escape

from .states import PlaybookState


def format_intel_matrix_html_from_rows(
    rows: list[tuple[str, str]],
    *,
    mandatory_four: bool = True,
) -> str:
    labels_default = ("Цена", "OI", "CVD", "Liq")
    if mandatory_four:
        by_label = {label: text for label, text in rows}
        rows = [(label, by_label.get(label) or "—") for label in labels_default]
    if not rows:
        return ""
    return "📊 <b>INTEL</b>\n" + "\n".join(
        f"▫️ <b>{escape(label)}</b> — {escape(text)}" for label, text in rows[:4]
    )


def format_intel_matrix_html(result: object, *, mandatory_four: bool = True) -> str:
    from .engine import PlaybookResult

    if not isinstance(result, PlaybookResult):
        return ""
    return format_intel_matrix_html_from_rows(list(result.intel_rows), mandatory_four=mandatory_four)


def format_playbook_brief_html(
    *,
    state: PlaybookState,
    symbol: str,
    situation: str,
    expect: str,
    intel_rows: list[tuple[str, str]],
    footer: str = "",
    rbr: dict | None = None,
    snap: object | None = None,
    ta: object | None = None,
) -> str:
    from ...chart_display_policy import ed_playbook_intel_panel_enabled
    from ...ta_analysis import TAAnalysisResult
    from ..snapshot import MarketSnapshot
    from .prose import compose_playbook_prose_html

    from ...channel_discipline import ed_entries_only_enabled
    from ...human_trade_brief import manual_entry_ready

    if (
        ed_entries_only_enabled()
        and isinstance(ta, TAAnalysisResult)
        and not manual_entry_ready(ta, symbol=symbol)
    ):
        head = f"<b>{escape(symbol)}</b> · вход не готов"
    else:
        head = f"<b>{escape(symbol)}</b> · {escape(state.badge_ru)}"
    blocks: list[str] = [head]

    if isinstance(snap, MarketSnapshot):
        blocks.append(
            compose_playbook_prose_html(snap, situation=situation, expect=expect, ta=ta if isinstance(ta, TAAnalysisResult) else None)
        )
    else:
        body_parts = [escape(situation.rstrip(".") + ".")]
        if expect:
            body_parts.append(escape(expect.rstrip(".") + "."))
        blocks.append(" ".join(body_parts))

    if ed_playbook_intel_panel_enabled():
        matrix = format_intel_matrix_html_from_rows(intel_rows, mandatory_four=True)
        if matrix:
            blocks.append(matrix)

    if footer:
        foot = footer
    else:
        from .chart_legend import chart_legend_html

        foot = chart_legend_html(rbr)
    if foot:
        blocks.append(foot)

    blocks.append("<i>Уровни и сетап — на графике.</i>")
    return "\n\n".join(blocks)


def format_rbr_watch_caption_html(
    ta: object,
    *,
    symbol: str = "",
) -> str:
    """WATCH RBR: шапка алерта + playbook brief (без дублирования legacy plan wall)."""
    from html import escape

    from ...range_breakdown_retest import get_rbr_from_ta
    from ...ta_analysis import TAAnalysisResult, fmt_price
    from .cache import get_or_run_playbook

    if not isinstance(ta, TAAnalysisResult):
        return ""
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return ""
    result = get_or_run_playbook(ta, symbol=symbol)
    sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
    phase = str(rbr.get("phase") or "")
    badge = result.state.badge_ru
    head = f"📌 <b>{escape(sym)}</b> · {escape(badge)}"

    body = result.body_html
    if sym and body.startswith("<b>"):
        body_lines = body.split("\n\n", 1)
        body = body_lines[1] if len(body_lines) > 1 else body
    if "📊" in body:
        body = body.split("📊", 1)[0].strip()

    parts = [head, body]
    return "\n".join(p for p in parts if p)
