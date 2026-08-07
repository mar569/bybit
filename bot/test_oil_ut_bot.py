"""Tests for UT Bot Alerts Pine port."""
from __future__ import annotations

from types import SimpleNamespace

from bot.oil_ut_bot import compute_oil_ut_bot, format_oil_ut_alert, ut_blocks_side


def _bar(o: float, h: float, l: float, c: float) -> SimpleNamespace:
    return SimpleNamespace(open=o, high=h, low=l, close=c, volume=1.0, open_time=0.0)


def _trend_up(n: int = 40, start: float = 80.0, step: float = 0.15) -> list:
    bars = []
    px = start
    for _ in range(n):
        o = px
        c = px + step
        bars.append(_bar(o, max(o, c) + 0.05, min(o, c) - 0.05, c))
        px = c
    return bars


def _trend_down(n: int = 40, start: float = 85.0, step: float = 0.15) -> list:
    bars = []
    px = start
    for _ in range(n):
        o = px
        c = px - step
        bars.append(_bar(o, max(o, c) + 0.05, min(o, c) - 0.05, c))
        px = c
    return bars


def test_ut_bot_uptrend_goes_long():
    bars = _trend_up(50)
    # forming bar excluded — add dummy last
    bars.append(_bar(90, 91, 89, 90.5))
    ut = compute_oil_ut_bot(bars, key_value=1.0, atr_period=10, exclude_forming=True)
    assert ut is not None
    assert ut.side == "long"
    assert ut.trail > 0
    assert len(ut.trails) == len(ut.buy_flags)
    assert any(ut.bar_bull)


def test_ut_bot_downtrend_goes_short():
    bars = _trend_down(50)
    bars.append(_bar(70, 71, 69, 70.5))
    ut = compute_oil_ut_bot(bars, key_value=1.0, atr_period=10, exclude_forming=True)
    assert ut is not None
    assert ut.side == "short"


def test_ut_bot_flip_buy_after_down_then_up():
    bars = _trend_down(30, start=90.0, step=0.2)
    bars.extend(_trend_up(25, start=bars[-1].close, step=0.25))
    bars.append(_bar(bars[-1].close, bars[-1].close + 0.1, bars[-1].close - 0.1, bars[-1].close))
    ut = compute_oil_ut_bot(bars, exclude_forming=True)
    assert ut is not None
    # After strong rebound should be long or have had a buy somewhere
    assert ut.side == "long" or any(ut.buy_flags)


def test_ut_blocks_side():
    bars = _trend_up(40)
    bars.append(_bar(86, 87, 85, 86.5))
    ut = compute_oil_ut_bot(bars, exclude_forming=True)
    assert ut is not None
    assert ut.side == "long"
    assert ut_blocks_side(ut, "short") is True
    assert ut_blocks_side(ut, "long") is False


def test_format_oil_ut_alert_buy():
    bars = _trend_up(40)
    # force a structure that may or may not flip on last — format works either way
    ut = compute_oil_ut_bot(bars + [_bar(88, 89, 87, 88.2)], exclude_forming=True)
    assert ut is not None
    text = format_oil_ut_alert(ut, interval_minutes=5)
    assert "UT" in text
    assert "UKOUSD" in text


def test_exclude_forming_shortens_series():
    bars = _trend_up(30)
    full = compute_oil_ut_bot(bars, exclude_forming=False)
    clipped = compute_oil_ut_bot(bars, exclude_forming=True)
    assert full is not None and clipped is not None
    assert len(clipped.trails) == len(full.trails) - 1
