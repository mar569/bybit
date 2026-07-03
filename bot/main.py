from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from .config import Config
from .settings import SettingsManager
from .scanner_engine import SignalEngine
from .telegram_bot import TelegramBot
from .exchanges.binance import BinanceScanner
from .outcome_tracker import OutcomeTracker

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
    telegram.scanner = scanner

    def scan_interval() -> float:
        return float(settings.settings.scan_interval_seconds)

    def binance_enabled() -> bool:
        return settings.settings.enabled_binance

    def bybit_enabled() -> bool:
        return settings.settings.enabled_bybit

    binance = BinanceScanner(
        on_update=scanner.update_snapshot,
        scan_interval=scan_interval,
        enabled=binance_enabled,
    )
    bybit = BybitScanner(
        on_update=scanner.update_snapshot,
        scan_interval=scan_interval,
        enabled=bybit_enabled,
    )

    async def run_scan() -> None:
        await asyncio.gather(
            binance.run(),
            bybit.run(),
        )

    stop_event = asyncio.Event()

    def _handle_stop(*_: Any) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for signame in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(signame, _handle_stop)
    else:
        logger.info("Windows detected: use Ctrl+C to stop the bot")

    scanner_task: asyncio.Task | None = None
    eval_task: asyncio.Task | None = None
    outcome_task: asyncio.Task | None = None
    try:
        await telegram.start()
        if telegram.redis is not None:
            telegram.outcome_tracker = OutcomeTracker(telegram.redis, scanner)
            outcome_task = asyncio.create_task(telegram.outcome_tracker.run_loop())
        eval_task = asyncio.create_task(scanner.run_evaluation_loop(interval=1.5))
        scanner_task = asyncio.create_task(run_scan())

        if sys.platform == "win32":
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                stop_event.set()
        else:
            await stop_event.wait()
    finally:
        logger.info("Shutting down")
        binance.stop()
        bybit.stop()
        await telegram.stop()
        if scanner_task is not None:
            scanner_task.cancel()
        if eval_task is not None:
            eval_task.cancel()
        if outcome_task is not None:
            outcome_task.cancel()
        await asyncio.gather(
            *(t for t in (scanner_task, eval_task, outcome_task) if t is not None),
            return_exceptions=True,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
