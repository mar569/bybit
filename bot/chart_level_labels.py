"""Подписи уровней на PNG — цена + короткий код, без «пол/потолок»."""
from __future__ import annotations

from .ta_analysis import fmt_price


def level_tag(kind: str, price: float) -> str:
    p = fmt_price(price)
    if kind == "range_top":
        return f"R↑ {p}"
    if kind == "range_bottom":
        return f"R↓ {p}"
    if kind == "break_up":
        return f"↑ {p}"
    if kind == "break_down":
        return f"↓ {p}"
    if kind == "structure":
        return p
    return p
