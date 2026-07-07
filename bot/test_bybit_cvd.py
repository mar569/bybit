from __future__ import annotations

import time

from bot.bybit_cvd import (
    BybitTakerCvdLiveTracker,
    aggregate_taker_trades,
    build_taker_cvd_snapshot,
    summarize_taker_cvd,
)
from bot.ta_range_trade import TaFactorContext, build_factor_context
from bot.bybit_klines import KlineBar


def test_aggregate_taker_trades_sell_dominant() -> None:
    now = int(time.time() * 1000)
    trades = [
        {"time": str(now - 60_000), "size": "100", "side": "Sell"},
        {"time": str(now - 30_000), "size": "200", "side": "Sell"},
        {"time": str(now - 10_000), "size": "50", "side": "Buy"},
    ]
    cutoff = now - 5 * 60_000
    buy, sell, count, _ = aggregate_taker_trades(trades, cutoff_ms=cutoff)
    assert count == 3
    assert sell > buy
    snap = summarize_taker_cvd(buy, sell, trade_count=count, window_minutes=5.0)
    assert snap.ratio <= 0.38
    assert "taker-sell" in snap.detail


def test_build_taker_cvd_snapshot() -> None:
    now = int(time.time() * 1000)
    trades = [{"time": str(now - i * 1000), "size": "10", "side": "Sell"} for i in range(40)]
    snap = build_taker_cvd_snapshot(trades, lookback_minutes=30.0, min_trades=25)
    assert snap is not None
    assert snap.trade_count >= 25
    assert snap.ratio <= 0.38


def test_live_tracker_builds_snapshot() -> None:
    tracker = BybitTakerCvdLiveTracker(lambda: ["TESTUSDT"])
    now_ms = int(time.time() * 1000)
    for i in range(50):
        tracker.record_trade(
            "TESTUSDT",
            ts_ms=now_ms - i * 2000,
            side="Sell",
            size=10.0,
        )
    snap = tracker.build_snapshot("TESTUSDT", lookback_minutes=30.0, min_trades=25)
    assert snap is not None
    assert snap.source == "live"
    assert snap.ratio <= 0.38


def test_build_factor_context_prefers_taker() -> None:
    bars = [
        KlineBar(1.0, 1.0, 1.1, 0.9, 1.05, 100.0),
        KlineBar(2.0, 1.05, 1.2, 1.0, 1.15, 120.0),
    ]
    snap = summarize_taker_cvd(10.0, 500.0, trade_count=80, window_minutes=15.0)
    ctx = build_factor_context(bars, None, taker_cvd=snap)
    assert isinstance(ctx, TaFactorContext)
    assert ctx.cvd_ratio <= 0.38
    assert "taker-sell" in ctx.cvd_detail
