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
from .exchanges.bybit import BybitScanner
from .bybit_liquidations import BybitLiquidationTracker
from .binance_liquidations import BinanceLiquidationTracker
from .liquidation_alerts import LiquidationAlertService
from .chart_screenshot import chart_capture_service
from .outcome_tracker import OutcomeTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Шум от Telegram/httpx (каждый getUpdates = "HTTP/1.1 200 OK") — не ошибки.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 300


async def _scanner_heartbeat_loop(scanner: SignalEngine) -> None:
    """Периодический лог — подтверждает, что биржи шлют данные (без спама httpx)."""
    await asyncio.sleep(60)
    while True:
        d = scanner.get_diagnostics()
        logger.info(
            "Scanner heartbeat: pairs=%d (Bybit %d, Binance %d) ready=%d hist=%d "
            "signals=%s prob=%s both_oi_price=%s",
            d["pairs_tracked"],
            d.get("pairs_bybit", 0),
            d.get("pairs_binance", 0),
            d["pairs_ready"],
            d["max_history_points"],
            "ON" if d["signals_enabled"] else "OFF",
            "ON" if d.get("probability_filter_enabled") else "OFF",
            "AND" if d.get("require_both_oi_and_price") else "OR",
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


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

    liquidation_alerts = LiquidationAlertService(
        lambda: settings.settings,
        telegram.dispatch_liquidation_alert,
    )

    def liquidation_symbols() -> list[str]:
        if settings.settings.liquidation_all_symbols and bybit.symbols:
            return bybit.symbols
        return scanner.get_bybit_top_symbols()

    def liquidation_bybit_enabled() -> bool:
        return bybit_enabled() and settings.settings.liquidation_alerts_enabled

    def liquidation_binance_enabled() -> bool:
        return binance_enabled() and settings.settings.liquidation_alerts_enabled

    async def on_liquidation_event(event) -> None:
        await liquidation_alerts.on_liquidation(event)

    liquidation_tracker = BybitLiquidationTracker(
        liquidation_symbols,
        enabled=liquidation_bybit_enabled,
        on_event=on_liquidation_event,
    )
    binance_liquidation_tracker = BinanceLiquidationTracker(
        enabled=liquidation_binance_enabled,
        on_event=on_liquidation_event,
    )
    scanner.attach_liquidation_tracker(liquidation_tracker)

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
    heartbeat_task: asyncio.Task | None = None
    outcome_task: asyncio.Task | None = None
    liq_task: asyncio.Task | None = None
    binance_liq_task: asyncio.Task | None = None
    try:
        await telegram.start()
        try:
            if not binance.symbols:
                await binance.load_symbols()
            if not bybit.symbols:
                await bybit.load_symbols()
        except Exception:
            logger.exception("Symbol preload failed — сканер догрузит при старте")
        s = settings.settings
        logger.info(
            "Startup: signals=%s liq=%s | Bybit %d sym | Binance %d sym",
            "ON" if s.signals_enabled else "OFF",
            "ON" if s.liquidation_alerts_enabled else "OFF",
            len(bybit.symbols),
            len(binance.symbols),
        )
        if telegram.redis is not None:
            telegram.outcome_tracker = OutcomeTracker(telegram.redis, scanner)
            outcome_task = asyncio.create_task(telegram.outcome_tracker.run_loop())
        eval_task = asyncio.create_task(scanner.run_evaluation_loop(interval=1.5))
        heartbeat_task = asyncio.create_task(_scanner_heartbeat_loop(scanner))
        liq_task = asyncio.create_task(liquidation_tracker.run())
        binance_liq_task = asyncio.create_task(binance_liquidation_tracker.run())
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
        liquidation_tracker.stop()
        binance_liquidation_tracker.stop()
        await chart_capture_service.close()
        await telegram.stop()
        if scanner_task is not None:
            scanner_task.cancel()
        if eval_task is not None:
            eval_task.cancel()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        if outcome_task is not None:
            outcome_task.cancel()
        if liq_task is not None:
            liq_task.cancel()
        if binance_liq_task is not None:
            binance_liq_task.cancel()
        await asyncio.gather(
            *(
                t
                for t in (
                    scanner_task,
                    eval_task,
                    heartbeat_task,
                    outcome_task,
                    liq_task,
                    binance_liq_task,
                )
                if t is not None
            ),
            return_exceptions=True,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
