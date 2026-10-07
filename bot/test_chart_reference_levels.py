from datetime import datetime, timezone

from bot.bybit_klines import KlineBar
from bot.chart_reference_levels import session_reference_levels


def _bar(ts: float, hi: float, lo: float) -> KlineBar:
    c = (hi + lo) / 2
    return KlineBar(open_time=ts, open=c, high=hi, low=lo, close=c, volume=1.0)


def test_session_levels_split_by_utc_day() -> None:
    d1 = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc).timestamp()
    d2 = datetime(2024, 6, 2, 14, 0, tzinfo=timezone.utc).timestamp()
    bars = [
        _bar(d1, 110, 100),
        _bar(d1 + 7200, 105, 95),
        _bar(d2, 120, 108),
        _bar(d2 + 7200, 118, 112),
    ]
    refs = session_reference_levels(bars)
    kinds = {r.kind for r in refs}
    assert "daily_high" in kinds
    assert "daily_low" in kinds
    assert "prev_daily_high" in kinds
    day_hi = next(r for r in refs if r.kind == "daily_high")
    assert day_hi.price == 120
