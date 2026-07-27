"""Тесты отрисовки TradeRev Fib-сеток на EW-графике."""
from __future__ import annotations


def test_correction_grid_levels_up() -> None:
    """Сетка коррекции: 0% = Т2 (пик), 61.8% = откат от импульса вверх."""
    from bot.chart_elliott_draw import FIB_CORR_LEVELS, FIB_CORR_GOLD

    p0, p_end = 100.0, 120.0
    span = p_end - p0
    lvl618 = p_end - span * 0.618
    lvl382 = p_end - span * 0.382
    assert abs(lvl618 - 107.64) < 0.01
    assert abs(lvl382 - 112.36) < 0.01
    assert 0.618 in FIB_CORR_GOLD
    assert 0.382 in FIB_CORR_GOLD
    assert 0.5 in FIB_CORR_LEVELS
    assert 0.786 in FIB_CORR_LEVELS


def test_impulse_extension_levels_up() -> None:
    """Расширение импульса: FE 100% = 1:1 от W1, от конца W2."""
    from bot.chart_elliott_draw import FIB_IMP_EXT_GOLD, FIB_IMP_EXT_LEVELS

    p0, p1, p2 = 100.0, 110.0, 105.0
    w1 = p1 - p0
    fe100 = p2 + w1 * 1.0
    fe161 = p2 + w1 * 1.618
    assert abs(fe100 - 115.0) < 1e-9
    assert abs(fe161 - 121.18) < 0.01
    assert 1.0 in FIB_IMP_EXT_GOLD
    assert 1.618 in FIB_IMP_EXT_GOLD
    assert 1.272 in FIB_IMP_EXT_LEVELS


def test_draw_overlays_smoke() -> None:
    """Smoke: overlays не падают на минимальном EW-результате."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bot.bybit_klines import KlineBar
    from bot.chart_elliott_draw import draw_elliott_waves
    from bot.elliott_wave import ElliottPoint, ElliottWaveResult

    bars = [
        KlineBar(open_time=1_700_000_000 + i * 300, open=100 + i * 0.1,
                 high=101 + i * 0.1, low=99 + i * 0.1, close=100.5 + i * 0.1, volume=1.0)
        for i in range(40)
    ]
    pts = [
        ElliottPoint("0", 2, 100.0),
        ElliottPoint("1", 8, 112.0),
        ElliottPoint("2", 14, 105.0),
        ElliottPoint("3", 22, 125.0),
        ElliottPoint("4", 28, 118.0),
        ElliottPoint("5", 34, 130.0),
        ElliottPoint("A", 36, 122.0),
        ElliottPoint("B", 37, 126.0),
        ElliottPoint("C", 38, 117.64),  # ~61.8% of 0→5
    ]
    ew = ElliottWaveResult(
        draw_points=pts,
        global_draw_points=pts,
        corr_type="zigzag",
        phase="abc_c",
    )
    fig, ax = plt.subplots(figsize=(8, 4))
    draw_elliott_waves(ax, bars, ew)
    plt.close(fig)


def test_impulse_extension_drawn_when_forming() -> None:
    """Пока нет W5 — рисуется FE-расширение (не сетка коррекции)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bot.bybit_klines import KlineBar
    from bot.chart_elliott_draw import draw_elliott_waves
    from bot.elliott_wave import ElliottPoint, ElliottWaveResult

    bars = [
        KlineBar(open_time=1_700_000_000 + i * 300, open=100, high=101, low=99, close=100, volume=1)
        for i in range(30)
    ]
    pts = [
        ElliottPoint("0", 2, 100.0),
        ElliottPoint("1", 10, 110.0),
        ElliottPoint("2", 16, 105.0),
    ]
    ew = ElliottWaveResult(draw_points=pts, global_draw_points=pts, phase="impulse_2")
    fig, ax = plt.subplots(figsize=(6, 3))
    draw_elliott_waves(ax, bars, ew)
    plt.close(fig)
