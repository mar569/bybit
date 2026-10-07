from __future__ import annotations

from .exhaustion_detector import ExhaustionSignal
from .market_phase import AccumulationDistribution
from .market_state import MarketState
from .pullback_compression import PullbackCompression
from .scenario_engine import (
    SCENARIO_BREAKOUT_COMPRESSION,
    SCENARIO_EXHAUSTION,
    pick_trading_scenario,
)
from .touch_compression import TouchCompression
from .asset_passport import AssetPassport


def _minimal_state(**kwargs) -> MarketState:
    base = MarketState(
        passport=AssetPassport(
            symbol="TESTUSDT",
            tier="alt",
            character_ru="alt volatile",
            btc_note="",
            recent_ru="",
            trade_bias_hint="",
        )
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def test_exhaustion_scenario_first() -> None:
    st = _minimal_state(
        exhaustion=ExhaustionSignal(True, "weak_high", "хай без объёма", True),
    )
    pick = pick_trading_scenario(st, verdict="LONG")
    assert pick.scenario_id == SCENARIO_EXHAUSTION
    assert pick.quality == "F"


def test_touch_compression_scenario() -> None:
    touch = TouchCompression(
        active=True,
        touches=5,
        span_bars=12,
        level=100.0,
        label_ru="сжатие касаний у 100",
    )
    st = _minimal_state(
        touch_compression=touch,
        htf_structure="bullish",
        range_position=0.62,
        entry_quality="good",
    )
    pick = pick_trading_scenario(st, verdict="WAIT")
    assert pick.scenario_id == SCENARIO_BREAKOUT_COMPRESSION
    assert pick.action in {"LONG", "WATCH"}
