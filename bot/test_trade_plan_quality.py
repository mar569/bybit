"""Проверка плана: MINA-подобный кейс и нормальный R:R."""
from __future__ import annotations

from bot.trade_plan_quality import validate_trade_plan


class _TaStub:
    verdict = "SHORT"
    action_priority = "short"
    current_price = 0.0909
    entry_zone = (0.09077, 0.09101)
    invalidation_price = 0.09319
    target_prices = [0.09080]
    setup_tps: list[float] = []


def test_mina_like_tp_bumped_on_chart() -> None:
    from bot.chart_position_boxes import chart_plan_targets

    ta = _TaStub()
    out = chart_plan_targets(ta, entry_lo=0.09077, entry_hi=0.09101)  # type: ignore[arg-type]
    assert out
    ref = 0.09077
    assert (ref - out[0]) / ref >= 0.0075


def test_valid_short_plan() -> None:
    ta = _TaStub()
    ta.target_prices = [0.0880, 0.0865]
    pq = validate_trade_plan(ta)  # type: ignore[arg-type]
    assert pq.ok
    assert pq.rr >= 0.75
