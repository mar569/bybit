"""H4/H1 план → M15 зона → M5/M15 триггер (A/B/C). Один контур входа."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult

from .scenario_report import ENTRY_A, ENTRY_B, ENTRY_C
from .ta_analysis import fmt_price


@dataclass
class HtfLtfEntryPlan:
    htf_line: str = ""
    m15_zone_line: str = ""
    ltf_trigger_line: str = ""
    entry_mode: str = ENTRY_C
    entry_mode_ru: str = "наблюдение"
    invalidate: float | None = None
    poc_line: str = ""

    def to_html(self) -> str:
        parts: list[str] = []
        if self.htf_line:
            parts.append(f"🗺 <b>HTF план:</b> {escape(self.htf_line)}")
        if self.m15_zone_line:
            parts.append(f"📍 <b>M15 зона:</b> {escape(self.m15_zone_line)}")
        if self.poc_line:
            parts.append(f"📊 {escape(self.poc_line)}")
        if self.ltf_trigger_line:
            parts.append(f"⚡ <b>LTF триггер:</b> {escape(self.ltf_trigger_line)}")
        parts.append(
            f"Режим <b>{escape(self.entry_mode)}</b> — {escape(self.entry_mode_ru)}"
        )
        if self.invalidate is not None:
            parts.append(f"Отмена HTF/LTF: <b>{fmt_price(self.invalidate)}</b>")
        return "\n".join(parts)


def _htf_bias(ta: TAAnalysisResult) -> tuple[str, str]:
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    htf = (getattr(ta, "htf_bias", "") or "").lower()
    pri = (getattr(ta, "action_priority", "") or "").lower()
    verdict = (ta.verdict or "WAIT").upper()
    smc = getattr(ta, "smc", None)
    macro = str(getattr(smc, "macro_structure_label", "") or "") if smc else ""
    htf_l = str(getattr(smc, "htf_structure_label", "") or "") if smc else ""

    if verdict in {"LONG", "SHORT"}:
        side = verdict.lower()
    elif htf in {"long", "short"}:
        side = htf
    elif pri in {"long", "short"}:
        side = pri
    else:
        side = "wait"

    bits: list[str] = []
    if stack:
        bits.append(stack)
    elif macro or htf_l:
        bits.append(" · ".join(x for x in (macro, htf_l) if x))
    if side == "long":
        bits.append("работаем long только от demand / Fib 0.5–0.618")
    elif side == "short":
        bits.append("работаем short только от supply / откат к сопротивлению")
    else:
        bits.append("нет HTF-направления — только границы range")
    return side, " · ".join(bits)[:220]


def _m15_zone(ta: TAAnalysisResult, side: str) -> tuple[str, float | None, float | None]:
    lo = hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        lo, hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    elif getattr(ta, "setup_entry", None):
        e = float(ta.setup_entry)
        lo, hi = e * 0.997, e * 1.003

    smc = getattr(ta, "smc", None)
    if lo is None and smc is not None and side in {"long", "short"}:
        blocks = getattr(smc, "order_blocks", None) or []
        want = "bullish" if side == "long" else "bearish"
        for block in blocks:
            if getattr(block, "mitigated", False):
                continue
            if getattr(block, "direction", "") != want:
                continue
            lo, hi = float(block.bottom), float(block.top)
            break

    if lo is not None and hi is not None:
        label = f"{fmt_price(lo)}–{fmt_price(hi)}"
        if getattr(ta, "reading_accept_fib", False):
            label += " (Fib confluence)"
        elif getattr(ta, "reading_accept_ob", False):
            label += " (OB)"
        return label, lo, hi

    if side == "long" and ta.nearest_support:
        p = float(ta.nearest_support)
        return f"demand ≈ {fmt_price(p)}", p * 0.998, p * 1.002
    if side == "short" and ta.nearest_resistance:
        p = float(ta.nearest_resistance)
        return f"supply ≈ {fmt_price(p)}", p * 0.998, p * 1.002
    return "ждём зону M15 — OB/Fib/уровень не подтверждён", None, None


def _ltf_trigger(ta: TAAnalysisResult, side: str) -> str:
    interval = int(getattr(ta, "analysis_interval_minutes", 5) or 5)
    tag = f"{interval}m"
    trigger = getattr(ta, "setup_trigger", "") or ""
    if side == "long" and ta.breakout_level:
        return f"close {tag} ≥ {fmt_price(ta.breakout_level)}" + (
            f" · {trigger[:60]}" if trigger else ""
        )
    if side == "short" and ta.breakdown_level:
        return f"close {tag} ≤ {fmt_price(ta.breakdown_level)}" + (
            f" · {trigger[:60]}" if trigger else ""
        )
    if trigger:
        return f"{tag}: {trigger[:100]}"
    return f"нет триггера на {tag} — не market"


def _resolve_mode(
    ta: TAAnalysisResult,
    side: str,
    zone_lo: float | None,
) -> tuple[str, str]:
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    ideal = bool(getattr(ta, "setup_ideal_ready", False))
    verdict = (ta.verdict or "WAIT").upper()

    if side == "wait" or verdict == "WAIT":
        if ideal and grade in {"A", "B"} and zone_lo:
            return ENTRY_A, "лимит в M15-зоне при HTF bias"
        return ENTRY_C, "HTF/M15 без точки — ждать триггер LTF"

    if ideal and grade in {"A", "B"} and zone_lo:
        return ENTRY_A, "лимит в M15-зоне (HTF + confluence)"
    if verdict in {"LONG", "SHORT"} or getattr(ta, "setup_trigger", ""):
        return ENTRY_B, "вход после close LTF за уровнем"
    if zone_lo:
        return ENTRY_A, "лимит в зоне, триггер LTF опционален"
    return ENTRY_C, "наблюдение — дождаться M15 зоны"


def build_htf_ltf_entry_plan(ta: TAAnalysisResult) -> HtfLtfEntryPlan:
    side, htf_line = _htf_bias(ta)
    m15_label, zlo, zhi = _m15_zone(ta, side)
    ltf_line = _ltf_trigger(ta, side)
    mode, mode_ru = _resolve_mode(ta, side, zlo)
    inv = ta.invalidation_price or getattr(ta, "setup_stop", None)
    poc = str(getattr(ta, "volume_poc_label", "") or "").strip()

    return HtfLtfEntryPlan(
        htf_line=htf_line,
        m15_zone_line=m15_label,
        ltf_trigger_line=ltf_line,
        entry_mode=mode,
        entry_mode_ru=mode_ru,
        invalidate=float(inv) if inv else None,
        poc_line=poc,
    )


def attach_htf_ltf_to_scenario_html(scenario_html: str, plan: HtfLtfEntryPlan) -> str:
    block = plan.to_html()
    if not block:
        return scenario_html
    if block in scenario_html:
        return scenario_html
    return f"{scenario_html}\n\n{block}" if scenario_html else block
