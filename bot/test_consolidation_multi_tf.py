from __future__ import annotations

from bot.consolidation_multi_tf import (
    apply_consolidation_trigger_levels,
    resolve_multi_tf_consolidation,
)
from bot.ta_analysis import ConsolidationZone
from bot.test_chart_patterns import _bar


def _flat_bars(n: int, base: float, spread: float) -> list:
    out = []
    for i in range(n):
        lo = base - spread / 2
        hi = base + spread / 2
        out.append(_bar(i, base, hi, lo, base))
    return out


def test_prefers_h1_box_when_ltf_too_narrow() -> None:
    ltf = ConsolidationZone(
        top=180.5,
        bottom=179.8,
        start_idx=0,
        end_idx=40,
        label="лок. боковик 0.4%",
    )
    mid = _flat_bars(90, 178.0, 3.0)
    h1 = _flat_bars(60, 177.5, 6.0)
    picked = resolve_multi_tf_consolidation(
        180.0,
        ltf=ltf,
        mid_bars=mid,
        htf_bars=h1,
    )
    assert picked is not None
    assert "H1" in picked.label or picked.top - picked.bottom > 2.0


def test_h4_can_win_when_price_inside_wide_box() -> None:
    ltf = ConsolidationZone(
        top=182.0,
        bottom=181.5,
        start_idx=0,
        end_idx=30,
        label="лок. боковик 0.3%",
    )
    mid = _flat_bars(90, 178.0, 4.0)
    h1 = _flat_bars(60, 177.0, 8.0)
    h4 = _flat_bars(45, 175.0, 12.0)
    picked = resolve_multi_tf_consolidation(
        176.5,
        ltf=ltf,
        mid_bars=mid,
        htf_bars=h1,
        macro_bars=h4,
    )
    assert picked is not None
    assert picked.bottom <= 176.5 <= picked.top


def test_apply_consolidation_trigger_levels_snaps_breakdown() -> None:
    zone = ConsolidationZone(
        top=180.0,
        bottom=176.0,
        start_idx=0,
        end_idx=10,
        label="H1: боковик",
    )
    bd, bo = apply_consolidation_trigger_levels(
        zone,
        breakdown=200.0,
        breakout=195.0,
        current=178.0,
    )
    assert bd is not None
    assert abs(float(bd) - 176.0 * 0.9995) < 0.05
    assert bo is not None
    assert abs(float(bo) - 180.0 * 1.0005) < 0.05
