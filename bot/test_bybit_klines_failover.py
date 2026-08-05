"""Bybit REST hosts failover."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.bybit_klines import BYBIT_REST_HOSTS, fetch_bybit_klines_sync


def test_fetch_bybit_klines_falls_back_to_bytick():
    assert "bytick" in BYBIT_REST_HOSTS[1]

    good = {
        "retCode": 0,
        "result": {
            "list": [
                ["1700000000000", "80", "81", "79", "80.5", "100"],
            ]
        },
    }

    calls: list[str] = []

    class _Resp:
        def read(self):
            import json

            return json.dumps(good).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=25):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        if "api.bybit.com" in url:
            raise OSError("Name or service not known")
        return _Resp()

    with patch("bot.bybit_klines.urllib.request.urlopen", side_effect=fake_urlopen):
        bars = fetch_bybit_klines_sync("BZUSDT", interval="5", limit=50)

    assert len(bars) == 1
    assert abs(bars[0].close - 80.5) < 1e-9
    assert any("bybit.com" in u for u in calls)
    assert any("bytick.com" in u for u in calls)
