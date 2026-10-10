from __future__ import annotations

import pytest

from bot.integrations.hummingbot_api import (
    client_from_config,
    format_hbot_status_html,
)


class _Cfg:
    hummingbot_api_url = "http://127.0.0.1:8000"
    hummingbot_api_username = "admin"
    hummingbot_api_password = "secret"


def test_client_from_config() -> None:
    assert client_from_config(_Cfg()) is not None
    assert client_from_config(object()) is None


def test_format_status_html() -> None:
    html = format_hbot_status_html(
        info={"api_version": "1.0", "docker_available": True},
        bots={"bot-a": {}},
        mqtt={"mqtt_connected": True, "active_bots": ["bot-a"], "discovered_bots": []},
        accounts_dist={"total_portfolio_value": 1234.5},
    )
    assert "Hummingbot" in html
    assert "1,234" in html or "1234" in html
