"""Market state (контур B): один снимок перед сценарием."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .asset_passport import AssetPassport, build_asset_passport
from .exhaustion_detector import ExhaustionSignal, detect_exhaustion_at_extreme
from .fib_entry_rules import FibEntryPlan, evaluate_fib_entry
from .market_phase import AccumulationDistribution, classify_accumulation_distribution
from .pullback_compression import PullbackCompression, measure_pullback_compression
from .retest_model import EntryQuality, RetestSignal, best_entry_quality, detect_retests
from .methodology_scores import MethodologyScore
from .scenario_engine import ScenarioPick, pick_trading_scenario
from .smc_pdf_checklist import SmcPdfChecklist, evaluate_smc_pdf_checklist
from .touch_compression import TouchCompression
from .zone_model import TradingZone, build_trading_zones


@dataclass
class MarketState:
    passport: AssetPassport
    zones: list[TradingZone] = field(default_factory=list)
    retests: list[RetestSignal] = field(default_factory=list)
    entry_quality: EntryQuality = "none"
    entry_quality_note: str = ""
    compression: PullbackCompression | None = None
    exhaustion: ExhaustionSignal = field(
        default_factory=lambda: ExhaustionSignal(False, "none", "", False)
    )
    accumulation: AccumulationDistribution = field(
        default_factory=lambda: AccumulationDistribution("none", "", 0)
    )
    fib: FibEntryPlan | None = None
    smc_checklist: SmcPdfChecklist = field(
        default_factory=lambda: SmcPdfChecklist(
            False, False, "none", False, False, 0, False, ""
        )
    )
    scenario: ScenarioPick | None = None
    htf_structure: str = ""
    range_position: float = 0.5
    summary_ru: str = ""
    methodology_grade: str = ""
    methodology: MethodologyScore | None = None
    touch_compression_label: str = ""
    touch_compression: TouchCompression | None = None

    def zones_for_chart(self, current: float | None = None) -> list[dict[str, Any]]:
        from .zone_model import select_zones_for_chart

        cur = float(current or 0.0)
        if cur <= 0 and self.zones:
            cur = float(self.zones[0].mid)
        picked = select_zones_for_chart(self.zones, cur, max_zones=4)
        out: list[dict[str, Any]] = []
        for z in picked:
            out.append(
                {
                    "kind": z.kind,
                    "top": z.top,
                    "bottom": z.bottom,
                    "tf": z.tf_label,
                    "freshness": z.freshness,
                    "valid": z.valid,
                    "label": z.label_ru,
                    "start_idx": z.start_idx,
                }
            )
        return out


def build_market_state(
    *,
    symbol: str,
    bars: Sequence,
    swings: Sequence,
    smc: object | None = None,
    wave: object | None = None,
    weekly_bars: Sequence | None = None,
    macro_bars: Sequence | None = None,
    htf_bars: Sequence | None = None,
    weekly_swings: Sequence | None = None,
    macro_swings: Sequence | None = None,
    htf_swings: Sequence | None = None,
    oi_bars: Sequence | None = None,
    reading_divergence: object | None = None,
    channel: object | None = None,
    breakout_level: float | None = None,
    breakdown_level: float | None = None,
    flow_continuation: int = 50,
    flow_correction: int = 50,
    accept_pattern: bool = True,
    structure_label: str = "",
    post_pump: bool = False,
    post_dump: bool = False,
    candle_compression: bool = False,
    range_position: float | None = None,
    btc_bars: Sequence | None = None,
    verdict: str = "WAIT",
    reading_accept_fib: bool = True,
) -> MarketState:
    current = float(bars[-1].close) if bars else 0.0
    passport = build_asset_passport(
        symbol,
        bars,
        btc_bars=btc_bars,
        post_pump=post_pump,
        post_dump=post_dump,
        range_position=range_position,
    )
    zones = build_trading_zones(
        bars,
        swings,
        smc=smc,
        weekly_bars=weekly_bars,
        macro_bars=macro_bars,
        htf_bars=htf_bars,
        weekly_swings=weekly_swings,
        macro_swings=macro_swings,
        htf_swings=htf_swings,
        current=current,
    )
    retests = detect_retests(
        bars,
        zones,
        smc=smc,
        channel=channel,
        breakout_level=breakout_level,
        breakdown_level=breakdown_level,
    )
    htf_structure = ""
    if smc is not None:
        htf_structure = getattr(smc, "htf_structure", "") or ""
    htf_chg = 0.0
    if htf_bars and len(htf_bars) >= 12:
        htf_chg = (float(htf_bars[-1].close) - float(htf_bars[-12].close)) / float(
            htf_bars[-12].close
        ) * 100.0

    fib = evaluate_fib_entry(
        current=current,
        fib_levels=getattr(wave, "fib_levels", []) if wave else [],
        wave_phase=getattr(wave, "wave_phase", "") if wave else "",
        htf_structure=htf_structure,
        wave_confidence=int(getattr(wave, "confidence", 0) or 0) if wave else 0,
        htf_change_pct=htf_chg,
        zones=zones,
        accept_fib=reading_accept_fib,
    )
    smc_cl = evaluate_smc_pdf_checklist(
        smc=smc,
        fib=fib,
        zones=zones,
        current=current,
        htf_structure=htf_structure,
    )
    aligned = htf_structure in {"bullish", "bearish"} and (
        (htf_structure == "bullish" and verdict in {"LONG", "WAIT"})
        or (htf_structure == "bearish" and verdict in {"SHORT", "WAIT"})
    )
    eq, eq_note = best_entry_quality(
        retests,
        htf_aligned=aligned,
        smc_checklist_ready=smc_cl.ready,
    )
    compression = measure_pullback_compression(bars)
    exhaustion = detect_exhaustion_at_extreme(
        bars,
        swings=swings,
        oi_bars=oi_bars,
        divergence=reading_divergence,
    )
    accum = classify_accumulation_distribution(
        bars,
        structure_label=structure_label,
        post_pump=post_pump,
        compression=candle_compression,
    )
    rp = float(range_position if range_position is not None else 0.5)

    from .methodology_scores import score_methodology
    from .touch_compression import detect_touch_compression

    touch = detect_touch_compression(
        bars,
        level=breakout_level or breakdown_level,
    )
    if touch is None and breakdown_level and breakout_level:
        touch = detect_touch_compression(bars, level=breakdown_level)
    if touch is None and breakout_level:
        touch = detect_touch_compression(bars, level=breakout_level)

    state = MarketState(
        passport=passport,
        zones=zones,
        retests=retests,
        entry_quality=eq,
        entry_quality_note=eq_note,
        compression=compression,
        exhaustion=exhaustion,
        accumulation=accum,
        fib=fib,
        smc_checklist=smc_cl,
        htf_structure=htf_structure,
        range_position=rp,
        touch_compression_label=touch.label_ru if touch and touch.active else "",
        touch_compression=touch if touch and touch.active else None,
    )
    state.scenario = pick_trading_scenario(state, verdict=verdict)

    meth = score_methodology(
        state,
        flow_continuation=flow_continuation,
        flow_correction=flow_correction,
        accept_pattern=accept_pattern,
        post_pump=post_pump,
    )
    state.methodology = meth
    state.methodology_grade = meth.grade
    if state.scenario and meth.grade == "F" and state.scenario.quality != "F":
        state.scenario = ScenarioPick(
            state.scenario.scenario_id,
            state.scenario.title_ru,
            "WATCH",
            "F",
            meth.label_ru,
        )

    bits = passport.to_lines()
    if eq != "none":
        bits.append(f"Вход: {eq} — {eq_note[:100]}")
    if smc_cl.label_ru:
        bits.append(f"SMC: {smc_cl.label_ru}")
    bits.append(meth.label_ru)
    if touch and touch.active and touch.label_ru not in " ".join(bits):
        bits.append(touch.label_ru[:80])
    if state.scenario:
        bits.append(f"Сценарий C: {state.scenario.title_ru} [{state.scenario.quality}]")
    state.summary_ru = " · ".join(bits)[:420]
    return state


def merge_market_state_into_reading(
    reading: object,
    state: MarketState,
):
    from dataclasses import replace

    present = list(getattr(reading, "present", []) or [])
    absent = list(getattr(reading, "absent", []) or [])

    for line in state.passport.to_lines()[:2]:
        if line not in " ".join(present):
            present.append(line[:90])
    for line in state.smc_checklist.to_present_lines():
        if line not in present:
            present.append(line)
    for line in state.smc_checklist.to_absent_lines():
        if line not in absent:
            absent.append(line)
    if state.exhaustion.active and state.exhaustion.label_ru:
        present.append(state.exhaustion.label_ru[:90])
    if state.compression and state.compression.weak_pullback:
        present.append(state.compression.label_ru[:90])
    if state.accumulation.phase in {"accumulation", "distribution"}:
        present.append(state.accumulation.label_ru[:90])
    if state.touch_compression_label:
        present.append(state.touch_compression_label[:90])
    if state.methodology and state.methodology.label_ru:
        present.append(state.methodology.label_ru[:90])
    if state.fib and not state.fib.checklist_ok:
        absent.append("Fib+OB чеклист не закрыт")
    if not any(z.valid for z in state.zones if z.kind in {"demand", "supply"}):
        absent.append("нет валидной зоны спрос/предложение (ликв.+пробой+откат)")

    live = getattr(reading, "live_scenario", "range")
    if state.scenario:
        sid = state.scenario.scenario_id
        if sid == "exhaustion_warning":
            live = "exhaustion"
        elif sid == "reversal":
            live = "reversal"
        elif sid == "continuation":
            live = "continuation"
        elif sid == "breakout_compression":
            live = "range"
        elif sid == "range_fade":
            live = "range"

    return replace(
        reading,
        present=present[:12],
        absent=absent[:12],
        live_scenario=live,
        narrative=(getattr(reading, "narrative", "") or "").strip()[:500],
    )
