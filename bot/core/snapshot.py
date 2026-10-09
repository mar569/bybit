"""Normalized market + TA slice for PlaybookEngine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..range_breakdown_retest import get_rbr_from_ta
from ..ta_analysis import TAAnalysisResult


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    interval_minutes: int
    current_price: float
    verdict: str
    phase_label: str
    structure_label: str
    momentum_label: str
    drawdown_from_high_pct: float
    post_pump: bool
    rbr: dict[str, Any] | None
    participation_lines: tuple[str, ...]
    break_up: float
    break_down: float
    invalidation_price: float
    target_prices: tuple[float, ...]
    reading_narrative: str
    reading_seek_label: str
    pattern_foresight_bias: str
    action_priority: str


def snapshot_from_ta(ta: TAAnalysisResult, *, symbol: str = "") -> MarketSnapshot:
    sym = (symbol or getattr(ta, "symbol", "") or "").strip().upper()
    interval = int(
        getattr(ta, "interval_minutes", 0)
        or getattr(ta, "analysis_interval_minutes", 0)
        or getattr(ta, "interval", 5)
        or 5
    )
    cur = float(getattr(ta, "current_price", 0) or 0)
    tps = tuple(float(x) for x in (getattr(ta, "target_prices", None) or []) if x)
    lines = tuple(str(x).strip() for x in (getattr(ta, "market_participation_lines", None) or []) if str(x).strip())
    return MarketSnapshot(
        symbol=sym,
        interval_minutes=interval,
        current_price=cur,
        verdict=(getattr(ta, "verdict", "") or "WAIT").upper(),
        phase_label=str(getattr(ta, "phase_label", "") or getattr(ta, "phase", "") or ""),
        structure_label=str(getattr(ta, "structure_label", "") or ""),
        momentum_label=str(getattr(ta, "momentum_label", "") or getattr(ta, "momentum", "") or ""),
        drawdown_from_high_pct=float(getattr(ta, "drawdown_from_high_pct", 0) or 0),
        post_pump=bool(getattr(ta, "post_pump", False)),
        rbr=get_rbr_from_ta(ta),
        participation_lines=lines,
        break_up=float(getattr(ta, "breakout_trigger_price", 0) or getattr(ta, "break_up", 0) or 0),
        break_down=float(getattr(ta, "breakdown_trigger_price", 0) or getattr(ta, "break_down", 0) or 0),
        invalidation_price=float(getattr(ta, "invalidation_price", 0) or 0),
        target_prices=tps,
        reading_narrative=str(getattr(ta, "reading_narrative", "") or ""),
        reading_seek_label=str(getattr(ta, "reading_seek_label", "") or ""),
        pattern_foresight_bias=str(getattr(ta, "pattern_foresight_bias", "") or ""),
        action_priority=str(getattr(ta, "action_priority", "") or "").lower(),
    )
