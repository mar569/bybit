from __future__ import annotations

from bot.core.asset_class import detect_asset_class, resolve_asset_flags
from bot.chart_display_policy import ed_chart_spec_layers_enabled


def test_detect_crypto_perp() -> None:
    assert detect_asset_class("BTCUSDT", exchange="bybit") == "crypto_perp"


def test_detect_equity() -> None:
    assert detect_asset_class("AAPL") == "equity"


def test_resolve_flags_equity_playbook_off_by_default() -> None:
    flags = resolve_asset_flags("AAPL")
    assert flags.asset_class == "equity"
    assert flags.playbook_enabled is False
    assert flags.quiver_intel is True


def test_chart_spec_layers_default_on_with_playbook() -> None:
    assert ed_chart_spec_layers_enabled() is True
