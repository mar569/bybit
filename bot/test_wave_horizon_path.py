"""Тесты прогнозного пути волн на 1–3ч."""
from __future__ import annotations


def test_path_after_impulse_is_abc_zigzag() -> None:
    from bot.elliott_advanced import build_most_likely_path
    from bot.elliott_wave import ElliottImpulse, ElliottPoint

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 10, 104.0),
        ElliottPoint("3", 20, 130.0),
        ElliottPoint("4", 25, 122.0),
        ElliottPoint("5", 35, 140.0),
    ]
    imp = ElliottImpulse(
        direction="up", points=pts, current_wave="complete", valid=True, quality=70,
    )
    path = build_most_likely_path(
        impulse=imp, abc=None, triangle=None, complex_corr=None,
        fib_targets=[], current=139.0,
    )
    assert path.bias == "short"
    assert path.scenario == "abc_correction"
    assert 1.0 <= path.horizon_hours <= 3.0
    assert len(path.prices) >= 4
    assert any("A" in L or "C" in L for L in path.labels)


def test_path_wave3_after_w2() -> None:
    from bot.elliott_advanced import build_most_likely_path
    from bot.elliott_wave import ElliottImpulse, ElliottPoint

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 112.0),
        ElliottPoint("2", 12, 105.0),
    ]
    imp = ElliottImpulse(
        direction="up", points=pts, current_wave="2", valid=True, quality=65,
        fib_w2_ok=True, fib_classic_ok=True,
    )
    path = build_most_likely_path(
        impulse=imp, abc=None, triangle=None, complex_corr=None,
        fib_targets=[], current=105.5,
    )
    assert path.bias == "long"
    assert path.scenario == "wave3"
    assert path.prices[-1] > path.prices[0]


def test_draw_horizon_path_smoke() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bot.bybit_klines import KlineBar
    from bot.chart_elliott_draw import draw_wave_horizon_path

    bars = [
        KlineBar(open_time=1_700_000_000 + i * 300, open=100, high=101, low=99, close=100, volume=1)
        for i in range(40)
    ]
    fig, ax = plt.subplots(figsize=(8, 4))
    draw_wave_horizon_path(
        ax,
        bars,
        [100, 98, 99, 96],
        ["сейчас", "A", "B", "C 61.8"],
        bias="short",
        horizon_hours=2.5,
        reason="после 5 → ABC",
        invalidation=101.0,
    )
    plt.close(fig)
