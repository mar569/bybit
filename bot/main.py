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
from .anomaly_alerts import AnomalyBatcher
from .liquidation_alerts import LiquidationAlertService
from .liquidation_analysis import LiquidationAnalysisEngine, format_liquidation_analysis
from .chart_screenshot import chart_capture_service
from .outcome_tracker import OutcomeTracker
from .analysis_outcome_tracker import AnalysisOutcomeTracker
from .target_watcher import TargetWatcher
from .bybit_cvd import BybitTakerCvdLiveTracker, get_taker_cvd_cache

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
            "Scanner heartbeat: pairs=%d ready=%d oi_ok=%d history_pts=%d signals=%s",
            d["pairs_tracked"],
            d["pairs_ready"],
            d["pairs_with_oi"],
            d["max_history_points"],
            "ON" if d["signals_enabled"] else "OFF",
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def _analysis_heartbeat_loop(analysis_engine: LiquidationAnalysisEngine) -> None:
    await asyncio.sleep(120)
    while True:
        ad = analysis_engine.get_diagnostics()
        logger.info(
            "Analysis heartbeat: scheduled=%d sent=%d pending=%d "
            "skip_thr=%d skip_trend=%d skip_conf=%d skip_cd=%d errors=%d",
            ad["scheduled"],
            ad["sent"],
            ad["pending"],
            ad["skipped_threshold"],
            ad.get("skipped_trend", 0),
            ad["skipped_confidence"],
            ad.get("skipped_cooldown", 0),
            ad["errors"],
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def main() -> None:
    config = Config.load()
    settings = SettingsManager()
    telegram = TelegramBot(config, settings)
    anomaly_batcher = AnomalyBatcher(telegram.dispatch_anomaly)
    scanner = SignalEngine(
        settings,
        telegram.dispatch_signal,
        on_trend_risk=telegram.dispatch_trend_risk,
    )
    scanner.attach_anomaly_batcher(anomaly_batcher)
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

    analysis_engine = LiquidationAnalysisEngine(
        lambda: settings.settings,
        scanner,
        None,
        telegram.dispatch_liquidation_analysis,
        weights_getter=lambda: telegram.get_analysis_adaptive_weights(),
    )
    telegram.analysis_engine = analysis_engine

    async def on_trading_signal(signal) -> None:
        await telegram.dispatch_signal(signal)
        if settings.settings.analysis_enabled and config.analysis_chat_configured:
            try:
                await analysis_engine.schedule_from_signal(signal)
            except Exception:
                logger.exception("Analysis schedule from signal failed %s", signal.symbol)

    scanner.on_signal = on_trading_signal

    async def on_liquidation_alert(event, event_count: int, total_usd: float) -> None:
        s = settings.settings
        if s.liquidation_alerts_enabled:
            await telegram.dispatch_liquidation_alert(event, event_count, total_usd)
        if s.analysis_enabled and config.analysis_chat_configured:
            await analysis_engine.schedule(event, event_count, total_usd)

    liquidation_alerts = LiquidationAlertService(
        lambda: settings.settings,
        on_liquidation_alert,
        oi_usd_getter=scanner.get_symbol_oi_usd,
    )

    def liquidation_symbols() -> list[str]:
        if settings.settings.liquidation_all_symbols and bybit.symbols:
            return bybit.symbols
        return scanner.get_bybit_top_symbols()

    def _liquidation_ws_enabled() -> bool:
        s = settings.settings
        return s.liquidation_alerts_enabled or (
            s.analysis_enabled and config.analysis_chat_configured
        )

    def liquidation_bybit_enabled() -> bool:
        return bybit_enabled() and _liquidation_ws_enabled()

    def liquidation_binance_enabled() -> bool:
        return binance_enabled() and _liquidation_ws_enabled()

    def cvd_ws_enabled() -> bool:
        s = settings.settings
        return bybit_enabled() and (
            _liquidation_ws_enabled()
            or s.signals_enabled
            or config.manual_ta_chat_configured
        )

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
    scanner.attach_binance_liquidation_tracker(binance_liquidation_tracker)
    analysis_engine.attach_liquidation_tracker(liquidation_tracker)
    analysis_engine.attach_binance_liquidation_tracker(binance_liquidation_tracker)

    cvd_live_tracker = BybitTakerCvdLiveTracker(
        liquidation_symbols,
        enabled=cvd_ws_enabled,
    )
    get_taker_cvd_cache().attach_live(cvd_live_tracker)

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
    analysis_outcome_task: asyncio.Task | None = None
    target_task: asyncio.Task | None = None
    liq_task: asyncio.Task | None = None
    binance_liq_task: asyncio.Task | None = None
    cvd_task: asyncio.Task | None = None
    anomaly_task: asyncio.Task | None = None
    analysis_heartbeat_task: asyncio.Task | None = None
    try:
        await telegram.start()
        try:
            if not binance.symbols:
                await binance.load_symbols()
            if not bybit.symbols:
                await bybit.load_symbols()
        except Exception:
            logger.exception("Symbol preload failed")
        s = settings.settings
        logger.info(
            "Startup: signals=%s liq=%s analysis=%s anomaly=%s | Bybit %d | Binance %d",
            "ON" if s.signals_enabled else "OFF",
            "ON" if s.liquidation_alerts_enabled else "OFF",
            "ON" if s.analysis_enabled and config.analysis_chat_configured else "OFF",
            "ON" if s.anomaly_enabled and config.anomaly_chat_configured else "OFF",
            len(bybit.symbols),
            len(binance.symbols),
        )
        if config.analysis_chat_configured:
            logger.info(
                "Analysis chat=%s | liq≥$%s/$%s/$%s alt/standard/major | "
                "conf wait≥%.0f%% dir≥%.0f%% | chart=%s",
                config.telegram_analysis_chat_id,
                int(s.analysis_alt_min_liq_usd),
                int(s.analysis_min_liq_usd),
                int(s.analysis_major_min_liq_usd),
                float(getattr(s, "analysis_min_confidence_wait", 42.0)),
                float(getattr(s, "analysis_min_confidence_directional", 58.0)),
                "ON" if s.analysis_chart_enabled else "OFF",
            )
        elif s.analysis_enabled:
            logger.warning(
                "analysis_enabled=ON but TELEGRAM_ANALYSIS_CHAT_ID is missing — no analysis alerts",
            )
        if telegram.redis is not None:
            telegram.outcome_tracker = OutcomeTracker(telegram.redis, scanner)
            outcome_task = asyncio.create_task(telegram.outcome_tracker.run_loop())
            telegram.analysis_outcome_tracker = AnalysisOutcomeTracker(telegram.redis, scanner)
            analysis_outcome_task = asyncio.create_task(telegram.analysis_outcome_tracker.run_loop())
            telegram.target_watcher = TargetWatcher(
                telegram.redis,
                scanner,
                notify=telegram.dispatch_target_notification,
            )
            target_task = asyncio.create_task(telegram.target_watcher.run_loop())
        eval_task = asyncio.create_task(scanner.run_evaluation_loop(interval=1.5))
        anomaly_task = asyncio.create_task(scanner.run_anomaly_flush_loop(interval=15.0))
        heartbeat_task = asyncio.create_task(_scanner_heartbeat_loop(scanner))
        analysis_heartbeat_task = asyncio.create_task(
            _analysis_heartbeat_loop(analysis_engine),
        )
        liq_task = asyncio.create_task(liquidation_tracker.run())
        binance_liq_task = asyncio.create_task(binance_liquidation_tracker.run())
        if cvd_ws_enabled():
            cvd_task = asyncio.create_task(cvd_live_tracker.run())
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
        cvd_live_tracker.stop()
        await chart_capture_service.close()
        await telegram.stop()
        if scanner_task is not None:
            scanner_task.cancel()
        if eval_task is not None:
            eval_task.cancel()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        if analysis_heartbeat_task is not None:
            analysis_heartbeat_task.cancel()
        if outcome_task is not None:
            outcome_task.cancel()
        if analysis_outcome_task is not None:
            analysis_outcome_task.cancel()
        if target_task is not None:
            target_task.cancel()
        if liq_task is not None:
            liq_task.cancel()
        if binance_liq_task is not None:
            binance_liq_task.cancel()
        if cvd_task is not None:
            cvd_task.cancel()
        if anomaly_task is not None:
            anomaly_task.cancel()
        await asyncio.gather(
            *(
                t
                for t in (
                    scanner_task,
                    eval_task,
                    heartbeat_task,
                    analysis_heartbeat_task,
                    outcome_task,
                    analysis_outcome_task,
                    target_task,
                    liq_task,
                    binance_liq_task,
                    cvd_task,
                    anomaly_task,
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
