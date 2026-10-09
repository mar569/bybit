from __future__ import annotations

from bot.adapters.liquid_trade.auth import sign_request
from bot.adapters.liquid_trade.http_client import bybit_symbol_to_liquid_perp
from bot.adapters.quiver.client import MockQuiverClient, QuiverSnippet, quiver_intel_line
from bot.adapters.quiver.http_client import snippet_from_congress_rows
from bot.core.playbook.engine import run_playbook
from bot.ta_analysis import TAAnalysisResult


def test_bybit_to_liquid_perp() -> None:
    assert bybit_symbol_to_liquid_perp("BTCUSDT") == "BTC-PERP"
    assert bybit_symbol_to_liquid_perp("ETH/USDT") == "ETH-PERP"


def test_liquid_sign_request_headers() -> None:
    headers = sign_request("sk_test", "GET", "/v1/markets/BTC-PERP/ticker", "", None)
    assert "X-Liquid-Signature" in headers
    assert "X-Liquid-Timestamp" in headers
    assert "X-Liquid-Nonce" in headers


def test_quiver_snippet_from_rows() -> None:
    rows = [
        {
            "Ticker": "AAPL",
            "Representative": "Pelosi",
            "Transaction": "Purchase",
            "Party": "D",
            "TransactionDate": "2024-01-10",
        },
        {
            "Ticker": "AAPL",
            "Representative": "Other",
            "Transaction": "Sale",
            "TransactionDate": "2024-06-01",
        },
    ]
    snip = snippet_from_congress_rows("AAPL", rows)
    assert snip is not None
    assert "Pelosi" in snip.headline_ru or "Other" in snip.headline_ru
    assert "покупка" in snip.headline_ru or "продажа" in snip.headline_ru


def test_quiver_intel_line_with_mock_client() -> None:
    class _Client(MockQuiverClient):
        def congress_activity(self, ticker: str) -> QuiverSnippet | None:
            return QuiverSnippet(ticker=ticker, headline_ru="Конгресс: test — покупка")

    line = quiver_intel_line("AAPL", client=_Client())
    assert "Конгресс" in line


def test_playbook_intel_liquid_mock(monkeypatch) -> None:
    import bot.adapters.liquid_trade.http_client as liquid_http

    monkeypatch.setattr(
        liquid_http,
        "liquid_intel_line",
        lambda symbol, **_: "Liquid mark ~68,000",
    )
    ta = TAAnalysisResult(
        current_price=68000.0,
        verdict="WAIT",
        analysis_interval_minutes=5,
    )
    result = run_playbook(ta, symbol="BTCUSDT")
    price_row = next(r for r in result.intel_rows if r[0] == "Цена")
    assert "Liquid mark" in price_row[1]
