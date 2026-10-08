"""Общие русские подписи для графика и Telegram (без EN на PNG)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult


def structure_break_label_ru(kind: str) -> str:
    k = (kind or "").lower()
    if k == "mss":
        return "смена характера"
    if k == "bos":
        return "слом структуры"
    return "слом структуры"


def pick_chart_zone(ta: "TAAnalysisResult", current: float) -> dict | None:
    metrics = getattr(ta, "market_metrics", None) or {}
    raw = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
    if not raw:
        return None
    best: dict | None = None
    best_score = -1.0
    for z in raw:
        if not isinstance(z, dict):
            continue
        top, bot = float(z.get("top", 0)), float(z.get("bottom", 0))
        if top <= bot:
            continue
        mid = (top + bot) / 2
        if abs(mid - current) / current > 0.14:
            continue
        valid = bool(z.get("valid"))
        tf = str(z.get("tf", "LTF"))
        score = 20.0 - abs(mid - current) / current * 100.0
        if valid:
            score += 25.0
        if tf in {"H4", "H1", "W1"}:
            score += 6.0
        if score > best_score:
            best_score = score
            best = z
    return best


def collect_reason_and_confirmations(ta: "TAAnalysisResult") -> tuple[str, list[str]]:
    reason = ""
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    trigger = str(getattr(ta, "setup_trigger", "") or "").strip()
    scenario = str(getattr(ta, "primary_scenario", "") or "").strip()
    if seek:
        reason = seek
    elif trigger:
        reason = trigger
    elif scenario:
        reason = scenario
    else:
        reason = str(getattr(ta, "verdict_reason", "") or "").strip()[:120]

    confirms: list[str] = []
    pat = getattr(ta, "primary_chart_pattern", None)
    if pat is not None:
        confirms.append(str(getattr(pat, "label_ru", "") or "фигура на графике"))
    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        confirms.append(structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos")))
    if smc and getattr(smc, "liquidity_sweep", False):
        confirms.append("манипуляция / снятие ликвидности")
    cur = float(getattr(ta, "current_price", 0) or 0)
    zone = pick_chart_zone(ta, cur)
    if zone and bool(zone.get("valid")):
        kind = str(zone.get("kind", "demand"))
        if "demand" in kind or "bull" in kind:
            confirms.append("зона спроса")
        elif "supply" in kind or "bear" in kind:
            confirms.append("зона предложения")
    for line in list(getattr(ta, "factor_lines", None) or [])[:3]:
        s = str(line).strip()
        if s and s not in confirms:
            confirms.append(s[:48])
    return reason[:140], confirms[:4]
