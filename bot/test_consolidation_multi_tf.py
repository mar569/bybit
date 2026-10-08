from bot.bybit_klines import KlineBar
from bot.consolidation_multi_tf import resolve_multi_tf_consolidation
from bot.ta_analysis import ConsolidationZone, detect_local_consolidation


def _bars_flat(low: float, high: float, n: int = 60) -> list[KlineBar]:
    out: list[KlineBar] = []
    t = 1_700_000_000.0
    mid = (low + high) / 2
    for i in range(n):
        out.append(
            KlineBar(open_time=t + i * 900, open=mid, high=high, low=low, close=mid, volume=1.0)
        )
    return out


def test_prefers_tight_ltf_over_wide_h4():
    current = 172.0
    ltf = detect_local_consolidation(_bars_flat(169.0, 180.0, 48), lookback=48, max_range_pct=8.0)
    assert ltf is not None
    h4_bars = _bars_flat(162.0, 188.0, 42)
    h4 = detect_local_consolidation(h4_bars, lookback=42, max_range_pct=24.0)
    assert h4 is not None
    h4 = ConsolidationZone(
        top=h4.top,
        bottom=h4.bottom,
        start_idx=h4.start_idx,
        end_idx=h4.end_idx,
        label="H4: боковик",
    )
    picked = resolve_multi_tf_consolidation(
        current,
        ltf=ltf,
        mid_bars=_bars_flat(169.0, 180.0, 88),
        htf_bars=_bars_flat(165.0, 185.0, 56),
        macro_bars=h4_bars,
    )
    assert picked is not None
    width = float(picked.top) - float(picked.bottom)
    assert width < 15.0
