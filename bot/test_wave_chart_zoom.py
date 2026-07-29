"""Wave chart: зум покрывает ранние точки, иначе круги за кадром."""
from __future__ import annotations

from bot.bybit_klines import KlineBar
from bot.chart_renderer import _wave_chart_zoom_hours


def _bars(n: int) -> list[KlineBar]:
    return [
        KlineBar(
            open_time=1_700_000_000 + i * 300,
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.0,
            volume=1.0,
        )
        for i in range(n)
    ]


def test_wave_zoom_covers_early_points_not_just_span() -> None:
    # 18ч × 5m = 216 баров; точки на индексах 20..100 (span ~6.7ч)
    # Старый алгоритм → ~12ч (последние бары) → индекс 20 за кадром
    bars = _bars(216)
    zoom = _wave_chart_zoom_hours(
        bars=bars,
        interval_minutes=5,
        analysis_hours=18.0,
        base_zoom=12.0,
        point_indices=[20, 40, 60, 80, 100],
    )
    # Нужно покрыть с ~index 12 до конца ≈ 204 бара ≈ 17ч
    assert zoom >= 16.0, f"expected zoom covering early waves, got {zoom}"
    assert zoom <= 18.0


def test_wave_zoom_empty_indices_keeps_base() -> None:
    zoom = _wave_chart_zoom_hours(
        bars=_bars(100),
        interval_minutes=5,
        analysis_hours=18.0,
        base_zoom=12.0,
        point_indices=[],
    )
    assert zoom == 12.0
