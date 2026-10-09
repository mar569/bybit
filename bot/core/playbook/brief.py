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
) -> str:
    head = f"<b>{escape(symbol)}</b> · {escape(state.badge_ru)}"
    body_parts = [escape(situation.rstrip(".") + ".")]
    if expect:
        body_parts.append(escape(expect.rstrip(".") + "."))
    body = " ".join(body_parts)

    blocks: list[str] = [head, body]
    matrix = format_intel_matrix_html_from_rows(intel_rows, mandatory_four=True)
    if matrix:
        blocks.append(matrix)
    if footer:
        foot = footer
    else:
        from .chart_legend import chart_legend_html

        foot = chart_legend_html(rbr)
    blocks.append(foot)
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
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    phase = str(rbr.get("phase") or "")
    floor = fmt_price(float(rbr.get("range_bottom") or 0))
    ceil = fmt_price(float(rbr.get("range_top") or 0))
    badge = result.state.badge_ru.split(maxsplit=1)[-1] if result.state.value != "observe" else "WATCH"

    if phase in {"fade_top", "await_break"}:
        head = f"📌 <b>{escape(sym)}</b> · {escape(badge)} ({conf}/10) · боковик <b>{floor}–{ceil}</b>"
    elif phase == "retest":
        head = f"📌 <b>{escape(sym)}</b> · {escape(badge)} ({conf}/10) · retest пола <b>{floor}</b>"
    else:
        head = f"📌 <b>{escape(sym)}</b> · {escape(badge)} ({conf}/10)"

    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    stack_short = ""
    if stack:
        parts = [p.strip() for p in stack.replace("→", "·").split("·") if p.strip()]
        stack_short = (
            f"🧭 {escape(parts[0])} … {escape(parts[-1])}"
            if len(parts) > 3
            else f"🧭 {escape(stack[:100])}"
        )

    body = result.body_html
    if sym and body.startswith("<b>"):
        body_lines = body.split("\n\n", 1)
        body = body_lines[1] if len(body_lines) > 1 else body
    if "📊" in body:
        body = body.split("📊", 1)[0].strip()

    from .chart_legend import chart_legend_html

    note = chart_legend_html(rbr)
    parts = [head, stack_short, body, note]
    return "\n".join(p for p in parts if p)
