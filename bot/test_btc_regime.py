from bot.btc_regime import btc_blocks_alt_side, btc_regime_label
from bot.bybit_klines import KlineBar


def _bars(closes: list[float]) -> list[KlineBar]:
    out: list[KlineBar] = []
    for i, c in enumerate(closes):
        out.append(
            KlineBar(
                open_time=float(i * 300_000),
                open=c,
                high=c * 1.001,
                low=c * 0.999,
                close=c,
                volume=1.0,
            )
        )
    return out


def test_btc_regime_bearish_on_dump():
    closes = [100.0] * 10 + [99.0]
    reg, chg, note = btc_regime_label(_bars(closes), lookback=10, block_dump_pct=0.5)
    assert reg == "bearish"
    assert chg < 0
    assert note


def test_btc_blocks_alt_long_in_bearish():
    reason = btc_blocks_alt_side("long", "bearish", symbol="ETHUSDT")
    assert "LONG" in reason.upper() or "лонг" in reason.lower()


def test_btc_allows_btc_itself():
    assert btc_blocks_alt_side("long", "bearish", symbol="BTCUSDT") == ""
