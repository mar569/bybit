from types import SimpleNamespace

from bot.chart_clean_sr import _collect_levels
from bot.bybit_klines import KlineBar


def _bar(i: int, o: float, h: float, l: float, c: float) -> KlineBar:
    return KlineBar(open_time=1_700_000_000 + i * 300, open=o, high=h, low=l, close=c, volume=1.0)


def test_collect_levels_support_resistance() -> None:
    bars = [_bar(i, 0.0052, 0.0053, 0.0051, 0.00525) for i in range(40)]
    ta = SimpleNamespace(
        current_price=0.00526,
        breakdown_level=0.0052,
        breakout_level=0.00544,
        nearest_support=0.00521,
        nearest_resistance=0.0055,
        consolidation=SimpleNamespace(top=0.00544, bottom=0.0052),
    )
    levels = _collect_levels(bars, ta)  # type: ignore[arg-type]
    sides = {l.side for l in levels}
    assert "support" in sides
    assert "resistance" in sides
