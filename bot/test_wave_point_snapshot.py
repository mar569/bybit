"""Точки WaveEvent → индексы баров по open_time."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_renderer import _apply_wave_snapshot_points, _remap_ew_points_ot
from bot.ta_analysis import TAAnalysisResult


def _bars(n: int = 80, start: float = 1_700_000_000.0) -> list[KlineBar]:
    return [
        KlineBar(
            open_time=start + i * 300,
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
            volume=1.0,
        )
        for i in range(n)
    ]


def test_remap_ew_points_by_open_time() -> None:
    bars = _bars(50)
    snaps = (
        ("1", bars[10].open_time, 1.05),
        ("2", bars[20].open_time, 0.98),
        ("3", bars[30].open_time, 1.12),
    )
    pts = _remap_ew_points_ot(snaps, bars)
    assert [p.label for p in pts] == ["1", "2", "3"]
    assert [p.index for p in pts] == [10, 20, 30]


def test_apply_snapshot_overrides_ta() -> None:
    bars = _bars(40)
    ta = TAAnalysisResult(verdict="SHORT")
    ok = _apply_wave_snapshot_points(
        ta,
        bars,
        global_ot=(
            ("0", bars[5].open_time, 1.2),
            ("1", bars[10].open_time, 1.0),
            ("2", bars[15].open_time, 1.1),
            ("3", bars[25].open_time, 0.9),
        ),
    )
    assert ok
    assert len(ta.elliott_global_draw_points) == 4
    assert len(ta.elliott_draw_points) >= 4
