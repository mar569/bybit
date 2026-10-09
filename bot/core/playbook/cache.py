"""Single playbook evaluation per TA object (chart + caption share one run)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...ta_analysis import TAAnalysisResult
    from .engine import PlaybookResult


def get_or_run_playbook(ta: TAAnalysisResult, *, symbol: str = "") -> PlaybookResult:
    mm = dict(getattr(ta, "market_metrics", None) or {})
    token = (symbol or getattr(ta, "symbol", "") or "").upper()
    cached = mm.get("playbook_v3")
    if isinstance(cached, dict) and cached.get("token") == token and cached.get("state"):
        from .chart_spec import ChartSpec, ChartLevel
        from .engine import PlaybookResult
        from .states import PlaybookState

        spec_raw = cached.get("chart_spec") or {}
        levels = [
            ChartLevel(float(lv["price"]), str(lv["label"]), str(lv["kind"]))
            for lv in (spec_raw.get("levels") or [])
            if isinstance(lv, dict)
        ]
        spec = ChartSpec(
            symbol=str(spec_raw.get("symbol") or token),
            interval_minutes=int(spec_raw.get("interval_minutes") or 5),
            display_hours=int(spec_raw.get("display_hours") or 14),
            levels=levels,
            show_range=bool(spec_raw.get("show_range")),
            show_primary_pattern=bool(spec_raw.get("show_primary_pattern", True)),
            show_smc_fvg=bool(spec_raw.get("show_smc_fvg", True)),
            show_trade_plan=bool(spec_raw.get("show_trade_plan")),
            rbr_phase=str(spec_raw.get("rbr_phase") or ""),
        )
        return PlaybookResult(
            state=PlaybookState(str(cached["state"])),
            headline_ru=str(cached.get("headline_ru") or ""),
            body_html=str(cached.get("body_html") or ""),
            intel_rows=[(str(a), str(b)) for a, b in (cached.get("intel_rows") or [])],
            alert_eligible=bool(cached.get("alert_eligible")),
            chart_spec=spec,
            block_reason=str(cached.get("block_reason") or ""),
            meta=dict(cached.get("meta") or {}),
        )

    from .engine import run_playbook

    result = run_playbook(ta, symbol=symbol)
    mm["playbook_v3"] = {
        "token": token,
        "state": result.state.value,
        "headline_ru": result.headline_ru,
        "body_html": result.body_html,
        "intel_rows": list(result.intel_rows),
        "alert_eligible": result.alert_eligible,
        "block_reason": result.block_reason,
        "meta": dict(result.meta),
        "chart_spec": {
            "symbol": result.chart_spec.symbol,
            "interval_minutes": result.chart_spec.interval_minutes,
            "display_hours": result.chart_spec.display_hours,
            "show_range": result.chart_spec.show_range,
            "show_primary_pattern": result.chart_spec.show_primary_pattern,
            "show_smc_fvg": result.chart_spec.show_smc_fvg,
            "show_trade_plan": result.chart_spec.show_trade_plan,
            "rbr_phase": result.chart_spec.rbr_phase,
            "levels": [
                {"price": lv.price, "label": lv.label, "kind": lv.kind}
                for lv in result.chart_spec.levels
            ],
        },
    }
    ta.market_metrics = mm
    return result
