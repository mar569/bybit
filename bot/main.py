from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from .config import Config
from .settings import SettingsManager
from .scanner_engine import SignalEngine
from .telegram_bot import TelegramBot
from .exchanges.binance import BinanceScanner
from .exchanges.bybit import BybitScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

async def main() -> None:
    config = Config.load()
    settings = SettingsManager()
    telegram = TelegramBot(config, settings)
    scanner = SignalEngine(settings, telegram.dispatch_signal)

    binance = BinanceScanner(on_update=scanner.update_snapshot)
    bybit = BybitScanner(on_update=scanner.update_snapshot)

    async def run_scan() -> None:
        await asyncio.gather(
            binance.run(),
            bybit.run(),
        )

    stop_event = asyncio.Event()

    def _handle_stop(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for signame in [signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(signame, _handle_stop)

    try:
        await telegram.start()
        scanner_task = asyncio.create_task(run_scan())
        await stop_event.wait()
    finally:
        logger.info("Shutting down")
        binance.stop()
        bybit.stop()
        await telegram.stop()
        scanner_task.cancel()
        await asyncio.gather(scanner_task, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
