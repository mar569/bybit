"""Footer под INTEL — по умолчанию пустой (график говорит сам)."""
from __future__ import annotations

from typing import Any


def chart_legend_html(rbr: dict[str, Any] | None, *, phase: str = "") -> str:
    from ...chart_display_policy import ed_playbook_minimal_copy_enabled

    if ed_playbook_minimal_copy_enabled():
        return ""
    if not rbr:
        return ""
    ph = (phase or str(rbr.get("phase") or "")).strip()
    if ph == "await_break":
        return "Триггер: close ниже нижней границы range."
    if ph == "retest":
        return "Триггер: отказ в зоне retest."
    return ""
