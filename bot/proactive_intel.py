"""Проактивный разбор: «вижу OI/ликвидность → жду сценарий», без market-ENTRY."""
from __future__ import annotations

from html import escape

from .models import Signal
from .ta_analysis import TAAnalysisResult, fmt_price


def build_proactive_intel_html(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    quality_reason: str = "",
) -> str:
    sym = (signal.symbol or "").upper()
    ex = (signal.exchange or "Binance").strip()
    oi = float(signal.oi_change_percent or 0)
    px = float(signal.price_change_percent or 0)
    side = (signal.side or "long").lower()

    brief = str(getattr(ta, "human_trade_brief", "") or "").strip()
    if not brief:
        brief = str(getattr(ta, "reading_narrative", "") or "")[:320]

    lines: list[str] = [
        f"🔭 <b>Наблюдение</b> · <b>{escape(sym)}</b> · {escape(ex)}",
    ]

    flow_bits: list[str] = []
    if abs(oi) >= 0.25:
        flow_bits.append(f"OI {oi:+.1f}%")
    if abs(px) >= 0.2:
        flow_bits.append(f"цена {px:+.1f}%")
    if flow_bits:
        lines.append("📊 " + escape(" · ".join(flow_bits)))

    sc_title = ""
    metrics = getattr(ta, "market_metrics", None) or {}
    if isinstance(metrics, dict):
        se = metrics.get("scenario_engine")
        if isinstance(se, dict):
            sc_title = str(se.get("title") or "")
    if not sc_title:
        sc_title = str(getattr(ta, "scenario_engine_id", "") or "").replace("_", " ")

    if sc_title:
        sq = str(getattr(ta, "scenario_engine_quality", "") or "")
        lines.append(f"🧩 Сценарий: <i>{escape(sc_title)}</i>" + (f" ({escape(sq)})" if sq else ""))

    if brief:
        lines.append(f"💬 {escape(brief[:520])}")

    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    inv = getattr(ta, "invalidation_price", None)
    trigger = str(getattr(ta, "setup_trigger", "") or "").strip()

    plan: list[str] = []
    if side == "long":
        plan.append("Сканер поймал рост — <b>не догоняем</b>, ждём откат и реакцию.")
    else:
        plan.append("Сканер поймал слив/откат — <b>не шортим в нож</b>, ждём структуру.")
    if seek:
        plan.append(f"Ждём: {escape(seek[:140])}.")
    elif trigger:
        plan.append(f"Триггер: {escape(trigger[:120])}.")
    if inv:
        plan.append(f"Идея отменяется за {fmt_price(float(inv))}.")
    plan.append(
        "Когда сформируется вход A/B с подтверждением — отдельный <b>ENTRY</b> в alert-канал."
    )
    lines.append("\n".join(plan))

    if quality_reason:
        lines.append(f"<i>{escape(quality_reason[:160])}</i>")

    return "\n\n".join(lines)
