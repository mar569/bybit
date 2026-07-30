"""Тесты Wave Watcher: gate, setup kinds, format, batcher."""
from __future__ import annotations

import asyncio
from dataclasses import replace

from bot.bybit_klines import KlineBar
from bot.elliott_wave import (
    ElliottAbc,
    ElliottEntryPlan,
    ElliottImpulse,
    ElliottPoint,
    ElliottWaveResult,
)
from bot.settings import ScannerSettings
from bot.wave_alerts import (
    WaveBatcher,
    WaveEvent,
    build_wave_event,
    compute_wave_importance,
    format_wave_alert,
)


def _imp(**kwargs) -> ElliottImpulse:
    pts = kwargs.pop(
        "points",
        [
            ElliottPoint("0", 0, 100.0),
            ElliottPoint("1", 10, 110.0),
            ElliottPoint("2", 20, 104.0),
            ElliottPoint("3", 30, 122.0),
            ElliottPoint("4", 40, 114.0),
            ElliottPoint("5", 50, 121.5),
        ],
    )
    base = ElliottImpulse(
        direction="up",
        points=pts,
        current_wave="4",
        valid=True,
        quality=72,
        fib_classic_ok=True,
        fib_w2_ok=True,
        fib_w4_ok=True,
        fib_w2_ratio=0.60,
        fib_w4_ratio=0.44,
    )
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def _bars(n: int = 60, price: float = 114.0) -> list[KlineBar]:
    out: list[KlineBar] = []
    for i in range(n):
        p = price + (i % 5) * 0.1
        out.append(
            KlineBar(
                open_time=1_700_000_000 + i * 300,
                open=p,
                high=p + 0.2,
                low=p - 0.2,
                close=p,
                volume=1000.0,
            )
        )
    return out


def test_build_wave_event_wave4_zone():
    settings = ScannerSettings.default()
    settings = replace(
        settings,
        wave_enabled=True,
        wave_min_importance=40.0,
        wave_allow_structure_watch=True,
    )
    ew = ElliottWaveResult(
        impulse=_imp(current_wave="4"),
        phase="impulse_4",
        label_ru="бычий импульс · волна 4",
        confidence=7,
        entry_plan=ElliottEntryPlan(
            mode="conservative",
            side="long",
            entry_price=114.5,
            stop_price=112.0,
            tp1=122.0,
            trigger="пробой хая волны 3",
            ready=False,
        ),
        has_global=True,
        has_local=True,
        global_label_ru="G: импульс up",
        local_label_ru="L: волна 4",
    )
    # fib_classic_ok is on impulse, not result — ok
    event = build_wave_event("Bybit", "AKEUSDT", ew, _bars(), settings, price=114.2)
    assert event is not None
    assert event.setup_kind == "wave4_zone"
    assert event.side == "long"
    assert event.entry_price == 114.5
    assert "волна 4" in event.expect_ru.lower() or "4" in event.expect_ru


def test_build_wave_event_wave2_breakout():
    settings = ScannerSettings.default()
    settings = replace(
        settings,
        wave_enabled=True,
        wave_min_importance=40.0,
        wave_setup_modes=("wave2_breakout", "wave3_impulse", "wave5_bounce"),
    )
    ew = ElliottWaveResult(
        impulse=_imp(current_wave="2"),
        phase="impulse_2",
        label_ru="бычий · волна 2",
        confidence=8,
        entry_plan=ElliottEntryPlan(
            mode="conservative",
            side="long",
            entry_price=110.5,
            stop_price=99.5,
            tp1=122.0,
            tp2=130.0,
            trigger="обновление хая волны 1",
            ready=True,
            rr=3.0,
        ),
    )
    event = build_wave_event("Bybit", "BTCUSDT", ew, _bars(price=110.4), settings)
    assert event is not None
    assert event.setup_kind == "wave2_breakout"
    assert event.entry_ready is True


def test_build_wave_event_rejects_low_quality():
    settings = ScannerSettings.default()
    settings = replace(
        settings,
        wave_enabled=True,
        wave_min_impulse_quality=80,
        wave_min_importance=10.0,
    )
    ew = ElliottWaveResult(
        impulse=_imp(quality=50, fib_classic_ok=False, fib_w2_ok=False, fib_w4_ok=False),
        phase="impulse_4",
        confidence=4,
        entry_plan=ElliottEntryPlan(mode="wait", side="long", ready=False),
    )
    assert build_wave_event("Bybit", "XUSDT", ew, _bars(), settings) is None


def test_format_wave_alert_contains_side():
    event = WaveEvent(
        exchange="Bybit",
        symbol="AKEUSDT",
        timestamp=1_700_000_000.0,
        price=0.1234,
        side="long",
        setup_kind="wave4_zone",
        phase="impulse_4",
        label_ru="тест",
        detail="detail",
        importance=70.0,
        confidence=7,
        entry_price=0.12,
        stop_price=0.11,
        tp_prices=(0.14, 0.15),
        expect_ru="ждём отскок вверх",
        fib_note="W4 Fib 44%",
        global_label="G up",
        local_label="L iv",
    )
    text = format_wave_alert(event)
    assert "LONG" in text
    assert "AKE" in text or "AKEUSDT" in text
    assert "ждём отскок" in text


def test_importance_entry_higher_than_complete():
    settings = ScannerSettings.default()
    ew_entry = ElliottWaveResult(
        impulse=_imp(valid=True, fib_classic_ok=True, quality=75),
        confidence=7,
        path_reason_ru="path",
        has_global=True,
        has_local=True,
    )
    ew_done = ElliottWaveResult(
        impulse=_imp(valid=True, fib_classic_ok=False, quality=60),
        confidence=5,
    )
    a = compute_wave_importance(ew_entry, "wave2_breakout", settings)
    b = compute_wave_importance(ew_done, "impulse_complete", settings)
    assert a > b


def test_wave_batcher_flush_respects_limit():
    sent: list[WaveEvent] = []

    async def _dispatch(ev: WaveEvent) -> bool:
        sent.append(ev)
        return True

    async def _run() -> None:
        batcher = WaveBatcher(_dispatch)
        settings = replace(
            ScannerSettings.default(),
            wave_min_importance=10.0,
            wave_max_per_minute=1,
            wave_batch_interval_seconds=0,
            wave_symbol_cooldown_seconds=0,
        )
        for i, sym in enumerate(("AAAUSDT", "BBBUSDT")):
            ev = WaveEvent(
                exchange="Bybit",
                symbol=sym,
                timestamp=1.0,
                price=1.0,
                side="long",
                setup_kind="entry_ready",
                phase="impulse_2",
                label_ru="",
                detail="",
                importance=80.0 + i,
            )
            await batcher.offer(ev, settings)
        n = await batcher.flush(settings)
        assert n == 1
        assert len(sent) == 1
        assert sent[0].symbol == "BBBUSDT"  # higher importance

    asyncio.run(_run())
