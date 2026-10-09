"""Единый профессиональный отчёт: reading → сценарий → A/B/C → уровни → BTC."""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Signal
    from .ta_analysis import TAAnalysisResult

from .ta_analysis import fmt_price

PUMP_EVENT_TYPES = frozenset({
    "mega_pump", "mega_dump", "vertical_pump", "vertical_dump",
    "impulse_pump", "impulse_dump", "pulse_pump", "pulse_dump",
    "price_pump", "price_dump", "trend_pump", "trend_dump",
    "reversal_pump", "reversal_dump", "liq_cascade_pump", "liq_cascade_dump",
    "short_squeeze", "trend_seed",
})

ENTRY_A = "A"  # лимит в зоне / OB / Fib
ENTRY_B = "B"  # после реакции свечи / пробой close
ENTRY_C = "C"  # только наблюдение


def _dedupe_present(items: list[str] | Any) -> list[str]:
    from .human_trade_brief import _dedupe_reading_lines

    return _dedupe_reading_lines(list(items or []), max_items=6)


@dataclass
class ScenarioReport:
    symbol: str = ""
    verdict: str = "WAIT"
    confidence: int = 0
    entry_mode: str = ENTRY_C
    entry_mode_ru: str = "наблюдение"
    live_scenario: str = ""
    narrative: str = ""
    seek_label: str = ""
    present: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    entry_lo: float | None = None
    entry_hi: float | None = None
    stop: float | None = None
    targets: list[float] = field(default_factory=list)
    invalidate: float | None = None
    btc_line: str = ""
    participation: list[str] = field(default_factory=list)
    plan_line: str = ""
    grade: str = ""
    setup_score: int = 0
    quality_tier: str = ""
    tf_stack: str = ""
    human_brief: str = ""

    def to_html_compact(self) -> str:
        parts: list[str] = []
        brief = str(getattr(self, "human_brief", "") or "").strip()
        if brief:
            parts.append(f"💬 <b>Разбор</b>\n{escape(brief[:900])}")
        else:
            tf_stack = str(getattr(self, "tf_stack", "") or "").strip()
            if tf_stack:
                parts.append(f"🧭 {escape(tf_stack[:200])}")
            if self.narrative:
                parts.append(f"📖 {escape(self.narrative[:400])}")
            if self.seek_label and self.seek_label not in self.narrative:
                parts.append(f"🎯 {escape(self.seek_label[:180])}")

        from .signal_locale import verdict_ru

        v = verdict_ru(self.verdict) or self.verdict
        mode = f"<b>{escape(v)}</b> {self.confidence}/10"
        if self.entry_mode_ru:
            mode += f" · {escape(self.entry_mode_ru)}"
        parts.append(mode)

        yes = self._list_block("Есть", self.present[:4])
        no = self._list_block("Нет", self.absent[:4])
        if yes:
            parts.append(yes)
        if no:
            parts.append(no)
        if self.plan_line and not brief:
            parts.append(escape(self.plan_line))
        if self.btc_line:
            parts.append(f"₿ {escape(self.btc_line[:120])}")
        if self.participation:
            flow = "\n".join(escape(x) for x in self.participation[:4])
            parts.append(f"📊 <b>Поток</b>\n{flow}")
        levels = self._levels_block()
        if levels:
            parts.append(levels)
        from .signal_locale import polish_user_copy

        return polish_user_copy("\n\n".join(parts))

    def to_html_full(self) -> str:
        base = self.to_html_compact()
        extra: list[str] = []
        if self.live_scenario:
            extra.append(f"<i>Сценарий:</i> {escape(self.live_scenario)}")
        if self.quality_tier:
            from .signal_locale import quality_tier_html

            tier = quality_tier_html(self.quality_tier) or escape(self.quality_tier)
            extra.append(f"Качество: {tier}")
        if extra:
            return base + "\n\n" + "\n".join(extra)
        return base

    def _list_block(self, title: str, items: list[str]) -> str:
        lines = [str(x).strip() for x in items if str(x).strip()]
        if not lines:
            return ""
        body = "\n".join(f"• {escape(line)}" for line in lines[:6])
        return f"<b>{escape(title)}</b>\n{body}"

    def _levels_block(self) -> str:
        bits: list[str] = []
        if self.entry_lo is not None and self.entry_hi is not None:
            bits.append(
                f"Вход: <b>{fmt_price(self.entry_lo)}–{fmt_price(self.entry_hi)}</b>"
            )
        elif self.entry_lo is not None:
            bits.append(f"Вход: <b>{fmt_price(self.entry_lo)}</b>")
        if self.stop is not None:
            bits.append(f"Стоп: <b>{fmt_price(self.stop)}</b>")
        if self.targets:
            tps = " → ".join(fmt_price(t) for t in self.targets[:3])
            bits.append(f"Цели: {tps}")
        if self.invalidate is not None:
            bits.append(f"Отмена: <b>{fmt_price(self.invalidate)}</b>")
        if not bits:
            return ""
        return "📐 " + " · ".join(bits)


def _resolve_entry_mode(ta: TAAnalysisResult, *, signal_side: str | None, ready: bool) -> tuple[str, str]:
    side = (signal_side or ta.action_priority or "").lower()
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    ideal = bool(getattr(ta, "setup_ideal_ready", False))
    verdict = (ta.verdict or "WAIT").upper()

    if verdict == "WAIT" and not ready:
        return ENTRY_C, "ждём структуру и уровень — не лезть по рынку"
    if ideal and grade in {"A", "B"} and getattr(ta, "setup_entry", None):
        return ENTRY_A, "лимит в зоне — Фибо или блок ордеров"
    if ready or verdict in {"LONG", "SHORT"}:
        return ENTRY_B, "вход после реакции свечи или закрепа за уровнем"
    if getattr(ta, "setup_trigger", ""):
        return ENTRY_B, "ждать подтверждение свечой"
    return ENTRY_C, "только наблюдение — точки входа пока нет"


def build_scenario_report(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    signal: Signal | None = None,
    signal_side: str | None = None,
    readiness: tuple[bool, str] | None = None,
    quality_tier: str | None = None,
) -> ScenarioReport:
    ready = bool(readiness and readiness[0])
    side = signal_side or (signal.side if signal else None) or ta.action_priority
    entry_mode, entry_ru = _resolve_entry_mode(ta, signal_side=side, ready=ready)

    entry_lo = entry_hi = None
    if ta.entry_zone and len(ta.entry_zone) == 2:
        entry_lo, entry_hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
    elif getattr(ta, "setup_entry", None):
        e = float(ta.setup_entry)
        entry_lo, entry_hi = e * 0.998, e * 1.002

    stop = ta.invalidation_price or getattr(ta, "setup_stop", None)
    from .chart_position_boxes import chart_plan_targets
    from .trade_plan_quality import validate_trade_plan

    targets = list(ta.target_prices[:3] or getattr(ta, "setup_tps", []) or [])
    planned = chart_plan_targets(ta, entry_lo=entry_lo, entry_hi=entry_hi)
    if planned:
        targets = planned[:3]
    plan_q = validate_trade_plan(ta)
    plan_warn = "" if plan_q.ok else plan_q.reason_ru

    btc = (ta.btc_context or "").strip()
    if not btc and ta.btc_alt_spread is not None:
        btc = f"альт vs BTC {ta.btc_alt_spread:+.2f}%"

    plan = (ta.narrative_plan or "").strip()
    eng = str(getattr(ta, "scenario_engine_id", "") or "").strip()
    eq = str(getattr(ta, "entry_quality", "") or "").strip()
    metrics = getattr(ta, "market_metrics", None) or {}
    eng_title = ""
    if isinstance(metrics, dict):
        se = metrics.get("scenario_engine")
        if isinstance(se, dict):
            eng_title = str(se.get("title") or "").strip()
    if eng_title:
        plan = eng_title
        sq = str(getattr(ta, "scenario_engine_quality", "") or "").strip()
        if sq:
            plan += f" · оценка {sq}"
        if eq and eq != "none":
            plan += f" · вход {eq}"
    elif eng:
        plan = eng.replace("_", " ")
    if not plan and readiness and readiness[1]:
        plan = readiness[1][:200]

    human_brief = str(getattr(ta, "human_trade_brief", "") or "").strip()
    absent = _dedupe_present(getattr(ta, "reading_absent", None) or [])
    if plan_warn and plan_warn not in absent:
        absent = [plan_warn, *absent][:6]

    import re

    narrative = getattr(ta, "reading_narrative", "") or ta.narrative_plain or ""
    if narrative.startswith("📐"):
        narrative = narrative[1:].strip()
    narrative = re.sub(r"<[^>]+>", "", narrative).strip()
    low = narrative.lower()
    if "простыми словами:" in low:
        idx = low.find("простыми словами:")
        narrative = narrative[idx + len("простыми словами:") :].strip()
    if signal and (
        (signal.signal_type or "").lower() in PUMP_EVENT_TYPES
        or signal.details.get("scanner_event_only")
    ):
        if entry_mode == ENTRY_A and (ta.setup_grade or "").upper() not in {"A", "B"}:
            entry_mode, entry_ru = ENTRY_C, "событие сканера — сначала разбор, не market"
        pump_note = "Сканер: импульс — торговый смысл только после reading."
        if pump_note not in narrative:
            narrative = f"{pump_note} {narrative}".strip()

    return ScenarioReport(
        symbol=symbol or "",
        verdict=ta.verdict or "WAIT",
        confidence=int(ta.verdict_confidence or 0),
        entry_mode=entry_mode,
        entry_mode_ru=entry_ru,
        live_scenario=getattr(ta, "reading_live_scenario", "") or "",
        tf_stack=str(getattr(ta, "reading_tf_stack", "") or ""),
        narrative=narrative,
        seek_label=getattr(ta, "reading_seek_label", "") or "",
        present=_dedupe_present(getattr(ta, "reading_present", None) or []),
        absent=absent,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=float(stop) if stop else None,
        targets=[float(t) for t in targets if t],
        invalidate=float(ta.invalidation_price) if ta.invalidation_price else None,
        btc_line=btc,
        participation=list(ta.market_participation_lines or [])[:5],
        plan_line=plan,
        grade=(ta.setup_grade or "").upper(),
        setup_score=int(ta.setup_score or 0),
        quality_tier=(quality_tier or "").lower(),
        human_brief=human_brief,
    )


def attach_scenario_report(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    signal: Signal | None = None,
    signal_side: str | None = None,
    readiness: tuple[bool, str] | None = None,
    quality_tier: str | None = None,
) -> ScenarioReport:
    return build_scenario_report(
        ta,
        symbol=symbol,
        signal=signal,
        signal_side=signal_side,
        readiness=readiness,
        quality_tier=quality_tier,
    )


def format_ta_caption_from_report(report: ScenarioReport, *, full: bool = False) -> str:
    return report.to_html_full() if full else report.to_html_compact()


def enrich_ta_scenario_fields(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    signal: Signal | None = None,
    signal_side: str | None = None,
    readiness: tuple[bool, str] | None = None,
    quality_tier: str | None = None,
    full_html: bool = False,
) -> TAAnalysisResult:
    """Пересобрать scenario_report после quality/readiness (сигнал / manual)."""
    from dataclasses import replace

    from .chart_display_policy import ed_playbook_v3_enabled, ed_signal_legacy_reading_enabled

    if ed_playbook_v3_enabled() and not ed_signal_legacy_reading_enabled():
        from .living_analysis import build_living_analysis_html

        html = build_living_analysis_html(ta, symbol=symbol) or ""
        return replace(
            ta,
            scenario_report_html=html,
            human_trade_brief_html=html,
            human_trade_brief=str(getattr(ta, "human_trade_brief", "") or "")[:500],
        )

    report = build_scenario_report(
        ta,
        symbol=symbol,
        signal=signal,
        signal_side=signal_side,
        readiness=readiness,
        quality_tier=quality_tier,
    )
    from .human_trade_brief import build_human_trade_brief, build_human_trade_brief_html

    brief_ta = replace(ta, human_trade_brief=report.human_brief or getattr(ta, "human_trade_brief", ""))
    human_plain = build_human_trade_brief(brief_ta, symbol=symbol)
    human_html = build_human_trade_brief_html(brief_ta, symbol=symbol)
    if human_plain:
        report = build_scenario_report(
            replace(ta, human_trade_brief=human_plain),
            symbol=symbol,
            signal=signal,
            signal_side=signal_side,
            readiness=readiness,
            quality_tier=quality_tier,
        )
    html = report.to_html_full() if full_html else report.to_html_compact()
    return replace(
        ta,
        scenario_entry_mode=report.entry_mode,
        scenario_report_html=html,
        human_trade_brief=human_plain,
        human_trade_brief_html=human_html,
    )


def cap_quality_for_scanner_event(
    signal_type: str,
    tier: str,
    ta: TAAnalysisResult | None,
) -> str:
    """Pump/dump = WATCH максимум, пока нет setup B+."""
    st = (signal_type or "").lower()
    if st not in PUMP_EVENT_TYPES:
        return tier
    if tier == "skip":
        return tier
    if ta is None:
        return "watch"
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    score = int(getattr(ta, "setup_score", 0) or 0)
    if grade in {"A", "B"} and score >= 7:
        return tier
    return "watch"
