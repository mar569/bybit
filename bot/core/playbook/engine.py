"""Single decision surface: state, brief inputs, chart spec, alert gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..snapshot import MarketSnapshot, snapshot_from_ta
from .chart_spec import ChartSpec, build_chart_spec
from .states import PlaybookState
from ...plan_staleness import plan_is_stale, plan_staleness_plain
from ...range_breakdown_retest import get_rbr_from_ta
from ...ta_analysis import TAAnalysisResult


@dataclass
class PlaybookResult:
    state: PlaybookState
    headline_ru: str
    body_html: str
    intel_rows: list[tuple[str, str]]
    alert_eligible: bool
    chart_spec: ChartSpec
    block_reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _intel_rows_from_snapshot(snap: MarketSnapshot, ta: TAAnalysisResult | None = None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    price_bits: list[str] = []
    if snap.drawdown_from_high_pct >= 2:
        price_bits.append(f"откат от хая ~{snap.drawdown_from_high_pct:.1f}%")
    if snap.momentum_label:
        price_bits.append(snap.momentum_label.lower())
    if snap.post_pump:
        price_bits.append("после импульса")
    if price_bits:
        rows.append(("Цена", ", ".join(price_bits)))
    elif snap.structure_label and snap.structure_label.lower() not in {"n/a", "—"}:
        rows.append(("Цена", snap.structure_label[:100]))

    oi_line = cvd_line = liq_line = ""
    for line in snap.participation_lines:
        low = line.lower()
        if not oi_line and ("oi" in low or "open interest" in low or "открытый интерес" in low):
            oi_line = line[:120]
        elif not cvd_line and ("cvd" in low or "агресс" in low or "delta" in low):
            cvd_line = line[:120]
        elif not liq_line and ("ликвид" in low or "liq" in low or "liquidation" in low):
            liq_line = line[:120]
    if oi_line:
        rows.append(("OI", oi_line))
    if cvd_line:
        rows.append(("CVD", cvd_line))
    if liq_line:
        rows.append(("Liq", liq_line))

    labels = ("Цена", "OI", "CVD", "Liq")
    by_label = {label: text for label, text in rows}
    if "Цена" not in by_label:
        if snap.phase_label:
            by_label["Цена"] = snap.phase_label[:100]
        elif snap.structure_label and snap.structure_label.lower() not in {"n/a", "—"}:
            by_label["Цена"] = snap.structure_label[:100]
    if ta is not None and "OI" not in by_label:
        oi_n = str(getattr(ta, "oi_narrative_label", "") or "").strip()
        if oi_n:
            by_label["OI"] = oi_n[:120]
    if ta is not None and "Liq" not in by_label:
        liq_note = str(getattr(ta, "liq_cascade_note", "") or getattr(ta, "liq_magnet_note", "") or "").strip()
        if liq_note:
            by_label["Liq"] = liq_note[:120]
    try:
        from ..asset_class import resolve_asset_flags
        from ...adapters.quiver.client import quiver_intel_line

        sym = (getattr(ta, "symbol", "") or "").strip().upper() or snap.symbol
        if sym and resolve_asset_flags(sym).quiver_intel:
            qline = quiver_intel_line(sym)
            if qline and "OI" in by_label and by_label.get("OI") == "—":
                by_label["OI"] = qline
            elif qline and by_label.get("OI") and by_label["OI"] != "—":
                by_label["OI"] = f"{by_label['OI'][:80]} · {qline[:60]}"
    except Exception:
        pass
    return [(label, by_label.get(label) or "—") for label in labels]


def _situation_ru(snap: MarketSnapshot) -> str:
    rbr = snap.rbr
    if rbr:
        phase = str(rbr.get("phase") or "")
        if phase == "fade_top":
            return "После импульса — боковик у потолка диапазона."
        if phase == "await_break":
            return "Боковик: пробоя пола ещё не было."
        if phase == "retest":
            return "Пробой пола был — смотрим retest."
        story = str(rbr.get("story_ru") or rbr.get("label_ru") or "").strip()
        if story:
            return story[:160]
    phase = snap.phase_label.lower()
    if any(k in phase for k in ("боков", "консол", "range", "флэт")):
        return "Консолидация — без market до триггера."
    if snap.post_pump:
        return "Импульс отработан, цена у локального экстремума."
    narrative = snap.reading_narrative.strip()
    if narrative:
        return narrative.split(".")[0][:160] + "."
    return "Смотрим реакцию на уровнях с графика."


def _expect_ru(snap: MarketSnapshot, *, stale_msg: str) -> str:
    if stale_msg:
        return stale_msg
    side = preferred_trade_side_from_snap(snap)
    rbr = snap.rbr
    if rbr and str(rbr.get("direction") or "") == "short":
        if str(rbr.get("phase") or "") == "fade_top":
            return "Ждём отказ сверху и движение вниз — без догонялки."
        if str(rbr.get("phase") or "") == "await_break":
            return "Ждём пробой пола и retest — вход не у потолка."
        return "Сценарий вниз — только по триггеру на графике."
    if side == "short":
        return "Ждём движение вниз по триггеру."
    if side == "long":
        return "Ждём движение вверх по триггеру."
    if snap.verdict in {"LONG", "SHORT"}:
        return "Вход только по триггеру, не market."
    return "Пока наблюдение — ключевые уровни на графике."


def preferred_trade_side_from_snap(snap: MarketSnapshot) -> str:
    if snap.rbr and str(snap.rbr.get("direction") or "") in {"long", "short"}:
        return str(snap.rbr["direction"])
    if snap.action_priority in {"long", "short"}:
        return snap.action_priority
    bias = snap.pattern_foresight_bias.lower()
    if bias == "bearish":
        return "short"
    if bias == "bullish":
        return "long"
    return ""


def _resolve_state(snap: MarketSnapshot, *, stale: bool, alert_eligible: bool) -> PlaybookState:
    if stale:
        return PlaybookState.NO_TRADE
    if alert_eligible:
        return PlaybookState.ARMED
    if snap.verdict in {"LONG", "SHORT"} and not stale:
        return PlaybookState.ARMED
    return PlaybookState.OBSERVE


def _rbr_watch_eligible(ta: TAAnalysisResult, snap: MarketSnapshot) -> bool:
    rbr = snap.rbr or get_rbr_from_ta(ta)
    if not rbr:
        return False
    if str(rbr.get("phase") or "") not in {"fade_top", "await_break", "retest"}:
        return False
    if plan_is_stale(ta):
        return False
    direction = str(rbr.get("direction") or "")
    if direction not in {"long", "short"}:
        return False
    return True


def run_playbook(ta: TAAnalysisResult, *, symbol: str = "") -> PlaybookResult:
    sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
    try:
        from ..asset_class import resolve_asset_flags

        flags = resolve_asset_flags(sym)
        if not flags.playbook_enabled:
            from ...manual_ta import chart_display_hours

            iv = int(getattr(ta, "analysis_interval_minutes", 5) or 5)
            empty = ChartSpec(symbol=sym, interval_minutes=iv, display_hours=chart_display_hours(iv))
            from .brief import format_playbook_brief_html
            from ...human_trade_brief import _symbol_short

            short = _symbol_short(sym) or sym
            body = format_playbook_brief_html(
                state=PlaybookState.OBSERVE,
                symbol=short,
                situation="Класс актива пока без playbook.",
                expect="Только наблюдение.",
                intel_rows=[("Цена", "—"), ("OI", "—"), ("CVD", "—"), ("Liq", "—")],
            )
            return PlaybookResult(
                state=PlaybookState.OBSERVE,
                headline_ru=f"{short} — наблюдение",
                body_html=body,
                intel_rows=[("Цена", "—"), ("OI", "—"), ("CVD", "—"), ("Liq", "—")],
                alert_eligible=False,
                chart_spec=empty,
                meta={"asset_class": flags.asset_class},
            )
    except Exception:
        pass
    snap = snapshot_from_ta(ta, symbol=symbol)
    stale_msg = plan_staleness_plain(ta)
    stale = bool(stale_msg)
    alert_eligible = _rbr_watch_eligible(ta, snap)
    state = _resolve_state(snap, stale=stale, alert_eligible=alert_eligible)
    chart_spec = build_chart_spec(snap)
    intel = _intel_rows_from_snapshot(snap, ta)
    situation = _situation_ru(snap)
    expect = _expect_ru(snap, stale_msg=stale_msg)

    from ...human_trade_brief import _symbol_short

    sym_short = _symbol_short(snap.symbol) or snap.symbol or "Монета"
    headline = f"{sym_short} — {state.badge_ru.split(maxsplit=1)[-1] if state != PlaybookState.OBSERVE else 'наблюдение'}"
    if stale:
        headline = f"{sym_short} — план устарел"

    from .brief import format_playbook_brief_html

    body_html = format_playbook_brief_html(
        state=state,
        symbol=sym_short,
        situation=situation,
        expect=expect,
        intel_rows=intel,
    )

    return PlaybookResult(
        state=state,
        headline_ru=headline,
        body_html=body_html,
        intel_rows=intel,
        alert_eligible=alert_eligible and state == PlaybookState.ARMED,
        chart_spec=chart_spec,
        block_reason=stale_msg,
        meta={"verdict": snap.verdict, "rbr_phase": chart_spec.rbr_phase},
    )
