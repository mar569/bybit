from __future__ import annotations

from .perpfinder_api import PerpFinderClient


def test_parse_funding_payload_extracts_bybit_fields() -> None:
    payload = {
        "rows": [
            {
                "symbol": "BTC",
                "exchanges": {
                    "Bybit": {
                        "rate1h": 0.000001,
                        "rawRate": 0.000012,
                        "intervalHours": 8,
                        "oi": 4_800_000_000.0,
                        "price": 83000.0,
                    }
                },
            }
        ],
        "updatedAt": "2026-01-01T00:00:00.000Z",
    }
    parsed = PerpFinderClient._parse_funding_payload(payload, coin="BTC", venue="Bybit")
    assert parsed["available"] is True
    assert parsed["funding_rate"] == 0.000012
    assert parsed["oi_usd"] == 4_800_000_000.0
    assert parsed["price"] == 83000.0


def test_parse_funding_payload_empty_rows() -> None:
    parsed = PerpFinderClient._parse_funding_payload({"rows": []}, coin="X", venue="Bybit")
    assert parsed["available"] is False
