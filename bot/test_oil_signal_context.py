"""Clear signal drivers: Hormuz fees + inventory."""
from __future__ import annotations

from types import SimpleNamespace

from bot.oil_signal_context import (
    analyze_hormuz_context,
    analyze_inventory_from_news,
    build_signal_drivers,
    format_clear_signal_card,
)


def test_hormuz_3pct_not_final_mixed():
    items = [
        SimpleNamespace(
            title="Iran seeks 5-7% Hormuz toll, Oman proposes around 3%, US wants no fees",
            theme="iran_geo",
            published_ts=1.0e9,
            summary="",
        ),
        SimpleNamespace(
            title="Progress on Hormuz deal but not finality yet negotiations underway",
            theme="iran_geo",
            published_ts=1.0e9,
            summary="",
        ),
    ]
    h = analyze_hormuz_context(items, now=1.0e9 + 600)
    assert h.status in {"not_final", "progress"}
    assert h.for_entry is False
    assert "3%" in h.fee_ru or "3%" in h.line_ru
    assert h.oil_bias == "mixed"


def test_inventory_build_bearish():
    items = [
        SimpleNamespace(
            title="EIA crude oil inventories build 2.5 million barrels surprise",
            theme="inventory",
            published_ts=1.0,
            summary="",
        )
    ]
    inv = analyze_inventory_from_news(items)
    assert inv.tone == "bearish"
    assert "build" in inv.line_ru.lower() or "давлен" in inv.line_ru.lower()


def test_clear_card_has_why_plan():
    items = [
        SimpleNamespace(
            title="Hormuz deal progress Oman 3% service fee US zero fee not final",
            theme="iran_geo",
            published_ts=1.0e9,
            summary="",
        )
    ]
    drivers = build_signal_drivers(
        news_items=items, news_mode="warm", side="WAIT"
    )
    text = format_clear_signal_card(
        side="WAIT",
        quality=5,
        price=80.2,
        drivers=drivers,
        trigger_ru="ждать финал сделки или уровень",
        horizon_ru="1–3д",
    )
    assert "СИГНАЛ WAIT" in text
    assert "Почему" in text
    assert "Фон" in text or "Ормуз" in text
