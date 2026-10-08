"""Опциональный контекст (сессия, макро) — одна строка, не ядро разбора."""
from __future__ import annotations

from html import escape

from .ta_analysis import TAAnalysisResult


def domain_context_line(
    symbol: str,
    ta: TAAnalysisResult,
) -> str:
    sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
    lines: list[str] = []

    btc = (getattr(ta, "btc_context", "") or "").strip()
    if btc and len(btc) < 120:
        lines.append(btc)

    if sym in {"BZUSDT", "UKOUSD.S", "UKOUSD"}:
        try:
            from .oil_session import oil_session_status

            st = oil_session_status()
            if st and not st.is_open:
                hint = (st.market_open_hint_ru or st.next_open_label_ru or "").strip()
                if hint:
                    lines.append(f"TradFi: {hint}")
        except Exception:
            pass

    if not lines:
        return ""
    return escape(" · ".join(lines[:2]))
