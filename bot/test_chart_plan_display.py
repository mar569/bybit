from __future__ import annotations

from .chart_plan_display import clamp_tp_for_chart


def test_short_tp_clamped_for_chart() -> None:
    entry = 0.345
    tp, stop, label = clamp_tp_for_chart(
        side="short", entry=entry, tp=0.302, stop=0.355,
    )
    assert tp > 0.302
    assert tp >= entry * 0.89
    assert "далее" in label or tp < entry
