"""Tests for TradeRevolution Elliott wave rules integration."""
from __future__ import annotations


def test_w3_never_shortest_fatal():
    """W3 самая короткая из 1/3/5 — должно быть fatal."""
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),  # w1 = 10
        ElliottPoint("2", 10, 105.0),
        ElliottPoint("3", 15, 108.0),  # w3 = 3 (shortest!)
        ElliottPoint("4", 20, 106.0),
        ElliottPoint("5", 25, 115.0),  # w5 = 9
    ]
    violations, valid = _validate_impulse_rules(pts, "up")
    assert not valid, "W3 shortest must be fatal"
    assert any("самая короткая" in v for v in violations)


def test_w3_not_longest_but_not_shortest_soft():
    """W3 not longest but not shortest — допуск, not fatal."""
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 108.0),  # w1 = 8
        ElliottPoint("2", 10, 103.0),
        ElliottPoint("3", 15, 115.0),  # w3 = 12 (middle)
        ElliottPoint("4", 20, 112.0),  # above W1
        ElliottPoint("5", 25, 126.0),  # w5 = 14 (longest)
    ]
    violations, valid = _validate_impulse_rules(pts, "up")
    assert valid, "W3 not shortest → should be valid (soft warning only)"


def test_fib_w2_accepts_786():
    """W2 at 78.6% should be accepted (TradeRevolution allows deep W2)."""
    from bot.elliott_wave import ElliottPoint, _fib_proportion_check

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 114.0),  # w1 = 14
        ElliottPoint("2", 10, 103.0),  # w2 = 11, ratio = 11/14 = 0.786
    ]
    fib = _fib_proportion_check(pts)
    assert fib["w2_ok"], f"W2 at 78.6% should be ok, got ratio={fib['w2']:.3f}"


def test_fib_w2_rejects_too_deep():
    """W2 beyond 85% should fail."""
    from bot.elliott_wave import ElliottPoint, _fib_proportion_check

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 114.0),
        ElliottPoint("2", 10, 101.5),  # ratio = 12.5/14 = 0.893
    ]
    fib = _fib_proportion_check(pts)
    assert not fib["w2_ok"], f"W2 at 89% should fail, got ratio={fib['w2']:.3f}"


def test_classify_abc_zigzag():
    from bot.elliott_wave import ElliottPoint, classify_abc_type
    pts = [ElliottPoint("A", 0, 90.0), ElliottPoint("B", 5, 95.0)]
    # b_retrace = 50% → zigzag
    assert classify_abc_type(pts, 0.50) == "zigzag"


def test_classify_abc_flat():
    from bot.elliott_wave import ElliottPoint, classify_abc_type
    pts = [ElliottPoint("A", 0, 90.0), ElliottPoint("B", 5, 99.0)]
    # b_retrace = 92% → flat
    assert classify_abc_type(pts, 0.92) == "flat"


def test_classify_abc_expanded_flat():
    from bot.elliott_wave import ElliottPoint, classify_abc_type
    pts = [
        ElliottPoint("A", 0, 90.0),
        ElliottPoint("B", 5, 102.0),
        ElliottPoint("C", 10, 88.0),
    ]
    # b_retrace > 1.0, C < A (for up impulse, correction goes down)
    result = classify_abc_type(pts, 1.05, impulse_direction="up")
    assert result == "expanded_flat"


def test_classify_abc_running_flat():
    from bot.elliott_wave import ElliottPoint, classify_abc_type
    pts = [
        ElliottPoint("A", 0, 90.0),
        ElliottPoint("B", 5, 102.0),
        ElliottPoint("C", 10, 92.0),  # C > A → running flat
    ]
    result = classify_abc_type(pts, 1.05, impulse_direction="up")
    assert result == "running_flat"


def test_fib_cluster_building():
    from bot.elliott_wave import ElliottImpulse, ElliottPoint
    from bot.elliott_advanced import build_fib_clusters

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 120.0),
        ElliottPoint("2", 10, 108.0),
        ElliottPoint("3", 15, 150.0),
        ElliottPoint("4", 20, 138.0),
    ]
    imp = ElliottImpulse(
        direction="up", points=pts, current_wave="4", valid=True, quality=70,
        extension="3",
    )
    clusters = build_fib_clusters(imp)
    assert isinstance(clusters, list)
    for c in clusters:
        assert c.strength >= 2
        assert c.price_lo <= c.price_hi


def test_structure_note_new_types():
    from bot.elliott_wave import _structure_note_ru

    note = _structure_note_ru(extension="3", truncated=False, diagonal="", corr_type="expanded_flat")
    assert "расширенный флет" in note
    assert "растяжение волны 3" in note

    note2 = _structure_note_ru(extension="", truncated=False, diagonal="leading", corr_type="running_flat")
    assert "бегущий флет" in note2
    assert "начальная диагональ" in note2
