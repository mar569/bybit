from __future__ import annotations

import asyncio
import json
import time
import sys
from pathlib import Path
from typing import Any

# ensure repo root is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import types

# provide a minimal dotenv shim if python-dotenv is not installed
if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

import importlib.util


def load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if spec and spec.loader:
        spec.loader.exec_module(module)
    return module


settings_mod = load_module_from_path("bot.settings", ROOT / "bot" / "settings.py")
# load models under a safe name first to avoid package-relative imports
models_path = ROOT / "bot" / "models.py"
models_mod = load_module_from_path("bot_models", models_path)

# load scanner_engine by reading source and replacing relative imports
scanner_path = ROOT / "bot" / "scanner_engine.py"
with open(scanner_path, "r", encoding="utf-8") as f:
    src = f.read()

src = src.replace("from .models import Signal, SnapshotPoint", "from bot_models import Signal, SnapshotPoint")
module = importlib.util.module_from_spec(importlib.util.spec_from_file_location("bot.scanner_engine", str(scanner_path)))
sys.modules["bot.scanner_engine"] = module
exec(compile(src, str(scanner_path), "exec"), module.__dict__)

SettingsManager = settings_mod.SettingsManager
SignalEngine = module.SignalEngine
Signal = models_mod.Signal


class MockTelegram:
    def __init__(self) -> None:
        self.sent: list[Signal] = []

    async def dispatch_signal(self, signal: Signal) -> None:
        self.sent.append(signal)
        print(f"[DISPATCH] {signal.exchange} {signal.symbol} {signal.signal_type} score={signal.signal_score} details={signal.details}")


async def run_simulation() -> None:
    settings_manager = SettingsManager()
    mock = MockTelegram()
    engine = SignalEngine(settings_manager, mock.dispatch_signal)

    now = time.time()

    scenarios = [
        # strong pump: OI large rise + price up
        {
            "exchange": "Binance",
            "symbol": "SOLUSDT",
            "earlier": {"price": 20.0, "open_interest": 100_000.0, "volume_24h": 1_000_000.0, "timestamp": now - (settings_manager.settings.oi_period_minutes * 60) - 5},
            "current": {"price": 22.5, "open_interest": 300_000.0, "volume_24h": 1_500_000.0, "timestamp": now},
        },
        # price pump without huge OI (should produce price_pump)
        {
            "exchange": "Bybit",
            "symbol": "ADAUSDT",
            "earlier": {"price": 0.5, "open_interest": 50_000.0, "volume_24h": 200_000.0, "timestamp": now - (settings_manager.settings.price_pump_window_minutes * 60) - 2},
            "current": {"price": 0.55, "open_interest": 49_000.0, "volume_24h": 400_000.0, "timestamp": now},
        },
        # volume spike scenario
        {
            "exchange": "Binance",
            "symbol": "XRPUSDT",
            "earlier": {"price": 0.4, "open_interest": 120_000.0, "volume_24h": 100_000.0, "timestamp": now - (settings_manager.settings.oi_period_minutes * 60) - 5},
            "current": {"price": 0.42, "open_interest": 130_000.0, "volume_24h": 1_000_000.0, "timestamp": now},
        },
    ]

    # feed earlier then current for each scenario
    for s in scenarios:
        e = s["earlier"]
        await engine.update_snapshot(
            s["exchange"],
            s["symbol"],
            e["price"],
            e["open_interest"],
            e["volume_24h"],
            None,
            None,
            e["timestamp"],
            {},
        )

    # small sleep to simulate time gap
    await asyncio.sleep(0.1)

    for s in scenarios:
        c = s["current"]
        await engine.update_snapshot(
            s["exchange"],
            s["symbol"],
            c["price"],
            c["open_interest"],
            c["volume_24h"],
            None,
            None,
            c["timestamp"],
            {},
        )

    # allow engine to dispatch
    await asyncio.sleep(0.2)

    print("\nSummary: dispatched signals:")
    for sig in mock.sent:
        print(json.dumps({
            "exchange": sig.exchange,
            "symbol": sig.symbol,
            "type": sig.signal_type,
            "score": sig.signal_score,
            "details": sig.details,
        }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run_simulation())
