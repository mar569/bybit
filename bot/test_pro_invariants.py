"""PRO-инварианты: цели/path/подпись согласованы с направлением."""
from __future__ import annotations

from bot.chart_pattern_models import ChartPattern, PatternLine, PatternPoint
from bot.pattern_foresight import build_pattern_foresight, foresight_enriches_scenario
from bot.pro_invariants import (
    bias_side,
    pattern_target_ok,
    pick_directional_tp,
    resolve_wait_plan_levels,
    sanitize_path_prices,
    sanitize_targets,
    target_matches_side,
)


def test_sanitize_targets_short_drops_above() -> None:
    out = sanitize_targets("short", 3.027, [3.544, 2.960, 2.938, 2.856], trigger=2.960)
    assert 3.544 not in out
    assert 2.960 not in out  # TP ≠ trigger
    assert out[0] == 2.938


def test_sanitize_targets_long_drops_below() -> None:
    out = sanitize_targets("long", 100.0, [99.0, 100.5, 101.0, 103.0], trigger=100.5)
    assert 99.0 not in out
    assert 100.5 not in out
    assert out[0] == 101.0


def test_path_final_must_match_bias() -> None:
    prices, labels = sanitize_path_prices(
        "short",
        3.027,
        [3.027, 3.10, 2.90],
        ["сейчас", "откат", "цель"],
    )
    assert prices[-1] < 3.027
    assert labels[-1] == "цель"


def test_foresight_htf_conflict_uses_htf_target() -> None:
    """LTF bull target не должен клеиться к HTF SHORT summary."""
    ltf = ChartPattern(
        kind="triangle_symmetric",
        subtype="continuation",
        status="confirmed",
        points=(PatternPoint(10, 3.0, "a"), PatternPoint(20, 3.1, "b")),
        lines=(PatternLine(10, 3.0, 20, 3.1, "body"),),
        zone_top=3.2,
        zone_bottom=2.95,
        neckline=None,
        pole_height=0.2,
        target_price=3.544,  # вверх
        stop_price=2.90,
        confidence=0.75,
        score_breakdown={},
        source_rule="test",
        label_ru="Треугольник",
        direction="bullish",
        entry_mode="breakout",
    )
    htf = ChartPattern(
        kind="head_shoulders",
        subtype="reversal",
        status="confirmed",
        points=(
            PatternPoint(5, 3.3, "left_shoulder"),
            PatternPoint(12, 3.5, "head"),
            PatternPoint(18, 3.35, "right_shoulder"),
        ),
        lines=(PatternLine(8, 3.15, 16, 3.15, "neckline"),),
        zone_top=3.5,
        zone_bottom=3.1,
        neckline=PatternLine(8, 3.15, 16, 3.15, "neckline"),
        pole_height=0.35,
        target_price=2.80,  # вниз
        stop_price=3.55,
        confidence=0.80,
        score_breakdown={},
        source_rule="test",
        label_ru="Голова и плечи",
        direction="bearish",
        entry_mode="breakout",
    )
    fs = build_pattern_foresight(
        [ltf],
        htf_patterns=[htf],
        primary=ltf,
        atr=0.05,
        current_price=3.027,
    )
    assert fs.bias == "short"
    assert fs.htf_conflict
    scene = foresight_enriches_scenario(fs)
    assert "3.544" not in scene
    assert "2.80" in scene or "2.8" in scene
    assert pattern_target_ok("bearish", 3.027, 2.80)
    assert not pattern_target_ok("bearish", 3.027, 3.544)


def test_foresight_drops_invalid_short_target() -> None:
    bad = ChartPattern(
        kind="head_shoulders",
        subtype="reversal",
        status="confirmed",
        points=(PatternPoint(5, 3.3, "left_shoulder"),),
        lines=(),
        zone_top=3.5,
        zone_bottom=3.0,
        neckline=None,
        pole_height=0.3,
        target_price=3.544,  # выше цены — невалидно для bearish
        stop_price=3.55,
        confidence=0.8,
        score_breakdown={},
        source_rule="test",
        label_ru="Голова и плечи",
        direction="bearish",
    )
    fs = build_pattern_foresight([bad], primary=bad, atr=0.05, current_price=3.027)
    assert fs.bias == "short"
    assert fs.target_text == ""
    assert "3.544" not in foresight_enriches_scenario(fs)


def test_resolve_wait_plan_tp_not_trigger() -> None:
    class _Sc:
        trigger_price = 2.96
        target_prices = [2.96, 2.938, 2.856]
        stop_price = 3.1406

    class _TA:
        current_price = 3.027
        verdict = "WAIT"
        action_priority = "short"
        invalidation_price = 3.1406
        breakdown_level = 2.96
        breakout_level = 3.125
        target_prices = [2.96, 3.544]
        bearish_scenario = _Sc()
        bullish_scenario = None

    stop, tp1, trig = resolve_wait_plan_levels(_TA())
    assert trig == 2.96
    assert stop == 3.1406
    assert tp1 is not None
    assert tp1 < 2.96
    assert tp1 == 2.938


def test_bias_side_and_pick_tp() -> None:
    assert bias_side("WAIT", "short") == "short"
    assert target_matches_side("short", 10.0, 9.5)
    assert not target_matches_side("short", 10.0, 10.5)
    tp = pick_directional_tp(
        side="short",
        current=10.0,
        target_prices=[10.5, 9.8, 9.5],
        trigger=9.9,
    )
    assert tp == 9.8
