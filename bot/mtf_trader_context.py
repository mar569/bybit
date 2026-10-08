"""Макро-контекст трейдера: памп, объём, ликвидность, где искать вход (RU)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult

from .chart_setup_interval import pick_setup_chart_interval
from .human_trade_brief import preferred_trade_side
from .ta_analysis import fmt_price


def build_trader_macro_lines(ta: "TAAnalysisResult", *, chart_interval: int = 5) -> list[str]:
    lines: list[str] = []
    setup_iv = pick_setup_chart_interval(ta, chart_interval)

    if bool(getattr(ta, "post_pump", False)):
        lines.append("монета недавно в импульсе — после пампа часто консолидация и ложные движения на 5m")

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(getattr(cons, "top", 0)), float(getattr(cons, "bottom", 0))
        if top > bot > 0:
            lines.append(
                f"боковик ≈ {fmt_price(bot)}–{fmt_price(top)} — сетап смотрим на {setup_iv}m, не на шум 5m"
            )

    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "liquidity_sweep", False):
        lines.append("снимали ликвидность — возможен разворот или ускорение после retest")

    flow = [str(x).lower() for x in (getattr(ta, "market_participation_lines", None) or [])]
    flow_join = " ".join(flow)
    if any(k in flow_join for k in ("oi без", "без явного", "баланс", "слаб")):
        lines.append("участия/OI мало — движение без «толпы», осторожно с погоней")
    if any("cvd" in x and ("↓" in x or "sell" in x or "шорт" in x) for x in flow):
        side = preferred_trade_side(ta)
        if side == "short":
            lines.append("поток taker согласен со шортом")

    brdn = getattr(ta, "breakdown_level", None)
    side = preferred_trade_side(ta)
    if side == "short" and brdn and float(brdn) > 0:
        lines.append(
            f"шорт по плану после закрепа ниже {fmt_price(float(brdn))} (retest пола боковика — как на 30m)"
        )
    elif side == "long" and getattr(ta, "breakout_level", None):
        bo = float(ta.breakout_level)
        lines.append(f"лонг — только после закрепа выше {fmt_price(bo)}")

    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    if stack:
        lines.insert(0, f"картина сверху: {stack}")

    return lines[:6]


def build_trader_macro_paragraph(ta: "TAAnalysisResult", *, chart_interval: int = 5) -> str:
    parts = build_trader_macro_lines(ta, chart_interval=chart_interval)
    return " ".join(parts)
