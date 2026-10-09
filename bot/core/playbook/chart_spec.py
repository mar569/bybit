"""Declarative chart layers (structure only; no trade plan boxes by default)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChartLevel:
    price: float
    label: str
    kind: str  # range_top | range_bottom | break_up | break_down | structure


@dataclass
class ChartSpec:
    symbol: str
    interval_minutes: int
    display_hours: int
    levels: list[ChartLevel] = field(default_factory=list)
    show_range: bool = False
    show_primary_pattern: bool = True
    show_smc_fvg: bool = True
    show_trade_plan: bool = False
    rbr_phase: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def build_chart_spec(snapshot: object, *, display_hours: int | None = None) -> ChartSpec:
    from ..snapshot import MarketSnapshot

    assert isinstance(snapshot, MarketSnapshot)
    from ...manual_ta import chart_display_hours

    hours = display_hours if display_hours is not None else chart_display_hours(snapshot.interval_minutes)
    spec = ChartSpec(
        symbol=snapshot.symbol,
        interval_minutes=snapshot.interval_minutes,
        display_hours=hours,
        show_trade_plan=False,
    )
    rbr = snapshot.rbr
    if rbr:
        spec.rbr_phase = str(rbr.get("phase") or "")
        spec.show_range = spec.rbr_phase in {"fade_top", "await_break", "retest", "broken"}
        top = float(rbr.get("range_top") or 0)
        floor = float(rbr.get("range_bottom") or 0)
        if top > 0:
            spec.levels.append(ChartLevel(top, "R↑", "range_top"))
        if floor > 0:
            spec.levels.append(ChartLevel(floor, "R↓", "range_bottom"))
    if snapshot.break_up > 0:
        spec.levels.append(ChartLevel(snapshot.break_up, "пробой ↑", "break_up"))
    if snapshot.break_down > 0:
        spec.levels.append(ChartLevel(snapshot.break_down, "пробой ↓", "break_down"))
    try:
        from ...chart_display_policy import chart_trade_plan_on_chart_enabled

        spec.show_trade_plan = chart_trade_plan_on_chart_enabled()
    except Exception:
        pass
    try:
        from ..asset_class import detect_asset_class, resolve_asset_flags

        sym = snapshot.symbol or ""
        spec.meta["asset_class"] = detect_asset_class(sym)
        flags = resolve_asset_flags(sym)
        spec.meta["asset_flags"] = {
            "playbook": flags.playbook_enabled,
            "chart_spec_layers": flags.chart_spec_layers,
            "quiver_intel": flags.quiver_intel,
        }
        if not flags.chart_spec_layers:
            spec.show_smc_fvg = False
    except Exception:
        pass
    return spec


def resolve_chart_zoom_hours(
    ta: object,
    bars: list,
    *,
    symbol: str = "",
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
) -> int:
    """Playbook display_hours as configured baseline; structure-aware expansion unchanged."""
    from ...chart_display_policy import ed_manual_chart_full_history_enabled, ed_playbook_v3_enabled
    from ...manual_ta import manual_chart_zoom_hours
    from ...ta_analysis import TAAnalysisResult

    cfg = configured
    if ed_playbook_v3_enabled() and isinstance(ta, TAAnalysisResult):
        from .cache import get_or_run_playbook

        spec = get_or_run_playbook(ta, symbol=symbol).chart_spec
        if cfg is None or int(cfg) <= 0:
            cfg = spec.display_hours
    if ed_manual_chart_full_history_enabled():
        cfg = None
    zoom = manual_chart_zoom_hours(
        ta,
        bars,
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=cfg,
    )
    if ed_manual_chart_full_history_enabled():
        zoom = max(zoom, int(analysis_hours))
    return min(int(zoom), int(analysis_hours))


def ed_story_zoom_hours_with_playbook(
    ta: object,
    bars: list,
    *,
    symbol: str = "",
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
) -> int:
    from ...chart_ed_story import ed_story_chart_zoom_hours

    cfg = configured
    from ...chart_display_policy import ed_playbook_v3_enabled
    from ...ta_analysis import TAAnalysisResult

    if ed_playbook_v3_enabled() and isinstance(ta, TAAnalysisResult):
        from .cache import get_or_run_playbook

        spec = get_or_run_playbook(ta, symbol=symbol).chart_spec
        if cfg is None or int(cfg) <= 0:
            cfg = spec.display_hours
    return ed_story_chart_zoom_hours(
        ta,
        bars,
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=cfg,
    )


def range_wait_zoom_hours_with_playbook(
    ta: object,
    bars: list,
    *,
    symbol: str = "",
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
) -> int:
    from ...chart_range_wait import range_wait_chart_zoom_hours

    cfg = configured
    from ...chart_display_policy import ed_playbook_v3_enabled
    from ...ta_analysis import TAAnalysisResult

    if ed_playbook_v3_enabled() and isinstance(ta, TAAnalysisResult):
        from .cache import get_or_run_playbook

        spec = get_or_run_playbook(ta, symbol=symbol).chart_spec
        if cfg is None or int(cfg) <= 0:
            cfg = spec.display_hours
    return range_wait_chart_zoom_hours(
        ta,
        bars,
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=cfg,
    )
