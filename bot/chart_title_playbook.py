"""Заголовок PNG — только playbook (без LONG 8/10)."""
from __future__ import annotations

import re

from .ta_analysis import TAAnalysisResult

# Matplotlib в Docker (DejaVu) не рисует emoji — иначе UserWarning Glyph missing
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)


def _chart_safe_badge(text: str) -> str:
    plain = _EMOJI_RE.sub("", text or "").strip()
    return " ".join(plain.split())


def pro_chart_title(
    symbol: str,
    ta: TAAnalysisResult,
    *,
    interval_minutes: int,
    hours_label: str = "",
) -> str | None:
    from .chart_display_policy import ed_playbook_v3_enabled

    if not ed_playbook_v3_enabled():
        return None
    try:
        from .core.playbook.cache import get_or_run_playbook

        sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
        pb = get_or_run_playbook(ta, symbol=sym)
    except Exception:
        return None
    tf = f"{interval_minutes}m"
    h = hours_label.strip() or ""
    meta = " · ".join(p for p in (tf, h) if p)
    from .channel_discipline import ed_entries_only_enabled
    from .human_trade_brief import manual_entry_ready

    if ed_entries_only_enabled():
        badge = "вход · план на PNG" if manual_entry_ready(ta, symbol=sym) else "вход не готов"
    else:
        badge = _chart_safe_badge(pb.state.badge_ru)
    if meta:
        return f"{sym or symbol} · {meta} · {badge}"
    return f"{sym or symbol} · {badge}"
