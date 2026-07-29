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


def test_bull_valid_impulse_rules():
    """Чистый бычий импульс: W2 > старт W1, W4 > W1 и W4 > W2."""
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 10, 104.0),  # выше 100
        ElliottPoint("3", 20, 125.0),
        ElliottPoint("4", 25, 116.0),  # выше W1=110 и выше W2=104
        ElliottPoint("5", 35, 132.0),
    ]
    violations, valid = _validate_impulse_rules(pts, "up")
    assert valid, f"expected valid, got {violations}"


def test_bear_valid_impulse_rules():
    """Падающий импульс: W2 < старт W1, W4 < W1 и W4 < W2."""
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 90.0),
        ElliottPoint("2", 10, 96.0),  # ниже 100
        ElliottPoint("3", 20, 75.0),
        ElliottPoint("4", 25, 84.0),  # ниже W1=90 и ниже W2=96
        ElliottPoint("5", 35, 68.0),
    ]
    violations, valid = _validate_impulse_rules(pts, "down")
    assert valid, f"expected valid bear, got {violations}"


def test_w2_beyond_w1_start_fatal_bull():
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 10, 99.0),  # ниже основания 0
    ]
    violations, valid = _validate_impulse_rules(pts, "up")
    assert not valid
    assert any("зашла за основание" in v for v in violations)


def test_w2_beyond_w1_start_fatal_bear():
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 90.0),
        ElliottPoint("2", 10, 101.0),  # выше основания 0
    ]
    violations, valid = _validate_impulse_rules(pts, "down")
    assert not valid
    assert any("зашла за основание" in v for v in violations)


def test_w4_into_w1_fatal_without_diagonal():
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 10, 104.0),
        ElliottPoint("3", 20, 125.0),
        ElliottPoint("4", 25, 108.0),  # в территорию W1
    ]
    violations, valid = _validate_impulse_rules(pts, "up", allow_overlap_4_1=False)
    assert not valid
    assert any("пересекла волну 1" in v for v in violations)


def test_w4_past_w2_fatal_bull_and_bear():
    from bot.elliott_wave import ElliottPoint, _validate_impulse_rules

    bull = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 10, 104.0),
        ElliottPoint("3", 20, 125.0),
        ElliottPoint("4", 25, 103.0),  # ниже W2, но выше W1? 103 < 110 → также W1
    ]
    # Чтобы изолировать W4 vs W2: W4 выше W1, но ниже W2
    bull_w4_vs_w2 = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 110.0),
        ElliottPoint("2", 10, 104.0),
        ElliottPoint("3", 20, 125.0),
        ElliottPoint("4", 25, 111.0),  # выше W1=110, но... 111 > 104, не пробивает W2
    ]
    # Реальный кейс W4 пробивает W2, оставаясь выше W1 невозможен на up (W2 < W1).
    # На up: W2 < W1 всегда; W4 > W1 ⇒ W4 > W2. Поэтому «пробила W2» при
    # strict означает W4 < W2, что автоматически даёт и overlap с W1.
    v, ok = _validate_impulse_rules(bull, "up")
    assert not ok
    assert any("пробила волну 2" in v_ or "пересекла волну 1" in v_ for v_ in v)

    bear = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 90.0),
        ElliottPoint("2", 10, 96.0),
        ElliottPoint("3", 20, 75.0),
        ElliottPoint("4", 25, 97.0),  # выше W2=96 → пробила волну 2 (+ возможно W1)
    ]
    v2, ok2 = _validate_impulse_rules(bear, "down")
    assert not ok2
    assert any("пробила волну 2" in x for x in v2)

    # sanity: чистая W4 не бьёт W2
    _, ok3 = _validate_impulse_rules(bull_w4_vs_w2, "up")
    assert ok3


def test_overlap_not_auto_valid_as_diagonal():
    """Overlap 4↔1 без клина → invalid; _make_impulse не промоутит в diagonal."""
    from bot.elliott_wave import ElliottPoint, _make_impulse, detect_diagonal_type

    pts = [
        ElliottPoint("0", 0, 100.0),
        ElliottPoint("1", 5, 120.0),
        ElliottPoint("2", 10, 108.0),
        ElliottPoint("3", 20, 150.0),
        ElliottPoint("4", 25, 115.0),  # overlap
        ElliottPoint("5", 35, 155.0),
    ]

    class _B:
        def __init__(self, i: int) -> None:
            self.open_time = i * 60_000
            self.open = self.high = self.low = self.close = 100.0
            self.volume = 1.0

    bars = [_B(i) for i in range(40)]
    assert detect_diagonal_type(pts, "up", bars) == ""
    assert _make_impulse(pts, "up", bars=bars, current_wave="5") is None


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
