"""Tests for Vataga oil triangle interpretation."""
from __future__ import annotations

from types import SimpleNamespace

from bot.oil_triangle import (
    interpret_oil_triangle,
    score_triangle_votes,
    triangle_blocks_side,
)


def _pat(**kwargs):
    base = dict(
        kind="triangle_ascending",
        label_ru="Восходящий треугольник",
        status="forming",
        direction="bullish",
        confidence=0.72,
        stop_price=82.5,
        target_price=84.0,
        volume_contracted=True,
        volume_breakout=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_forming_ascending_waits_and_blocks_short():
    ta = SimpleNamespace(
        primary_chart_pattern=_pat(),
        chart_patterns=[_pat()],
        elliott_triangle_kind="",
        elliott_triangle_bias="",
    )
    plan = interpret_oil_triangle(ta, proposed_side="SHORT")
    assert plan is not None
    assert plan.action == "wait"
    assert plan.priority == "long"
    assert plan.allow_long is True
    assert plan.allow_short is False
    assert triangle_blocks_side(plan, "short")


def test_confirmed_descending_goes_short():
    ta = SimpleNamespace(
        primary_chart_pattern=_pat(
            kind="triangle_descending",
            label_ru="Нисходящий треугольник",
            status="confirmed",
            direction="bearish",
        ),
        chart_patterns=[],
        elliott_triangle_kind="",
        elliott_triangle_bias="",
    )
    plan = interpret_oil_triangle(ta)
    assert plan is not None
    assert plan.action == "short"
    assert plan.allow_short is True
    assert plan.allow_long is False
    long_pts, short_pts, factors = score_triangle_votes(plan)
    assert short_pts >= 3
    assert long_pts == 0
    assert any("ПРОБОЙ" in f or "пробой" in f.lower() or "△" in f for f in factors)


def test_expanding_is_caution():
    ta = SimpleNamespace(
        primary_chart_pattern=_pat(
            kind="expanding_triangle",
            label_ru="Расходящийся треугольник",
            status="forming",
            direction="neutral",
        ),
        chart_patterns=[],
        elliott_triangle_kind="",
        elliott_triangle_bias="",
    )
    plan = interpret_oil_triangle(ta)
    assert plan is not None
    assert plan.priority == "caution"
    assert plan.action == "wait"
    assert plan.allow_long is False
    assert plan.allow_short is False


def test_false_breakout_cancel():
    ta = SimpleNamespace(
        primary_chart_pattern=_pat(
            kind="false_breakout",
            label_ru="Ложный пробой",
            status="forming",
            direction="bullish",
        ),
        chart_patterns=[],
        elliott_triangle_kind="",
        elliott_triangle_bias="",
    )
    plan = interpret_oil_triangle(ta)
    assert plan is not None
    assert "ложн" in plan.why_ru.lower() or plan.action == "cancel"
