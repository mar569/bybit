from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict
from typing import Any

import redis.asyncio as redis
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .models import Signal
from .probability_engine import PROBABILITY_BYPASS_TYPES
from .scanner_engine import format_oi_usd, SignalEngine
from .set_parser import SET_HELP, parse_set_command
from .market_structure import format_market_structure_block, format_market_structure_compact
from .bybit_market_data import format_bybit_real_data_block, format_bybit_real_data_compact
from .chart_renderer import get_signal_chart_png, render_analysis_chart, render_annotated_chart, render_signal_chart
from .bybit_klines import BybitKlineCache
from .manual_ta import (
    MANUAL_TA_CHART_SOURCES,
    MANUAL_TA_TIMEFRAMES,
    MTA_CALLBACK_PREFIX,
    MTA_ALERT_CALLBACK_PREFIX,
    MTA_INTENT_CALLBACK_PREFIX,
    MTA_MUTE_CALLBACK_PREFIX,
    MTA_WIZARD_KEY,
    MTC_CALLBACK_PREFIX,
    MTCW_CALLBACK_PREFIX,
    MTW_CALLBACK_PREFIX,
    MTW_CANCEL_CALLBACK,
    build_mta_callback,
    build_mta_alert_callback,
    build_mta_intent_callback,
    build_mta_mute_callback,
    build_mtc_callback,
    build_mtcw_callback,
    build_mtw_callback,
    manual_ta_help_text,
    manual_ta_hours,
    manual_ta_wizard_start_text,
    parse_manual_ta_input,
    parse_user_trade_intent,
    parse_mta_callback,
    parse_mta_alert_callback,
    parse_mta_intent_callback,
    parse_mta_mute_callback,
    parse_mtc_callback,
    parse_mtcw_callback,
    parse_mtw_callback,
)
from .ta_range_trade import merge_liq_stats_dict
from .ta_analysis import (
    detect_repeat_spike_dump_risk,
    format_scenario_update_html,
    evaluate_entry_readiness,
    ta_manual_detailed_html,
    ta_analysis_chart_caption_html,
    ta_signal_caption_html,
    should_skip_noise_signal,
    ta_telegram_caption_html,
    ta_user_intent_html,
    ta_display_score,
    fmt_price,
)
from .scenario_watcher import ScenarioUpdate, ScenarioWatcher
from .test_signals import build_test_signals
from .liquidation_alerts import (
    LiquidationAlertEvent,
    base_ticker,
    coinglass_url,
    format_liquidation_alert,
)
from .liquidation_analysis import (
    AnalysisFactor,
    LiquidationAnalysisResult,
    format_liquidation_analysis,
)
from .analysis_outcome_tracker import AnalysisOutcomeSummary
from .anomaly_alerts import AnomalyEvent, format_anomaly_alert
from .trade_playbook import (
    build_hot_caption,
    build_pro_detail_html,
    resolve_trade_playbook,
)
from .signal_quality_gate import (
    assess_signal_quality,
    attach_cvd_to_signal_details,
    format_manual_ta_flow_html,
    format_quality_hot_html,
    format_quality_warnings_html,
)
from .bybit_cvd import get_taker_cvd_cache
from .chart_screenshot import chart_capture_service
from .settings import SettingsManager, clamp_cooldown_seconds

logger = logging.getLogger(__name__)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _plain_caption(text: str) -> str:
    stripped = _HTML_TAG_RE.sub("", text or "")
    return html.unescape(stripped)


def _signal_cooldown_seconds(signal: Signal, settings) -> int:
    is_vertical = signal.signal_type in {"vertical_pump", "vertical_dump"}
    is_impulse = signal.signal_type in {"impulse_pump", "impulse_dump"}
    if is_vertical:
        return clamp_cooldown_seconds(settings.breakout_cooldown_seconds, default=150)
    if is_impulse:
        return clamp_cooldown_seconds(settings.impulse_cooldown_seconds, default=120)
    return clamp_cooldown_seconds(settings.signal_cooldown_seconds, default=120)

EXCHANGE_LABEL = {
    "binance": ("🟡", "Binance"),
    "bybit": ("⚫", "ByBit"),
}


class TelegramBot:
    def __init__(self, config: Config, settings_manager: SettingsManager) -> None:
        self.config = config
        self.settings_manager = settings_manager
        self.scanner: SignalEngine | None = None
        self.analysis_engine: Any | None = None
        self.outcome_tracker: Any | None = None
        self.analysis_outcome_tracker: Any | None = None
        self.target_watcher: Any | None = None
        self._signal_pro_cache: dict[str, str] = {}
        self._last_signal_analysis_time: dict[str, float] = {}
        self._last_send_time: dict[int, float] = {}
        self._minute_send_times: dict[int, list[float]] = {}
        self._unreachable_chats: set[int] = set()
        self._send_lock = asyncio.Lock()
        self._last_symbol_signal_time: dict[str, float] = {}
        self._symbol_dispatch_locks: dict[str, asyncio.Lock] = {}
        self.application: Application | None = None
        self._run_task: asyncio.Task | None = None
        self.redis: redis.Redis | None = None
        self._manual_ta_last: dict[tuple[int, str, int], dict[str, Any]] = {}
        self._manual_ta_last_by_chat: dict[int, dict[str, Any]] = {}
        self._manual_ta_alerts: dict[tuple[int, str, int, str], dict[str, Any]] = {}
        self._manual_ta_muted: dict[tuple[int, str], float] = {}
        self._manual_ta_alert_task: asyncio.Task | None = None
        self._manual_alert_kline_cache = BybitKlineCache(ttl_seconds=15.0)
        self.scenario_watcher = ScenarioWatcher()
        self._scenario_watch_task: asyncio.Task | None = None
        self._pause_snapshot: dict[str, bool] | None = None

    _BOT_PAUSE_KEYS: tuple[str, ...] = (
        "signals_enabled",
        "liquidation_alerts_enabled",
        "analysis_enabled",
        "anomaly_enabled",
        "scenario_watch_enabled",
        "manual_ta_alerts_enabled",
    )

    _NOTIFICATION_CHANNELS: tuple[tuple[str, str, str], ...] = (
        ("signals_enabled", "signals", "📡 Сигналы сканера"),
        ("liquidation_alerts_enabled", "liq", "💧 Ликвидации"),
        ("analysis_enabled", "analysis", "🧠 Анализ ликвидаций"),
        ("anomaly_enabled", "anomaly", "⚡ Аномалии"),
        ("scenario_watch_enabled", "scenario", "🔮 Сценарии (фаза 2)"),
        ("manual_ta_alerts_enabled", "mta_alert", "🔔 Алерты ручного TA"),
    )

    def _bot_notifications_blocked(self) -> bool:
        return bool(self.settings_manager.settings.bot_paused)

    def _channel_setting_field(self, channel_id: str) -> str | None:
        for field, cid, _ in self._NOTIFICATION_CHANNELS:
            if cid == channel_id:
                return field
        return None

    def _any_notification_channel_on(self) -> bool:
        settings = self.settings_manager.settings
        return any(getattr(settings, field) for field, _, _ in self._NOTIFICATION_CHANNELS)

    def _sync_bot_paused_from_channels(self) -> None:
        self.settings_manager.update(bot_paused=not self._any_notification_channel_on())

    def _on_channel_disabled(self, channel_id: str) -> None:
        if channel_id == "scenario":
            self.scenario_watcher.clear_all()
        elif channel_id == "mta_alert":
            self._manual_ta_alerts.clear()

    def _set_all_notification_channels(self, enabled: bool) -> None:
        updates = {field: enabled for field, _, _ in self._NOTIFICATION_CHANNELS}
        self.settings_manager.update(**updates)
        self._sync_bot_paused_from_channels()
        if not enabled:
            self.scenario_watcher.clear_all()
            self._manual_ta_alerts.clear()

    def _toggle_notification_channel(self, channel_id: str) -> str:
        field = self._channel_setting_field(channel_id)
        if field is None:
            return ""
        settings = self.settings_manager.settings
        new_val = not bool(getattr(settings, field))
        self.settings_manager.update(**{field: new_val})
        if not new_val:
            self._on_channel_disabled(channel_id)
        self._sync_bot_paused_from_channels()
        if new_val:
            self._pause_snapshot = None
        for _, cid, label in self._NOTIFICATION_CHANNELS:
            if cid == channel_id:
                return f"{label} → {'ВКЛ' if new_val else 'ВЫКЛ'}"
        return ""

    def _build_notifications_panel_text(self) -> str:
        settings = self.settings_manager.settings
        lines = [
            "<b>🎛 Каналы уведомлений</b>",
            "Включайте и выключайте каждое направление отдельно.",
            f"Общий статус: <b>{'⏸ всё выкл' if settings.bot_paused else '▶️ работает'}</b>\n",
        ]
        for field, _, label in self._NOTIFICATION_CHANNELS:
            on = bool(getattr(settings, field))
            mark = "✅" if on else "❌"
            lines.append(f"{mark} {label}: <b>{'ВКЛ' if on else 'ВЫКЛ'}</b>")
        lines.append(
            "\n<i>⏸ Стоп / ▶️ Старт на клавиатуре — быстро выключить или восстановить всё.</i>"
        )
        return "\n".join(lines)

    def _notifications_keyboard(self) -> InlineKeyboardMarkup:
        settings = self.settings_manager.settings
        rows: list[list[InlineKeyboardButton]] = []
        for field, channel_id, label in self._NOTIFICATION_CHANNELS:
            on = bool(getattr(settings, field))
            short = label.split(" ", 1)[-1][:22]
            rows.append([
                InlineKeyboardButton(
                    self._mark(f"{short} {'ON' if on else 'OFF'}", on),
                    callback_data=f"toggle_ch:{channel_id}",
                ),
            ])
        rows.append([
            InlineKeyboardButton("▶️ Всё ВКЛ", callback_data="toggle_ch:all_on"),
            InlineKeyboardButton("⏸ Всё ВЫКЛ", callback_data="toggle_ch:all_off"),
        ])
        return InlineKeyboardMarkup(rows)

    async def _show_notifications_panel(self, update: Update) -> None:
        if update.message is None:
            return
        await update.message.reply_text(
            self._build_notifications_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._notifications_keyboard(),
        )

    async def _handle_channels_callback(
        self,
        update: Update,
        query: CallbackQuery,
        payload: str,
    ) -> bool:
        if not payload.startswith("toggle_ch:"):
            return False
        if not self._is_admin(update):
            await query.answer("Нет доступа.", show_alert=True)
            return True

        action = payload[len("toggle_ch:"):]
        label = ""
        if action == "all_on":
            if self._pause_snapshot is None:
                settings = self.settings_manager.settings
                self._pause_snapshot = {
                    key: bool(getattr(settings, key)) for key in self._BOT_PAUSE_KEYS
                }
            self._set_all_notification_channels(True)
            self._pause_snapshot = None
            label = "Все каналы → ВКЛ"
        elif action == "all_off":
            settings = self.settings_manager.settings
            if not settings.bot_paused:
                self._pause_snapshot = {
                    key: bool(getattr(settings, key)) for key in self._BOT_PAUSE_KEYS
                }
            self._set_all_notification_channels(False)
            label = "Все каналы → ВЫКЛ"
        else:
            label = self._toggle_notification_channel(action)

        await query.answer(f"✅ {label}" if label else "OK", show_alert=False)
        await self._safe_edit_message_text(
            query,
            self._build_notifications_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._notifications_keyboard(),
        )
        return True

    def _eval_signal_readiness(
        self,
        ta: Any,
        signal: Signal,
        *,
        for_filter: bool = False,
    ) -> tuple[bool, str]:
        s = self.settings_manager.settings
        cvd_ratio = None
        try:
            raw = signal.details.get("cvd_ratio")
            cvd_ratio = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            cvd_ratio = None
        return evaluate_entry_readiness(
            ta,
            signal.side,
            signal.signal_score,
            min_ta_score=s.actionable_min_ta_score,
            max_trigger_dist_pct=s.actionable_max_trigger_dist_pct,
            min_timing_score=s.actionable_min_signal_score,
            max_timing_score=s.actionable_max_signal_score,
            require_smc=s.actionable_require_smc,
            check_scanner_timing=for_filter,
            signal_type=signal.signal_type,
            accept_armed=s.actionable_accept_armed,
            cvd_ratio=cvd_ratio,
            cvd_short_max=s.signal_cvd_short_max_ratio,
            cvd_long_min=s.signal_cvd_long_min_ratio,
        )

    async def start(self) -> None:
        self.application = Application.builder().token(self.config.telegram_token).build()
        self.application.add_handler(CommandHandler("start", self.on_start))
        self.application.add_handler(CommandHandler("help", self.on_help))
        self.application.add_handler(CommandHandler("status", self.on_status))
        self.application.add_handler(CommandHandler("settings", self.on_settings))
        self.application.add_handler(CommandHandler("history", self.on_history))
        self.application.add_handler(CommandHandler("set", self.on_set))
        self.application.add_handler(CommandHandler("test", self.on_test))
        self.application.add_handler(CommandHandler("test_analysis", self.on_test_analysis))
        self.application.add_handler(CommandHandler("scan", self.on_scan))
        self.application.add_handler(CommandHandler("chart", self.on_chart))
        self.application.add_handler(CommandHandler("ta", self.on_ta_help))
        self.application.add_handler(CommandHandler("pause", self.on_pause))
        self.application.add_handler(CommandHandler("cancel", self.on_cancel))
        self.application.add_handler(CallbackQueryHandler(self.on_callback_query))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.on_manual_ta_photo))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_manual_ta_text),
            group=1,
        )
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text_message))

        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        try:
            self.redis = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            await self.redis.ping()
            logger.info("Redis client initialized: %s", redis_url)
        except Exception:
            self.redis = None
            logger.info("Redis not available, continuing without persistence")

        await self.application.initialize()
        await self.application.start()
        try:
            await self.application.updater.start_polling()
        except Exception:
            self._run_task = asyncio.create_task(self.application.run_polling())

        s = self.settings_manager.settings
        try:
            await self.application.bot.send_message(
                chat_id=self.config.telegram_admin_id,
                text=(
                    "✅ Сканер запущен (Bybit/Binance API v5).\n"
                    f"{self._signals_status_line()}.\n"
                    f"⚡ Пульс: <b>{s.pulse_period_minutes}м</b> "
                    f"(OI≥{s.pulse_oi_rise_percent}% / цена≥{s.pulse_price_rise_percent}%)\n"
                    f"📈 LONG <b>{s.long_period_minutes}м</b> OI≥{s.oi_rise_percent}% | "
                    f"📉 SHORT <b>{s.short_period_minutes}м</b> OI≥{s.oi_drop_percent}%\n"
                    f"🚀 Мега: +{','.join(str(int(t)) for t in s.flash_price_tiers)}% за "
                    f"{','.join(str(m) for m in s.flash_window_minutes)}м\n"
                    f"💥 Ликвидации: {'вкл' if s.liquidation_alerts_enabled else 'выкл'} "
                    f"(≥${int(s.liquidation_min_usd):,} · Binance+Bybit)".replace(",", " ")
                    + f"\n🧠 Анализ: {'вкл' if s.analysis_enabled and self.config.analysis_chat_configured else 'выкл'} "
                    f"(≥${int(s.analysis_min_liq_usd):,} · conf≥{s.analysis_min_confidence:.0f}%)".replace(",", " ")
                    + f"\n📈 График к сигналам: <b>{'ON' if s.signal_chart_enabled else 'OFF'}</b> "
                    f"· режим <b>{s.signal_chart_source}</b> "
                    f"{'(TA-разметка)' if s.signal_chart_source == 'annotated' else '(скрин TV/CG)'}"
                    + (
                        f"\n📐 Ручной TA-чат: <b>ON</b> (id {self.config.telegram_manual_ta_chat_id})"
                        if self.config.manual_ta_chat_configured
                        else "\n📐 Ручной TA-чат: <b>выкл</b> (задайте TELEGRAM_MANUAL_TA_CHAT_ID)"
                    )
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=self._reply_keyboard(),
            )
        except Exception as exc:
            logger.warning("Failed to send startup welcome message: %s", exc)

        alert_chat_id = self.config.telegram_alert_chat_id
        if alert_chat_id is not None:
            try:
                chat = await self.application.bot.get_chat(alert_chat_id)
                logger.info("Alert chat OK: %s (%s)", chat.title or chat.username or chat.id, alert_chat_id)
            except BadRequest as exc:
                logger.warning(
                    "TELEGRAM_ALERT_CHAT_ID=%s недоступен: %s. "
                    "Очистите переменную или добавьте бота в чат.",
                    alert_chat_id,
                    exc,
                )
            except Exception as exc:
                logger.warning("Could not verify TELEGRAM_ALERT_CHAT_ID=%s: %s", alert_chat_id, exc)

        analysis_chat_id = self.config.telegram_analysis_chat_id
        if analysis_chat_id is not None:
            try:
                chat = await self.application.bot.get_chat(analysis_chat_id)
                logger.info(
                    "Analysis chat OK: %s (%s)",
                    chat.title or chat.username or chat.id,
                    analysis_chat_id,
                )
            except BadRequest as exc:
                logger.warning(
                    "TELEGRAM_ANALYSIS_CHAT_ID=%s недоступен: %s. "
                    "Добавьте бота в чат или очистите переменную.",
                    analysis_chat_id,
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "Could not verify TELEGRAM_ANALYSIS_CHAT_ID=%s: %s",
                    analysis_chat_id,
                    exc,
                )
        elif s.analysis_enabled:
            logger.warning(
                "analysis_enabled=ON, но TELEGRAM_ANALYSIS_CHAT_ID не задан — "
                "разборы ликвидаций не будут отправляться"
            )

        manual_ta_chat_id = self.config.telegram_manual_ta_chat_id
        if manual_ta_chat_id is not None:
            try:
                chat = await self.application.bot.get_chat(manual_ta_chat_id)
                logger.info(
                    "Manual TA chat OK: %s (%s)",
                    chat.title or chat.username or chat.id,
                    manual_ta_chat_id,
                )
            except BadRequest as exc:
                logger.warning(
                    "TELEGRAM_MANUAL_TA_CHAT_ID=%s недоступен: %s. "
                    "Добавьте бота в чат или очистите переменную.",
                    manual_ta_chat_id,
                    exc,
                )
            except Exception as exc:
                logger.warning(
                    "Could not verify TELEGRAM_MANUAL_TA_CHAT_ID=%s: %s",
                    manual_ta_chat_id,
                    exc,
                )

        logger.info("Telegram bot started")
        if self._manual_ta_alert_task is None or self._manual_ta_alert_task.done():
            self._manual_ta_alert_task = asyncio.create_task(self._manual_ta_alert_loop())
        if self._scenario_watch_task is None or self._scenario_watch_task.done():
            self._scenario_watch_task = asyncio.create_task(self._scenario_watch_loop())

    async def stop(self) -> None:
        if self.application is None:
            return
        await self.application.updater.stop_polling()
        await self.application.stop()
        await self.application.shutdown()
        if self._manual_ta_alert_task is not None:
            self._manual_ta_alert_task.cancel()
            try:
                await self._manual_ta_alert_task
            except asyncio.CancelledError:
                pass
            self._manual_ta_alert_task = None
        if self._scenario_watch_task is not None:
            self._scenario_watch_task.cancel()
            try:
                await self._scenario_watch_task
            except asyncio.CancelledError:
                pass
            self._scenario_watch_task = None
        if self.redis is not None:
            try:
                await self.redis.close()
            except Exception:
                pass

    def _chat_env_hint(self, chat_id: int) -> str:
        if chat_id == self.config.notification_chat_id:
            return "TELEGRAM_ALERT_CHAT_ID"
        if self.config.telegram_analysis_chat_id == chat_id:
            return "TELEGRAM_ANALYSIS_CHAT_ID"
        if self.config.telegram_anomaly_chat_id == chat_id:
            return "TELEGRAM_ANOMALY_CHAT_ID"
        if self.config.anomaly_chat_id == chat_id:
            if self.config.telegram_anomaly_chat_id is None:
                return "TELEGRAM_ANALYSIS_CHAT_ID (аномалии)"
            return "TELEGRAM_ANOMALY_CHAT_ID"
        if self.config.telegram_manual_ta_chat_id == chat_id:
            return "TELEGRAM_MANUAL_TA_CHAT_ID"
        return f"chat_id={chat_id}"

    def _mark_chat_unreachable(self, chat_id: int, reason: str) -> None:
        if chat_id in self._unreachable_chats:
            return
        self._unreachable_chats.add(chat_id)
        logger.warning(
            "Telegram чат %s недоступен (%s) — проверьте %s или отключите канал",
            chat_id,
            reason,
            self._chat_env_hint(chat_id),
        )

    def _handle_send_error(self, chat_id: int, exc: Exception) -> bool:
        """Log Telegram send errors; return True if chat should be treated as dead."""
        if isinstance(exc, Forbidden):
            self._mark_chat_unreachable(chat_id, str(exc))
            return True
        if isinstance(exc, BadRequest):
            msg = str(exc)
            if "Chat not found" in msg or "group chat was deleted" in msg:
                self._mark_chat_unreachable(chat_id, msg)
                return True
            logger.error("Telegram BadRequest for chat %s: %s", chat_id, exc)
            return False
        return False

    async def _send_to_chat(
        self,
        chat_id: int,
        message: str,
        keyboard: InlineKeyboardMarkup,
        is_priority: bool,
    ) -> bool:
        if self.application is None:
            return False
        if chat_id in self._unreachable_chats:
            return False

        settings = self.settings_manager.settings
        async with self._send_lock:
            now = time.time()
            recent = [t for t in self._minute_send_times.get(chat_id, []) if now - t < 60.0]
            if len(recent) >= settings.telegram_max_per_minute:
                logger.warning(
                    "Telegram rate limit: skip chat %s (%d msg/min)",
                    chat_id,
                    settings.telegram_max_per_minute,
                )
                return False

            last = self._last_send_time.get(chat_id, 0.0)
            wait_for = settings.telegram_min_interval_seconds - (now - last)
            if wait_for > 0:
                await asyncio.sleep(wait_for)

            for attempt in range(2):
                try:
                    sent = await self.application.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                        disable_notification=not is_priority,
                        reply_markup=keyboard,
                    )
                    sent_at = time.time()
                    self._last_send_time[chat_id] = sent_at
                    bucket = self._minute_send_times.setdefault(chat_id, [])
                    bucket.append(sent_at)
                    self._minute_send_times[chat_id] = [t for t in bucket if sent_at - t < 60.0]

                    return True
                except RetryAfter as exc:
                    if attempt == 0:
                        logger.warning("Telegram flood control chat %s, wait %ss", chat_id, exc.retry_after)
                        await asyncio.sleep(float(exc.retry_after) + 1.0)
                        continue
                    logger.warning("Telegram flood control chat %s, message dropped", chat_id)
                    return False
                except (Forbidden, BadRequest) as exc:
                    self._handle_send_error(chat_id, exc)
                    return False
                except Exception:
                    logger.exception("Failed to send signal to chat %s", chat_id)
                    return False
        return False

    async def _send_chart(
        self,
        chat_id: int,
        png_bytes: bytes,
        caption: str,
        *,
        is_priority: bool,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> bool:
        if self.application is None:
            return False
        if chat_id in self._unreachable_chats:
            return False
        async with self._send_lock:
            raw_caption = caption or ""
            # Long captions often exceed safe HTML entity boundaries.
            # Send them as plain text to avoid malformed-tag BadRequest.
            if len(raw_caption) > 900:
                try:
                    await self.application.bot.send_photo(
                        chat_id=chat_id,
                        photo=png_bytes,
                        caption=_plain_caption(raw_caption)[:1000],
                        disable_notification=not is_priority,
                        reply_markup=keyboard,
                    )
                    self._last_send_time[chat_id] = time.time()
                    return True
                except RetryAfter as exc:
                    logger.warning("Telegram chart flood chat %s, wait %ss", chat_id, exc.retry_after)
                    await asyncio.sleep(float(exc.retry_after) + 1.0)
                    return False
                except (Forbidden, BadRequest) as exc:
                    self._handle_send_error(chat_id, exc)
                    return False
                except Exception:
                    logger.exception("Failed to send long chart caption to chat %s", chat_id)
                    return False
            html_caption = raw_caption[:1000]
            try:
                await self.application.bot.send_photo(
                    chat_id=chat_id,
                    photo=png_bytes,
                    caption=html_caption,
                    parse_mode=ParseMode.HTML,
                    disable_notification=not is_priority,
                    reply_markup=keyboard,
                )
                self._last_send_time[chat_id] = time.time()
                return True
            except RetryAfter as exc:
                logger.warning("Telegram chart flood chat %s, wait %ss", chat_id, exc.retry_after)
                await asyncio.sleep(float(exc.retry_after) + 1.0)
                return False
            except (Forbidden, BadRequest) as exc:
                if self._handle_send_error(chat_id, exc):
                    return False
                logger.warning("Telegram chart BadRequest for chat %s: %s", chat_id, exc)
                try:
                    plain_caption = _plain_caption(raw_caption)[:1000]
                    await self.application.bot.send_photo(
                        chat_id=chat_id,
                        photo=png_bytes,
                        caption=plain_caption,
                        disable_notification=not is_priority,
                        reply_markup=keyboard,
                    )
                    self._last_send_time[chat_id] = time.time()
                    return True
                except Exception:
                    logger.exception("Failed to send chart fallback to chat %s", chat_id)
                    return False
            except Exception:
                logger.exception("Failed to send chart to chat %s", chat_id)
                return False

    def _signal_keyboard(self, signal: Signal, pro_token: str = "") -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("📊 CoinGlass", url=signal.link),
                InlineKeyboardButton(
                    f"📋 {signal.symbol}",
                    callback_data=f"symcopy:{signal.symbol}",
                ),
            ],
        ]
        if pro_token:
            rows.append([InlineKeyboardButton("📖 Подробнее", callback_data=f"sigpro:{pro_token}")])
        return InlineKeyboardMarkup(rows)

    async def _store_signal_pro(self, text: str) -> str:
        token = uuid.uuid4().hex[:10]
        if self.redis is not None:
            try:
                await self.redis.setex(f"sigpro:{token}", 86400, text)
            except Exception:
                logger.exception("Failed to cache signal pro text")
        self._signal_pro_cache[token] = text
        return token

    async def _load_signal_pro(self, token: str) -> str:
        if self.redis is not None:
            try:
                raw = await self.redis.get(f"sigpro:{token}")
                if raw:
                    return raw.decode() if isinstance(raw, bytes) else str(raw)
            except Exception:
                logger.exception("Failed to load signal pro text")
        return self._signal_pro_cache.get(token, "")

    async def dispatch_target_notification(self, text: str) -> None:
        if self.application is None or self._bot_notifications_blocked():
            return
        await self._send_to_chat(
            self.config.notification_chat_id,
            text,
            None,
            is_priority=True,
        )

    async def _dispatch_signal_pro_analysis(
        self,
        signal: Signal,
        ta_result: Any,
        png: bytes | None,
        *,
        readiness: tuple[bool, str] | None,
        pro_text: str,
    ) -> None:
        settings = self.settings_manager.settings
        chat_id = self.config.telegram_analysis_chat_id
        if chat_id is None or not settings.analysis_enabled:
            return
        if not settings.signal_pro_to_analysis_chat:
            return
        sym_key = signal.symbol.upper()
        cd = max(120, int(settings.signal_cooldown_seconds))
        now = time.time()
        if now - self._last_signal_analysis_time.get(sym_key, 0.0) < cd:
            return
        body = pro_text or build_pro_detail_html(
            signal, ta_result, readiness=readiness,
        )
        caption = f"🧠 <b>Разбор сигнала</b> · <b>{sym_key}</b>\n\n{body}"
        if len(caption) > 1020:
            caption = caption[:1017] + "…"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=signal.link)],
        ])
        sent = False
        if png:
            sent = await self._send_chart(chat_id, png, caption, is_priority=False, keyboard=keyboard)
        if not sent:
            sent = await self._send_to_chat(chat_id, caption, keyboard, is_priority=False)
        if sent:
            self._last_signal_analysis_time[sym_key] = now
            logger.info("Signal pro analysis %s %s → chat %s", signal.exchange, sym_key, chat_id)

    def _get_symbol_dispatch_lock(self, symbol: str) -> asyncio.Lock:
        key = symbol.upper()
        lock = self._symbol_dispatch_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._symbol_dispatch_locks[key] = lock
        return lock

    async def _reserve_symbol_cooldown(
        self,
        symbol: str,
        cooldown: int,
        symbol_key: str,
    ) -> None:
        now = time.time()
        self._last_symbol_signal_time[symbol.upper()] = now
        if self.redis is not None:
            try:
                await self.redis.set(
                    symbol_key,
                    str(now),
                    ex=int(cooldown) + 5,
                )
            except Exception:
                logger.exception("Failed to reserve last_signal in Redis")

    async def dispatch_signal(self, signal: Signal, *, skip_dedupe: bool = False) -> None:
        if self.application is None:
            return
        if not skip_dedupe and self._bot_notifications_blocked():
            return
        if not skip_dedupe and not self.settings_manager.settings.signals_enabled:
            return

        async with self._get_symbol_dispatch_lock(signal.symbol):
            await self._dispatch_signal_locked(signal, skip_dedupe=skip_dedupe)

    async def _dispatch_signal_locked(self, signal: Signal, *, skip_dedupe: bool = False) -> None:
        priority_max = self.settings_manager.settings.priority_score_max
        prob = float(signal.details.get("probability_percent", 0) or 0)
        is_vertical = signal.signal_type in {"vertical_pump", "vertical_dump"}
        is_impulse = signal.signal_type in {"impulse_pump", "impulse_dump"}
        is_trend = signal.signal_type in {"trend_pump", "trend_dump"}
        is_reversal = signal.signal_type in {"reversal_pump", "reversal_dump"}
        is_liq_cascade = signal.signal_type in {"liq_cascade_pump", "liq_cascade_dump"}
        is_priority = (
            is_vertical
            or is_impulse
            or is_trend
            or is_reversal
            or is_liq_cascade
            or prob >= 75
            or signal.signal_score <= priority_max
            or signal.signal_type in {"mega_pump", "mega_dump", "short_squeeze"}
        )
        if is_vertical or is_impulse or is_trend:
            message = self._format_vertical_breakout_message(
                signal, compact=self.settings_manager.settings.signal_message_compact,
            )
        else:
            message = self._format_signal_message(
                signal,
                is_priority=is_priority,
                compact=self.settings_manager.settings.signal_message_compact,
            )
        keyboard = self._signal_keyboard(signal)
        notify_chat_id = self.config.notification_chat_id

        cooldown = _signal_cooldown_seconds(signal, self.settings_manager.settings)
        symbol_key = f"last_signal:sym:{signal.symbol.upper()}"
        should_send = True
        if not skip_dedupe:
            now = time.time()
            last_local = self._last_symbol_signal_time.get(signal.symbol.upper(), 0.0)
            if now - last_local < cooldown:
                should_send = False
            elif self.redis is not None:
                try:
                    last = await self.redis.get(symbol_key)
                    if last is not None and now - float(last) < cooldown:
                        should_send = False
                except Exception:
                    should_send = True

        if not should_send:
            logger.debug(
                "Skip duplicate %s (cooldown %.0fs)",
                signal.symbol,
                cooldown,
            )
            return

        if not skip_dedupe:
            await self._reserve_symbol_cooldown(signal.symbol, cooldown, symbol_key)

        if (
            settings := self.settings_manager.settings
        ).probability_filter_enabled and not skip_dedupe:
            bypass = signal.signal_type in PROBABILITY_BYPASS_TYPES
            min_prob = settings.min_probability_percent
            if bypass and getattr(settings, "probability_bypass_weaken", True):
                floor_prob = max(52.0, min_prob - 8.0)
                if prob < floor_prob:
                    logger.info(
                        "Telegram skip %s %s: probability %.0f%% < %.0f%% (bypass floor)",
                        signal.exchange,
                        signal.symbol,
                        prob,
                        floor_prob,
                    )
                    return
            elif not bypass and prob < min_prob:
                logger.info(
                    "Telegram skip %s %s: probability %.0f%% < %.0f%%",
                    signal.exchange,
                    signal.symbol,
                    prob,
                    min_prob,
                )
                return

        sent_any = False
        ta_result = None
        readiness: tuple[bool, str] | None = None
        png: bytes | None = None
        pro_text = ""
        need_ta = (
            settings.signal_chart_enabled
            or settings.signal_playbook_enabled
            or (
                not skip_dedupe
                and (settings.actionable_signals_only or settings.actionable_show_readiness_badge)
            )
        )
        if need_ta:
            ms = signal.details.get("market_structure")
            warning = ""
            if isinstance(ms, dict):
                warning = str(ms.get("structure_warning", ""))
            oi_bars = None
            liq_context = None
            if self.scanner is not None:
                try:
                    oi_bars = self.scanner.get_five_min_oi_bars(signal.exchange, signal.symbol)
                except Exception:
                    oi_bars = None
                try:
                    ex = signal.exchange.lower()
                    stats_5 = self.scanner._get_liquidation_stats(ex, signal.symbol, 5)
                    stats_15 = self.scanner._get_liquidation_stats(ex, signal.symbol, 15)
                    liq_context = merge_liq_stats_dict(
                        stats_5.to_dict() if stats_5 else None,
                        stats_15.to_dict() if stats_15 else None,
                    )
                except Exception:
                    liq_context = None
            png = None
            chart_source = ""
            chart_fail = ""
            want_chart = settings.signal_chart_enabled or settings.signal_playbook_enabled
            if want_chart:
                try:
                    png, chart_source, ta_result, chart_fail = await asyncio.wait_for(
                        get_signal_chart_png(
                            signal.exchange,
                            signal.symbol,
                            chart_source=settings.signal_chart_source,
                            chart_hours=settings.signal_chart_hours,
                            chart_interval_minutes=settings.signal_chart_interval_minutes,
                            side=signal.side,
                            structure_warning=warning,
                            probability_percent=float(
                                signal.details.get("probability_percent", 0) or 0
                            ),
                            coinglass_url=signal.link,
                            oi_bars=oi_bars,
                            liq_context=liq_context,
                        ),
                        timeout=35.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Chart capture timeout for %s", signal.symbol)
                    chart_fail = "общий timeout 35с"
                except Exception:
                    logger.exception("Chart capture failed for %s", signal.symbol)
                    chart_fail = "исключение при построении"
            else:
                try:
                    _, ta_result = await asyncio.wait_for(
                        render_annotated_chart(
                            signal.symbol,
                            side=signal.side,
                            hours=settings.signal_chart_hours,
                            interval_minutes=settings.signal_chart_interval_minutes,
                            oi_bars=oi_bars,
                            liq_context=liq_context,
                            neutral=True,
                            chart_source=settings.signal_chart_source,
                            exchange=signal.exchange.lower(),
                        ),
                        timeout=35.0,
                    )
                except Exception:
                    logger.exception("TA-only fetch failed for %s", signal.symbol)

            if ta_result is not None:
                if (
                    settings.signal_cvd_gate_enabled
                    and signal.details.get("cvd_ratio") is None
                    and "bybit" in signal.exchange.lower()
                ):
                    try:
                        lookback = float(settings.signal_cvd_lookback_minutes)
                        cvd_snap = await get_taker_cvd_cache().get_cvd(
                            signal.symbol, lookback_minutes=lookback,
                        )
                        if cvd_snap is not None:
                            attach_cvd_to_signal_details(signal.details, cvd_snap)
                    except Exception:
                        logger.debug("CVD fetch failed for %s", signal.symbol)

                readiness = self._eval_signal_readiness(ta_result, signal, for_filter=False)

                outcome_stats = None
                if (
                    settings.signal_outcome_feedback_enabled
                    and self.outcome_tracker is not None
                ):
                    try:
                        outcome_stats = await self.outcome_tracker.fade_type_stats(
                            signal.signal_type,
                        )
                    except Exception:
                        outcome_stats = None

                quality = assess_signal_quality(
                    signal,
                    ta=ta_result,
                    btc_change_pct=self.scanner.get_btc_change_percent(5)
                    if self.scanner is not None else None,
                    settings=settings,
                    readiness=readiness,
                    outcome_stats=outcome_stats,
                )
                signal.details["quality_tier"] = quality.tier
                signal.details["quality_block"] = quality.block_reason

                if quality.tier == "skip" and not skip_dedupe:
                    logger.info(
                        "Telegram skip %s %s: quality — %s",
                        signal.exchange, signal.symbol, quality.block_reason,
                    )
                    return

                cvd_ratio = quality.cvd_ratio
                if settings.signal_skip_noise and not skip_dedupe:
                    skip, noise_reason = should_skip_noise_signal(
                        ta_result,
                        signal.side,
                        signal.signal_score,
                        signal_type=signal.signal_type,
                        price_change_percent=signal.price_change_percent,
                        cvd_ratio=cvd_ratio,
                        cvd_short_max=settings.signal_cvd_short_max_ratio,
                        cvd_long_min=settings.signal_cvd_long_min_ratio,
                    )
                    if skip:
                        logger.info(
                            "Telegram skip %s %s: noise — %s",
                            signal.exchange, signal.symbol, noise_reason,
                        )
                        return

                if settings.actionable_signals_only and not skip_dedupe:
                    if quality.tier == "watch" and not settings.signal_watch_mode_enabled:
                        logger.info(
                            "Telegram skip %s %s: watch-only mode off — %s",
                            signal.exchange, signal.symbol, quality.block_reason,
                        )
                        return
                    if quality.tier != "entry" and not settings.signal_watch_mode_enabled:
                        ready, reason = self._eval_signal_readiness(
                            ta_result, signal, for_filter=True,
                        )
                        if not ready:
                            logger.info(
                                "Telegram skip %s %s: not actionable — %s",
                                signal.exchange, signal.symbol, reason,
                            )
                            return
            else:
                quality = None
                if settings.actionable_signals_only and not skip_dedupe:
                    logger.info(
                        "Telegram skip %s %s: actionable filter needs TA",
                        signal.exchange,
                        signal.symbol,
                    )
                    return

            quality_html = format_quality_warnings_html(quality) if quality else ""
            hot_quality_html = format_quality_hot_html(quality) if quality else ""
            tier_prefix = ""
            if quality and quality.tier == "entry":
                tier_prefix = "🎯 <b>ENTRY</b> · "
            elif quality and quality.tier == "watch":
                tier_prefix = "👀 <b>WATCH</b> · "
            signal_header = f"{tier_prefix}{message}"

            ta_caption = ""
            pro_text = ""
            pro_token = ""
            if ta_result is not None and settings.signal_playbook_enabled:
                chart_caption = build_hot_caption(
                    signal,
                    ta_result,
                    header=signal_header,
                    readiness=readiness,
                    quality_html=hot_quality_html,
                    quality_tier=quality.tier if quality else None,
                )
                pro_text = build_pro_detail_html(
                    signal, ta_result, readiness=readiness, quality_html=quality_html,
                )
                pro_token = await self._store_signal_pro(pro_text)
                keyboard = self._signal_keyboard(signal, pro_token)
            else:
                if ta_result is not None:
                    ta_caption = ta_signal_caption_html(
                        ta_result,
                        signal_side=signal.side,
                        readiness=readiness,
                        show_readiness_badge=settings.actionable_show_readiness_badge,
                        compact=settings.signal_ta_compact,
                        signal_type=signal.signal_type,
                    )
                chart_caption = f"{signal_header}\n\n{ta_caption}" if ta_caption else signal_header
            if png:
                sent_any = await self._send_chart(
                    notify_chat_id, png, chart_caption, is_priority=is_priority, keyboard=keyboard,
                )
            elif chart_fail:
                logger.warning(
                    "Signal %s %s: chart skipped (%s), source=%s enabled=%s",
                    signal.exchange,
                    signal.symbol,
                    chart_fail,
                    settings.signal_chart_source,
                    want_chart,
                )
                if chart_caption:
                    sent_any = await self._send_to_chat(
                        notify_chat_id, chart_caption, keyboard, is_priority,
                    )
            elif ta_caption and not want_chart:
                sent_any = await self._send_to_chat(
                    notify_chat_id, chart_caption, keyboard, is_priority,
                )

        if not sent_any:
            fallback = chart_caption if need_ta and chart_caption else message
            sent_any = await self._send_to_chat(notify_chat_id, fallback, keyboard, is_priority)

        if not sent_any:
            return

        if (
            sent_any
            and ta_result is not None
            and settings.signal_pro_to_analysis_chat
            and not skip_dedupe
        ):
            try:
                await self._dispatch_signal_pro_analysis(
                    signal,
                    ta_result,
                    png,
                    readiness=readiness,
                    pro_text=pro_text,
                )
            except Exception:
                logger.exception("Signal pro analysis failed for %s", signal.symbol)

        if (
            sent_any
            and ta_result is not None
            and settings.target_watcher_enabled
            and self.target_watcher is not None
            and not skip_dedupe
            and signal.details.get("quality_tier") == "entry"
        ):
            try:
                pb = resolve_trade_playbook(signal, ta_result)
                if pb and pb.target_prices:
                    await self.target_watcher.schedule(
                        exchange=signal.exchange,
                        symbol=signal.symbol,
                        playbook=pb,
                        signal_type=signal.signal_type,
                        entry_price=signal.current_price,
                    )
            except Exception:
                logger.exception("TargetWatcher schedule failed for %s", signal.symbol)

        if sent_any and self.outcome_tracker is not None and self.settings_manager.settings.outcome_tracking_enabled:
            try:
                await self.outcome_tracker.schedule(signal)
            except Exception:
                logger.exception("Outcome schedule failed")

        if sent_any and not skip_dedupe:
            now = time.time()
            self._last_symbol_signal_time[signal.symbol.upper()] = now
            if self.redis is not None:
                try:
                    await self.redis.set(
                        symbol_key,
                        str(now),
                        ex=int(cooldown) + 5,
                    )
                except Exception:
                    logger.exception("Failed to update last_signal in Redis")

        if (
            sent_any
            and ta_result is not None
            and settings.scenario_watch_enabled
            and not settings.bot_paused
            and not skip_dedupe
        ):
            try:
                if signal.details.get("quality_tier") == "watch":
                    self.scenario_watcher.try_enroll_quality_watch(
                        signal, ta_result, settings,
                        quality_tier="watch",
                    )
                else:
                    self.scenario_watcher.try_enroll(signal, ta_result, settings)
            except Exception:
                logger.exception("Scenario watch enroll failed for %s", signal.symbol)

        if self.redis is not None:
            try:
                raw = json.dumps(asdict(signal), ensure_ascii=False)
                await self.redis.rpush("signals", raw)
                await self.redis.ltrim("signals", -500, -1)
            except Exception:
                logger.exception("Failed to persist signal to Redis")

    async def dispatch_liquidation_alert(
        self,
        event: LiquidationAlertEvent,
        event_count: int,
        total_usd: float,
    ) -> None:
        if self.application is None:
            return
        settings = self.settings_manager.settings
        if self._bot_notifications_blocked():
            return
        if not settings.liquidation_alerts_enabled:
            return

        message = format_liquidation_alert(
            event,
            event_count,
            total_usd,
            show_reversal_hint=settings.liquidation_show_reversal_hint,
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=coinglass_url(event.symbol, event.exchange))],
        ])
        notify_chat_id = self.config.notification_chat_id
        sent = await self._send_to_chat(notify_chat_id, message, keyboard, is_priority=True)
        if sent:
            logger.info(
                "Liquidation alert %s %s $%.0f (%d events)",
                event.exchange,
                event.symbol,
                total_usd,
                event_count,
            )

    async def dispatch_anomaly(self, event: AnomalyEvent) -> bool:
        if self.application is None:
            return False
        settings = self.settings_manager.settings
        if self._bot_notifications_blocked():
            return False
        if not settings.anomaly_enabled:
            return False
        chat_id = self.config.anomaly_chat_id
        if chat_id is None:
            return False

        message = format_anomaly_alert(event)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=coinglass_url(event.symbol, event.exchange))],
        ])
        sent = await self._send_to_chat(chat_id, message, keyboard, is_priority=True)
        if sent:
            logger.info(
                "Anomaly %s %s %s",
                event.exchange,
                event.symbol,
                event.anomaly_type,
            )
        return sent

    async def dispatch_trend_risk(
        self,
        risk: TrendExhaustionRisk,
        exchange: str,
        symbol: str,
    ) -> None:
        if self.application is None or self._bot_notifications_blocked():
            return
        settings = self.settings_manager.settings
        if not settings.signals_enabled:
            return
        if not getattr(settings, "trend_exhaustion_risk_enabled", True):
            return

        sym = symbol.upper()
        link = coinglass_url(sym, exchange)
        if risk.kind == "dump_risk":
            title = "⚠️ WATCH · риск слива"
            hint = "Не лонг у хая. Ждите trend_dump или пробой вниз."
        else:
            title = "⚠️ WATCH · риск отскока"
            hint = "Не шорт у дна. Ждите trend_pump или пробой вверх."

        score = int(risk.meta.get("risk_score", 0))
        message = (
            f"<b>{title}</b>\n"
            f"<a href=\"{link}\"><b>{sym}</b></a> · {exchange}\n"
            f"📊 {html.escape(risk.detail)}\n"
            f"🔢 факторов: {score}\n"
            f"💡 {hint}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=link)],
        ])
        sent = await self._send_to_chat(
            self.config.notification_chat_id, message, keyboard, is_priority=False,
        )
        if sent:
            logger.info("Trend risk %s %s %s", exchange, sym, risk.kind)

    async def get_analysis_adaptive_weights(self) -> dict[str, float] | None:
        if self.analysis_outcome_tracker is None:
            return None
        return await self.analysis_outcome_tracker.get_adaptive_weights()

    async def dispatch_liquidation_analysis(self, result: LiquidationAnalysisResult) -> None:
        if self.application is None:
            return
        settings = self.settings_manager.settings
        chat_id = self.config.telegram_analysis_chat_id
        if chat_id is None:
            logger.warning("Analysis dispatch skipped: TELEGRAM_ANALYSIS_CHAT_ID not set")
            return
        if self._bot_notifications_blocked():
            return
        if not settings.analysis_enabled:
            return

        message = format_liquidation_analysis(result)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 CoinGlass", url=coinglass_url(result.symbol, result.exchange)),
            ],
        ])
        sent = await self._send_to_chat(chat_id, message, keyboard, is_priority=False)
        if not sent:
            logger.warning(
                "Analysis NOT delivered to chat %s for %s (send failed or rate limit)",
                chat_id,
                result.symbol,
            )
            return
        if settings.analysis_chart_enabled:
            oi_bars = None
            liq_context = None
            if self.scanner is not None:
                try:
                    oi_bars = self.scanner.get_five_min_oi_bars(result.exchange, result.symbol)
                except Exception:
                    oi_bars = None
                try:
                    stats_5 = self.scanner._get_liquidation_stats(result.exchange, result.symbol, 5)
                    stats_15 = self.scanner._get_liquidation_stats(result.exchange, result.symbol, 15)
                    liq_context = merge_liq_stats_dict(
                        stats_5.to_dict() if stats_5 else None,
                        stats_15.to_dict() if stats_15 else None,
                    )
                except Exception:
                    liq_context = None
            png = None
            ta_result = None
            chart_src = getattr(settings, "analysis_chart_source", "annotated")
            try:
                if chart_src == "annotated":
                    png, ta_result = await asyncio.wait_for(
                        render_analysis_chart(
                            result.symbol,
                            direction=result.direction,
                            hours=settings.signal_chart_hours,
                            interval_minutes=settings.signal_chart_interval_minutes,
                            invalidation_price=result.invalidation_price,
                            oi_bars=oi_bars,
                            liq_context=liq_context,
                            exchange=result.exchange,
                        ),
                        timeout=20.0,
                    )
                else:
                    png = await asyncio.wait_for(
                        chart_capture_service.capture_tradingview(
                            result.exchange,
                            result.symbol,
                            interval_minutes=settings.analysis_chart_interval_minutes,
                        ),
                        timeout=20.0,
                    )
            except asyncio.TimeoutError:
                logger.warning("Analysis chart timeout for %s", result.symbol)
                png = None
            except Exception:
                logger.exception("Analysis chart capture failed for %s", result.symbol)
                png = None
            if png is None and chart_src != "annotated":
                try:
                    png, ta_result = await render_analysis_chart(
                        result.symbol,
                        direction=result.direction,
                        hours=settings.signal_chart_hours,
                        interval_minutes=settings.signal_chart_interval_minutes,
                        invalidation_price=result.invalidation_price,
                        oi_bars=oi_bars,
                        liq_context=liq_context,
                        exchange=result.exchange,
                    )
                except Exception:
                    logger.exception("Analysis annotated fallback failed for %s", result.symbol)
            if png:
                caption = (
                    f"📊 #{base_ticker(result.symbol)} · {result.direction_label} "
                    f"· {result.confidence:.0f}%"
                )
                if ta_result is not None:
                    ta_caption = ta_analysis_chart_caption_html(
                        ta_result,
                        analysis_direction=result.direction,
                        post_dump_late=result.post_dump_late,
                        liq_cascade_note=result.liq_cascade_note,
                    )
                    caption = f"{caption}\n\n{ta_caption}"
                await self._send_chart(
                    chat_id, png, caption, is_priority=False,
                )
        if sent:
            logger.info(
                "Analysis alert %s %s conf=%.0f%% %s",
                result.exchange,
                result.symbol,
                result.confidence,
                result.direction,
            )
            if (
                self.analysis_outcome_tracker is not None
                and settings.analysis_outcome_tracking_enabled
            ):
                try:
                    await self.analysis_outcome_tracker.schedule(result)
                except Exception:
                    logger.exception("Analysis outcome schedule failed")

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            self._build_settings_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._reply_keyboard(),
        )

    async def on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            self._build_help_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._reply_keyboard(),
        )

    async def on_set(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        args = context.args or []
        result = parse_set_command(args)
        if not result.ok and not result.updates:
            await update.message.reply_text(result.message, parse_mode=ParseMode.HTML)
            return
        if result.updates:
            self.settings_manager.update(**result.updates)
        await update.message.reply_text(
            result.message + "\n\n" + self._build_settings_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._settings_keyboard(),
        )

    async def on_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text("Отправляю тестовые сигналы LONG и SHORT…")
        for signal in build_test_signals():
            await self.dispatch_signal(signal, skip_dedupe=True)
        await update.message.reply_text(
            "✅ Тест готов: 2 сообщения (🟢 LONG Bybit + 🔴 SHORT Binance).",
            reply_markup=self._reply_keyboard(),
        )

    async def on_test_analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        if not self.config.analysis_chat_configured:
            await update.message.reply_text(
                "❌ <b>TELEGRAM_ANALYSIS_CHAT_ID</b> не задан в <code>.env</code>.\n\n"
                "1) Создайте отдельный чат/канал\n"
                "2) Добавьте бота\n"
                "3) Узнайте id через getUpdates (как для ALERT_CHAT)\n"
                "4) Пропишите в .env: <code>TELEGRAM_ANALYSIS_CHAT_ID=-100...</code>\n"
                "5) Перезапустите бота",
                parse_mode=ParseMode.HTML,
            )
            return
        now = time.time()
        sample = LiquidationAnalysisResult(
            symbol="LABUSDT",
            exchange="Bybit",
            cluster_side="short_liq",
            cluster_usd=85_000.0,
            cluster_events=22,
            cluster_price=14.2,
            cluster_time=now - 90,
            current_price=13.8,
            price_change_since_cluster_pct=-2.8,
            oi_change_since_cluster_pct=1.8,
            direction="wait",
            direction_label="⏸ выжидание",
            confidence=68.0,
            window_min=30,
            window_max=120,
            invalidation_price=14.5,
            invalidation_label="пробой $14.5 вверх → сценарий отменён",
            factors=[
                AnalysisFactor("trend", "Тренд + liq", 0.88, 0.20, "тренд вверх + смыв шортов"),
                AnalysisFactor("cvd", "CVD (объём)", 0.55, 0.16, "CVD≈ баланс"),
                AnalysisFactor("oi_narrative", "Open Interest", 0.72, 0.12, "OI растёт на коррекции"),
            ],
            continuation_risk=True,
            trend_label="тренд вверх +8.2% (1ч) / +12.1% (4ч)",
            scenario_text="шорты смыли на тренде → коррекция → жди подтверждения направления",
            is_correction=True,
        )
        await self.dispatch_liquidation_analysis(sample)
        await update.message.reply_text(
            "✅ Тестовый разбор отправлен в аналитический чат.",
            reply_markup=self._reply_keyboard(),
        )

    async def on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            await self._build_settings_panel_text_async(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._settings_keyboard(),
        )

    async def on_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        if self.redis is None:
            await update.message.reply_text("История недоступна: Redis не настроен.")
            return
        try:
            args = context.args or []
            limit = int(args[0]) if args else 20
            raw_list = await self.redis.lrange("signals", -limit, -1)
            if not raw_list:
                await update.message.reply_text("История пуста.")
                return
            parts = []
            for raw in reversed(raw_list):
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                side = obj.get("side", "long")
                mark = "🟢" if side == "long" else "🔴"
                parts.append(
                    f"{mark} {obj.get('exchange')} <a href=\"{obj.get('link')}\">{obj.get('symbol')}</a> | "
                    f"OI {obj.get('oi_change_percent')}% | "
                    f"Цена {obj.get('price_change_percent')}% | "
                    f"Сигнал {obj.get('signal_score')}/10"
                )
            await update.message.reply_text(
                "\n\n".join(parts),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Error reading history")
            await update.message.reply_text("Ошибка при получении истории.")

    async def on_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            self._build_settings_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._settings_keyboard(),
        )

    async def on_ta_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._can_use_manual_ta(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            manual_ta_help_text(),
            parse_mode=ParseMode.HTML,
        )

    def _is_manual_ta_chat(self, update: Update) -> bool:
        chat = update.effective_chat
        if chat is None or not self.config.manual_ta_chat_configured:
            return False
        return chat.id == self.config.telegram_manual_ta_chat_id

    def _can_use_manual_ta(self, update: Update) -> bool:
        return self._is_manual_ta_chat(update) or self._is_admin(update)

    def _manual_ta_tf_keyboard(self, symbol: str, *, wizard: bool = False) -> InlineKeyboardMarkup:
        builder = build_mtw_callback if wizard else build_mta_callback
        buttons = [
            InlineKeyboardButton(f"{tf}m", callback_data=builder(symbol, tf))
            for tf in MANUAL_TA_TIMEFRAMES
        ]
        rows: list[list[InlineKeyboardButton]] = [buttons]
        if wizard:
            rows.append([InlineKeyboardButton("❌ Отмена", callback_data=MTW_CANCEL_CALLBACK)])
        else:
            rows.append([InlineKeyboardButton("📊 CoinGlass", url=coinglass_url(symbol, "bybit"))])
        return InlineKeyboardMarkup(rows)

    def _manual_ta_chart_source_keyboard(
        self,
        symbol: str,
        interval_minutes: int,
        *,
        wizard: bool = False,
    ) -> InlineKeyboardMarkup:
        default_source = self.settings_manager.settings.manual_ta_chart_source
        if default_source not in MANUAL_TA_CHART_SOURCES:
            default_source = "tv_annotated"
        builder = build_mtcw_callback if wizard else build_mtc_callback
        rows: list[list[InlineKeyboardButton]] = [[
            InlineKeyboardButton(
                ("✅ " if default_source == "tv_annotated" else "") + "TV + TA (overlay)",
                callback_data=builder(symbol, interval_minutes, "tv_annotated"),
            ),
            InlineKeyboardButton(
                ("✅ " if default_source == "annotated" else "") + "Полный TA (annotated)",
                callback_data=builder(symbol, interval_minutes, "annotated"),
            ),
        ], [
            InlineKeyboardButton(
                ("✅ " if default_source == "annotated_pro" else "") + "PRO annotated",
                callback_data=builder(symbol, interval_minutes, "annotated_pro"),
            ),
        ]]
        if wizard:
            rows.append([InlineKeyboardButton("❌ Отмена", callback_data=MTW_CANCEL_CALLBACK)])
        else:
            rows.append([InlineKeyboardButton("📊 CoinGlass", url=coinglass_url(symbol, "bybit"))])
        return InlineKeyboardMarkup(rows)

    def _manual_ta_result_keyboard(
        self,
        symbol: str,
        interval_minutes: int,
        ta_result: Any | None,
        *,
        chat_id: int | None = None,
    ) -> InlineKeyboardMarkup:
        base = self._manual_ta_tf_keyboard(symbol, wizard=False).inline_keyboard
        rows = [list(row) for row in base]
        side = "long"
        if ta_result is not None:
            verdict = getattr(ta_result, "verdict", "")
            priority = getattr(ta_result, "action_priority", "")
            if verdict == "SHORT" or priority == "short":
                side = "short"

        if chat_id is not None and self._is_manual_ta_muted(chat_id, symbol):
            rows.append([
                InlineKeyboardButton(
                    f"🔔 Включить {symbol}",
                    callback_data=build_mta_mute_callback(symbol, interval_minutes, "unmute"),
                ),
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    "🔻 Мой SHORT",
                    callback_data=build_mta_intent_callback(symbol, interval_minutes, "short"),
                ),
                InlineKeyboardButton(
                    "🔺 Мой LONG",
                    callback_data=build_mta_intent_callback(symbol, interval_minutes, "long"),
                ),
            ])
            rows.append([
                InlineKeyboardButton(
                    "🔔 Пробой",
                    callback_data=build_mta_alert_callback(symbol, interval_minutes, side, "breakout"),
                ),
                InlineKeyboardButton(
                    "🔁 Ретест",
                    callback_data=build_mta_alert_callback(symbol, interval_minutes, side, "retest"),
                ),
                InlineKeyboardButton(
                    "📈 Объём",
                    callback_data=build_mta_alert_callback(symbol, interval_minutes, side, "volume"),
                ),
            ])
            active = self._active_manual_ta_alerts_count(chat_id, symbol) if chat_id else 0
            stop_label = f"⏹ Стоп ({active})" if active else "⏹ Стоп алерты"
            rows.append([
                InlineKeyboardButton(
                    stop_label,
                    callback_data=build_mta_mute_callback(symbol, interval_minutes, "stop"),
                ),
                InlineKeyboardButton(
                    "🔕 Монета OFF",
                    callback_data=build_mta_mute_callback(symbol, interval_minutes, "mute"),
                ),
            ])
        return InlineKeyboardMarkup(rows)

    def _is_manual_ta_muted(self, chat_id: int, symbol: str) -> bool:
        key = (chat_id, symbol.upper())
        expires_at = self._manual_ta_muted.get(key)
        if expires_at is None:
            return False
        if time.time() >= expires_at:
            self._manual_ta_muted.pop(key, None)
            return False
        return True

    def _mute_manual_ta_symbol(self, chat_id: int, symbol: str, *, hours: float = 24.0) -> None:
        self._manual_ta_muted[(chat_id, symbol.upper())] = time.time() + hours * 3600
        self._stop_manual_ta_alerts_for_symbol(chat_id, symbol)

    def _unmute_manual_ta_symbol(self, chat_id: int, symbol: str) -> None:
        self._manual_ta_muted.pop((chat_id, symbol.upper()), None)

    def _stop_manual_ta_alerts_for_symbol(self, chat_id: int, symbol: str) -> int:
        sym = symbol.upper()
        removed = 0
        for key in list(self._manual_ta_alerts):
            if key[0] == chat_id and key[1].upper() == sym:
                self._manual_ta_alerts.pop(key, None)
                removed += 1
        return removed

    def _active_manual_ta_alerts_count(self, chat_id: int | None, symbol: str) -> int:
        if chat_id is None:
            return 0
        sym = symbol.upper()
        return sum(
            1 for key in self._manual_ta_alerts
            if key[0] == chat_id and key[1].upper() == sym
        )

    def _manual_ta_alert_fired_keyboard(self, symbol: str, interval: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔕 Не слать по монете",
                    callback_data=build_mta_mute_callback(symbol, interval, "mute"),
                ),
                InlineKeyboardButton(
                    "⏹ Стоп алерты",
                    callback_data=build_mta_mute_callback(symbol, interval, "stop"),
                ),
            ],
        ])

    async def _manual_ta_alert_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(12.0)
                if not self._manual_ta_alerts or self.application is None:
                    continue
                if self._bot_notifications_blocked():
                    continue
                if not self.settings_manager.settings.manual_ta_alerts_enabled:
                    continue
                now = time.time()
                for key, watcher in list(self._manual_ta_alerts.items()):
                    chat_id, symbol, interval, mode = key
                    if self._is_manual_ta_muted(chat_id, symbol):
                        self._stop_manual_ta_alerts_for_symbol(chat_id, symbol)
                        continue
                    expires_at = float(watcher.get("expires_at", 0))
                    if expires_at and now >= expires_at:
                        self._manual_ta_alerts.pop(key, None)
                        continue
                    trigger = float(watcher.get("trigger", 0) or 0)
                    side = str(watcher.get("side", "short"))
                    if trigger <= 0:
                        self._manual_ta_alerts.pop(key, None)
                        continue
                    bars = await self._manual_alert_kline_cache.get_klines(symbol, limit=24, interval_minutes=interval)
                    if not bars:
                        continue
                    price = bars[-1].close
                    fired = False
                    mode_title = "пробой"
                    if mode == "breakout":
                        fired = price <= trigger if side == "short" else price >= trigger
                        mode_title = "пробой"
                    elif mode == "retest":
                        was_broken = bool(watcher.get("was_broken", False))
                        tol = max(trigger * 0.004, 1e-9)
                        near = abs(price - trigger) <= tol
                        if not was_broken:
                            was_broken = price <= trigger if side == "short" else price >= trigger
                            watcher["was_broken"] = was_broken
                        else:
                            fired = near
                        mode_title = "ретест"
                    elif mode == "volume":
                        if len(bars) >= 12:
                            recent = bars[-1]
                            base = bars[-11:-1]
                            avg_vol = sum(b.volume for b in base) / max(len(base), 1)
                            if avg_vol > 0:
                                vol_mult = recent.volume / avg_vol
                                dir_ok = (recent.close < recent.open) if side == "short" else (recent.close > recent.open)
                                fired = vol_mult >= 2.2 and dir_ok
                        mode_title = "ускорение объёма"
                    if not fired:
                        continue
                    emoji = "🔻" if side == "short" else "🔺"
                    await self.application.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"{emoji} <b>Алерт ручного TA</b> · {mode_title}\n"
                            f"{symbol} {interval}m: цена <b>{price:.6g}</b>, уровень <b>{trigger:.6g}</b>.\n"
                            f"Сигнал: {'вниз' if side == 'short' else 'вверх'} · проверьте свечу/контекст."
                        ),
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._manual_ta_alert_fired_keyboard(symbol, interval),
                    )
                    self._manual_ta_alerts.pop(key, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Manual TA alert loop error")

    async def _scenario_watch_loop(self) -> None:
        while True:
            try:
                settings = self.settings_manager.settings
                interval = float(getattr(settings, "scenario_watch_tick_seconds", 12.0))
                await asyncio.sleep(interval)
                if (
                    not settings.scenario_watch_enabled
                    or settings.bot_paused
                    or self.scanner is None
                    or self.application is None
                    or self.scenario_watcher.active_count == 0
                ):
                    continue
                updates = self.scenario_watcher.tick(self.scanner, settings)
                for upd in updates:
                    try:
                        await self._dispatch_scenario_update(upd)
                    except Exception:
                        logger.exception(
                            "Scenario update dispatch failed %s %s",
                            upd.watch.exchange,
                            upd.watch.symbol,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scenario watch loop error")

    async def _dispatch_scenario_update(self, upd: ScenarioUpdate) -> None:
        if self.application is None:
            return
        if self._bot_notifications_blocked():
            return
        watch = upd.watch
        settings = self.settings_manager.settings
        notify_chat_id = self.config.notification_chat_id

        ta_fresh = None
        oi_bars = None
        liq_context = None
        if self.scanner is not None:
            try:
                oi_bars = self.scanner.get_five_min_oi_bars(watch.exchange, watch.symbol)
            except Exception:
                oi_bars = None
            try:
                stats = self.scanner._get_liquidation_stats(watch.exchange, watch.symbol, 15)
                if stats is not None:
                    liq_context = stats.to_dict()
            except Exception:
                liq_context = None

        message = format_scenario_update_html(
            symbol=watch.symbol,
            exchange=watch.exchange,
            update_kind=upd.kind,
            price=upd.price,
            move_pct=upd.move_pct,
            reference_price=upd.reference_price,
            correction_target=watch.correction_target,
            breakdown_level=watch.breakdown_level,
            breakout_level=watch.breakout_level,
            ta=ta_fresh,
        )
        if upd.kind in {"entry_short", "entry_long"}:
            label = "SHORT" if upd.kind == "entry_short" else "LONG"
            message = (
                f"🎯 <b>TRIGGER · ENTRY</b> · <b>{watch.symbol}</b> · {label}\n"
                f"{message}"
            )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=watch.coinglass_url)],
        ])

        sent = False
        if settings.scenario_watch_chart_enabled:
            try:
                png, ta_fresh = await asyncio.wait_for(
                    render_signal_chart(
                        watch.symbol,
                        side=watch.side,
                        hours=settings.signal_chart_hours,
                        interval_minutes=settings.signal_chart_interval_minutes,
                        oi_bars=oi_bars,
                        liq_context=liq_context,
                        chart_source=settings.signal_chart_source,
                        exchange=watch.exchange.lower(),
                    ),
                    timeout=35.0,
                )
                if ta_fresh is not None:
                    message = format_scenario_update_html(
                        symbol=watch.symbol,
                        exchange=watch.exchange,
                        update_kind=upd.kind,
                        price=upd.price,
                        move_pct=upd.move_pct,
                        reference_price=upd.reference_price,
                        correction_target=watch.correction_target,
                        breakdown_level=watch.breakdown_level,
                        breakout_level=watch.breakout_level,
                        ta=ta_fresh,
                    )
                    followup = ta_scenario_followup_caption_html(ta_fresh, upd.kind, watch.side)
                    caption = f"{message}\n{followup}" if followup else message
                else:
                    caption = message
                if png:
                    sent = await self._send_chart(
                        notify_chat_id, png, caption, is_priority=True, keyboard=keyboard,
                    )
            except asyncio.TimeoutError:
                logger.warning("Scenario update chart timeout for %s", watch.symbol)
            except Exception:
                logger.exception("Scenario update chart failed for %s", watch.symbol)

        if not sent:
            sent = await self._send_to_chat(notify_chat_id, message, keyboard, is_priority=True)

        if sent:
            logger.info(
                "Scenario update %s %s kind=%s move=%.2f%%",
                watch.exchange,
                watch.symbol,
                upd.kind,
                upd.move_pct,
            )

    def _mta_wizard_state(self, context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
        state = context.user_data.get(MTA_WIZARD_KEY)
        return state if isinstance(state, dict) else None

    def _clear_mta_wizard(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data.pop(MTA_WIZARD_KEY, None)

    def _start_mta_wizard(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data[MTA_WIZARD_KEY] = {
            "symbol": None,
            "interval": None,
            "photo_file_id": None,
        }

    async def _cancel_mta_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if self._mta_wizard_state(context) is None:
            return False
        self._clear_mta_wizard(context)
        if update.message:
            await update.message.reply_text(
                "❌ Ручной анализ отменён.",
                reply_markup=self._reply_keyboard(),
            )
        return True

    async def on_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            return
        if await self._cancel_mta_wizard(update, context):
            return
        if update.message:
            await update.message.reply_text("Нечего отменять.")

    async def _start_manual_ta_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.config.manual_ta_chat_configured:
            if update.message:
                await update.message.reply_text(
                    "⚠️ Чат ручного TA не настроен.\n"
                    "Добавьте <code>TELEGRAM_MANUAL_TA_CHAT_ID</code> в .env и перезапустите бота.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._reply_keyboard(),
                )
            return
        self._start_mta_wizard(context)
        if update.message:
            await update.message.reply_text(
                manual_ta_wizard_start_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._reply_keyboard(),
            )

    async def _prompt_manual_ta_timeframe(
        self,
        update: Update,
        symbol: str,
        *,
        wizard: bool = False,
    ) -> None:
        if update.message is None:
            return
        photo_hint = ""
        if wizard:
            photo_hint = "\n\n<i>Скрин можно прислать до или после выбора TF.</i>"
        await update.message.reply_text(
            f"📐 <b>{symbol}</b>\nВыберите таймфрейм для TA-разметки:{photo_hint}",
            parse_mode=ParseMode.HTML,
            reply_markup=self._manual_ta_tf_keyboard(symbol, wizard=wizard),
        )

    async def _advance_mta_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        state = self._mta_wizard_state(context)
        if state is None or update.message is None:
            return

        symbol = state.get("symbol")
        interval = state.get("interval")
        photo_file_id = state.get("photo_file_id")

        if not symbol:
            if photo_file_id:
                await update.message.reply_text(
                    "✅ Скрин получен.\n"
                    "Теперь отправьте тикер:\n"
                    "<code>GRASS</code> · <code>GRASS 10m</code> · <code>BTCUSDT 15m</code>",
                    parse_mode=ParseMode.HTML,
                )
            return

        if interval in MANUAL_TA_TIMEFRAMES:
            await self._finish_mta_wizard(update, context, symbol, interval)
            return

        hint = "✅ Тикер принят."
        if not photo_file_id:
            hint += "\n📷 Пришлите скрин CoinGlass/TradingView (можно после выбора TF)."
        await update.message.reply_text(hint, parse_mode=ParseMode.HTML)
        await self._prompt_manual_ta_timeframe(update, symbol, wizard=True)

    async def _finish_mta_wizard(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        symbol: str,
        interval_minutes: int,
        *,
        query: CallbackQuery | None = None,
        chart_source: str | None = None,
    ) -> None:
        if chart_source is None:
            text = (
                f"📐 <b>{symbol}</b> · {interval_minutes}m\n"
                "Выберите тип графика:"
            )
            if query:
                await query.answer("Выберите вид графика")
                try:
                    await query.edit_message_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._manual_ta_chart_source_keyboard(
                            symbol,
                            interval_minutes,
                            wizard=True,
                        ),
                    )
                except BadRequest:
                    if query.message:
                        await query.message.reply_text(
                            text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=self._manual_ta_chart_source_keyboard(
                                symbol,
                                interval_minutes,
                                wizard=True,
                            ),
                        )
            elif update.message:
                await update.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._manual_ta_chart_source_keyboard(
                        symbol,
                        interval_minutes,
                        wizard=True,
                    ),
                )
            return

        state = self._mta_wizard_state(context) or {}
        photo_file_id = state.get("photo_file_id")
        self._clear_mta_wizard(context)

        deliver_chat_id = self.config.telegram_manual_ta_chat_id
        notify_chat_id = update.effective_chat.id if update.effective_chat else None
        if deliver_chat_id is None:
            err = "Чат ручного TA не настроен."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return

        await self._process_manual_ta_request(
            update,
            symbol,
            interval_minutes,
            query=query,
            deliver_chat_id=deliver_chat_id,
            notify_chat_id=notify_chat_id,
            photo_file_id=photo_file_id,
            chart_source=chart_source,
        )

    async def _handle_mta_wizard_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if not self._is_admin(update) or update.message is None:
            return False
        state = self._mta_wizard_state(context)
        if state is None:
            return False

        photos = update.message.photo
        if not photos:
            return False
        state["photo_file_id"] = photos[-1].file_id

        symbol, interval = parse_manual_ta_input(update.message.caption or "")
        if symbol:
            state["symbol"] = symbol
        if interval in MANUAL_TA_TIMEFRAMES:
            state["interval"] = interval

        if state.get("symbol") and state.get("interval") in MANUAL_TA_TIMEFRAMES:
            await self._finish_mta_wizard(
                update,
                context,
                state["symbol"],
                state["interval"],
            )
            return True

        await self._advance_mta_wizard(update, context)
        return True

    async def _handle_mta_wizard_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if not self._is_admin(update) or update.message is None:
            return False
        state = self._mta_wizard_state(context)
        if state is None:
            return False

        text = (update.message.text or "").strip()
        if not text:
            return True

        symbol, interval = parse_manual_ta_input(text)
        if not symbol:
            await update.message.reply_text(
                "Не распознал тикер. Пример: <code>GRASSUSDT</code> или <code>GRASS 10m</code>",
                parse_mode=ParseMode.HTML,
            )
            return True

        state["symbol"] = symbol
        if interval in MANUAL_TA_TIMEFRAMES:
            state["interval"] = interval

        if state.get("interval") in MANUAL_TA_TIMEFRAMES:
            await self._finish_mta_wizard(update, context, symbol, state["interval"])
            return True

        await self._advance_mta_wizard(update, context)
        return True

    async def _process_manual_ta_request(
        self,
        update: Update,
        symbol: str,
        interval_minutes: int,
        *,
        query: CallbackQuery | None = None,
        deliver_chat_id: int | None = None,
        notify_chat_id: int | None = None,
        photo_file_id: str | None = None,
        chart_source: str | None = None,
    ) -> None:
        chat = update.effective_chat
        if chat is None:
            return

        target_chat_id = deliver_chat_id if deliver_chat_id is not None else chat.id
        from_wizard = deliver_chat_id is not None and notify_chat_id is not None

        hours = manual_ta_hours(interval_minutes)
        progress = f"⏳ Строю TA: <b>{symbol}</b> · {interval_minutes}m · {hours}ч…"
        if from_wizard:
            progress += "\n<i>Результат уйдёт в чат ручного TA.</i>"

        if query:
            await query.answer(f"⏳ {symbol} · {interval_minutes}m")
            try:
                await query.edit_message_text(progress, parse_mode=ParseMode.HTML)
            except BadRequest:
                pass
        elif update.message:
            await update.message.reply_text(progress, parse_mode=ParseMode.HTML)

        oi_bars = None
        liq_context = None
        if self.scanner is not None:
            if interval_minutes == 5:
                try:
                    oi_bars = self.scanner.get_five_min_oi_bars("bybit", symbol)
                except Exception:
                    oi_bars = None
            try:
                stats_5 = self.scanner._get_liquidation_stats("bybit", symbol, 5)
                stats_15 = self.scanner._get_liquidation_stats("bybit", symbol, 15)
                liq_context = merge_liq_stats_dict(
                    stats_5.to_dict() if stats_5 else None,
                    stats_15.to_dict() if stats_15 else None,
                )
            except Exception:
                liq_context = None

        chart_source = chart_source or self.settings_manager.settings.manual_ta_chart_source

        try:
            png, ta = await asyncio.wait_for(
                render_annotated_chart(
                    symbol,
                    side="long",
                    hours=hours,
                    interval_minutes=interval_minutes,
                    oi_bars=oi_bars,
                    neutral=True,
                    chart_source=chart_source,
                    exchange="bybit",
                    liq_context=liq_context,
                ),
                timeout=50.0,
            )
        except asyncio.TimeoutError:
            err = f"Таймаут загрузки свечей для {symbol} ({interval_minutes}m)."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return
        except Exception:
            logger.exception("Manual TA failed for %s %sm", symbol, interval_minutes)
            err = f"Ошибка построения графика {symbol}."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return

        if not png or ta is None:
            err = f"Нет данных Bybit для {symbol} ({interval_minutes}m)."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return

        if photo_file_id and self.application is not None:
            ref_caption = f"📷 Референс · <b>{symbol}</b> · {interval_minutes}m"
            if from_wizard and update.effective_user:
                ref_caption += f"\nЗапрос: {update.effective_user.mention_html()}"
            try:
                await self.application.bot.send_photo(
                    chat_id=target_chat_id,
                    photo=photo_file_id,
                    caption=ref_caption,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                logger.exception("Failed to forward manual TA reference photo for %s", symbol)

        caption = (
            f"<b>{symbol}</b> · Bybit {interval_minutes}m · {hours}ч\n"
            f"{ta_manual_detailed_html(ta)}"
        )
        try:
            cvd_snap = await get_taker_cvd_cache().get_cvd(
                symbol,
                lookback_minutes=float(
                    self.settings_manager.settings.signal_cvd_lookback_minutes
                ) * 1.5,
            )
            flow_html = format_manual_ta_flow_html(
                ta,
                cvd_snap=cvd_snap,
                cvd_short_max=self.settings_manager.settings.signal_cvd_short_max_ratio,
                cvd_long_min=self.settings_manager.settings.signal_cvd_long_min_ratio,
            )
            if flow_html:
                caption += f"\n\n<b>📊 Поток рынка</b>\n{flow_html}"
        except Exception:
            logger.debug("Manual TA CVD fetch failed for %s", symbol)
        # Доп. глубина для ручного TA: 24-48ч истории по паттерну spike->dump.
        extra_hours = 24 if interval_minutes <= 10 else 48
        per_hour = max(1, 60 // interval_minutes)
        extra_limit = max(48, min(extra_hours * per_hour, 200))
        try:
            extra_bars = await self._manual_alert_kline_cache.get_klines(
                symbol,
                limit=extra_limit,
                interval_minutes=interval_minutes,
            )
            extra_repeat, extra_note = detect_repeat_spike_dump_risk(extra_bars)
            if extra_repeat and extra_note:
                caption += f"\n🧠 <b>Глубокая история {extra_hours}ч:</b> {extra_note}"
        except Exception:
            logger.exception("Failed extended repeat-pattern check for %s", symbol)
        self._manual_ta_last[(target_chat_id, symbol, interval_minutes)] = {
            "verdict": ta.verdict,
            "priority": ta.action_priority,
            "breakout": ta.breakout_level,
            "plan_breakout": ta.breakout_level,
            "breakdown": ta.breakdown_level,
            "price": ta.current_price,
            "updated_at": time.time(),
            "chart_source": chart_source,
        }
        self._manual_ta_last_by_chat[target_chat_id] = {
            "symbol": symbol,
            "interval": interval_minutes,
            "chart_source": chart_source,
        }
        keyboard = self._manual_ta_result_keyboard(
            symbol, interval_minutes, ta, chat_id=target_chat_id,
        )
        await self._send_chart(
            target_chat_id,
            png,
            caption,
            is_priority=False,
            keyboard=keyboard,
        )

        if from_wizard and notify_chat_id is not None and notify_chat_id != target_chat_id:
            if self.application is not None:
                try:
                    await self.application.bot.send_message(
                        chat_id=notify_chat_id,
                        text=(
                            f"✅ <b>{symbol}</b> · {interval_minutes}m отправлен "
                            f"в чат ручного TA."
                        ),
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._reply_keyboard(),
                    )
                except Exception:
                    logger.exception("Failed to notify admin about manual TA delivery")

    async def _process_manual_ta_intent(
        self,
        update: Update,
        symbol: str,
        interval_minutes: int,
        user_side: str,
        *,
        query: CallbackQuery | None = None,
        chart_source: str | None = None,
        deliver_chat_id: int | None = None,
    ) -> None:
        chat = update.effective_chat
        if chat is None:
            return

        target_chat_id = deliver_chat_id if deliver_chat_id is not None else chat.id
        side_label = "SHORT" if user_side == "short" else "LONG"
        hours = manual_ta_hours(interval_minutes)
        progress = (
            f"⏳ Оцениваю ваш <b>{side_label}</b>: <b>{symbol}</b> · {interval_minutes}m…"
        )

        if query:
            try:
                await query.answer(f"Ваш {side_label}")
            except BadRequest:
                pass
            try:
                await query.edit_message_text(progress, parse_mode=ParseMode.HTML)
            except BadRequest:
                pass
        elif update.message:
            await update.message.reply_text(progress, parse_mode=ParseMode.HTML)

        oi_bars = None
        liq_context = None
        if self.scanner is not None:
            if interval_minutes == 5:
                try:
                    oi_bars = self.scanner.get_five_min_oi_bars("bybit", symbol)
                except Exception:
                    oi_bars = None
                try:
                    stats_5 = self.scanner._get_liquidation_stats("bybit", symbol, 5)
                    stats_15 = self.scanner._get_liquidation_stats("bybit", symbol, 15)
                    liq_context = merge_liq_stats_dict(
                        stats_5.to_dict() if stats_5 else None,
                        stats_15.to_dict() if stats_15 else None,
                    )
                except Exception:
                    liq_context = None

        last = self._manual_ta_last.get((target_chat_id, symbol, interval_minutes), {})
        chart_source = (
            chart_source
            or last.get("chart_source")
            or self.settings_manager.settings.manual_ta_chart_source
        )

        try:
            png, ta = await asyncio.wait_for(
                render_annotated_chart(
                    symbol,
                    side=user_side,
                    hours=hours,
                    interval_minutes=interval_minutes,
                    oi_bars=oi_bars,
                    neutral=True,
                    chart_source=chart_source,
                    exchange="bybit",
                    liq_context=liq_context,
                ),
                timeout=50.0,
            )
        except asyncio.TimeoutError:
            err = f"Таймаут загрузки свечей для {symbol} ({interval_minutes}m)."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return
        except Exception:
            logger.exception("Manual TA intent failed for %s %sm %s", symbol, interval_minutes, user_side)
            err = f"Ошибка построения графика {symbol}."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return

        if not png or ta is None:
            err = f"Нет данных Bybit для {symbol} ({interval_minutes}m)."
            if query and query.message:
                await query.message.reply_text(err)
            elif update.message:
                await update.message.reply_text(err)
            return

        score = ta_display_score(ta)
        sticky_breakout = last.get("plan_breakout") or last.get("breakout")
        score_label = "ясность" if ta.verdict == "WAIT" else "уверенность"
        caption = (
            f"<b>{symbol}</b> · Bybit {interval_minutes}m · ваш <b>{side_label}</b>\n"
            f"{ta_user_intent_html(ta, user_side, sticky_breakout=sticky_breakout)}\n\n"
            f"📐 TA сейчас: <b>{ta.verdict}</b> · {score_label} {score}/10 · "
            f"цена <b>{fmt_price(ta.current_price)}</b>"
        )

        plan_breakout = ta.breakout_level
        # Анти-chase: если раньше дали LONG-триггер и цена его взяла / почти взяла —
        # не перезаписываем план более высоким «скользящим» хаем.
        if (
            sticky_breakout
            and ta.breakout_level
            and ta.current_price
            and float(sticky_breakout) > 0
        ):
            sticky_f = float(sticky_breakout)
            fresh_f = float(ta.breakout_level)
            px = float(ta.current_price)
            if fresh_f > sticky_f * 1.002 and px >= sticky_f * 0.997:
                plan_breakout = sticky_f
            elif px < sticky_f * 0.96:
                plan_breakout = fresh_f

        self._manual_ta_last[(target_chat_id, symbol, interval_minutes)] = {
            "verdict": ta.verdict,
            "priority": ta.action_priority,
            "breakout": ta.breakout_level,
            "plan_breakout": plan_breakout,
            "breakdown": ta.breakdown_level,
            "price": ta.current_price,
            "updated_at": time.time(),
            "chart_source": chart_source,
            "user_side": user_side,
        }
        self._manual_ta_last_by_chat[target_chat_id] = {
            "symbol": symbol,
            "interval": interval_minutes,
            "chart_source": chart_source,
        }

        keyboard = self._manual_ta_result_keyboard(
            symbol, interval_minutes, ta, chat_id=target_chat_id,
        )
        await self._send_chart(
            target_chat_id,
            png,
            caption,
            is_priority=False,
            keyboard=keyboard,
        )

    async def on_manual_ta_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        if await self._handle_mta_wizard_photo(update, context):
            return
        if not self._is_manual_ta_chat(update):
            return

        symbol, interval = parse_manual_ta_input(update.message.caption or "")
        if not symbol:
            await update.message.reply_text(
                "Укажите тикер в подписи к фото:\n<code>GRASSUSDT</code> или <code>GRASS 10m</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if interval in MANUAL_TA_TIMEFRAMES:
            await update.message.reply_text(
                f"📐 <b>{symbol}</b> · {interval}m\nВыберите тип графика:",
                parse_mode=ParseMode.HTML,
                reply_markup=self._manual_ta_chart_source_keyboard(symbol, interval, wizard=False),
            )
        else:
            await self._prompt_manual_ta_timeframe(update, symbol)

    async def on_manual_ta_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        if await self._handle_mta_wizard_text(update, context):
            return
        if not self._is_manual_ta_chat(update):
            return

        text = (update.message.text or "").strip()
        if not text:
            return
        if text.lower() in {"/ta", "/help", "/start", "help", "помощь", "команды"}:
            await update.message.reply_text(
                manual_ta_help_text(),
                parse_mode=ParseMode.HTML,
            )
            return

        intent = parse_user_trade_intent(text)
        if intent:
            chat_id = update.effective_chat.id
            last = self._manual_ta_last_by_chat.get(chat_id)
            if last:
                await self._process_manual_ta_intent(
                    update,
                    last["symbol"],
                    int(last["interval"]),
                    intent,
                    chart_source=last.get("chart_source"),
                )
                return
            await update.message.reply_text(
                "Сначала запросите разбор монеты, затем напишите "
                "<code>хочу шорт</code> или <code>хочу long</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        symbol, interval = parse_manual_ta_input(text)
        if not symbol:
            await update.message.reply_text(
                "Не распознал тикер. Пример: <code>GRASSUSDT</code> или <code>GRASS 10m</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if interval in MANUAL_TA_TIMEFRAMES:
            await update.message.reply_text(
                f"📐 <b>{symbol}</b> · {interval}m\nВыберите тип графика:",
                parse_mode=ParseMode.HTML,
                reply_markup=self._manual_ta_chart_source_keyboard(symbol, interval, wizard=False),
            )
        else:
            await self._prompt_manual_ta_timeframe(update, symbol)

    async def on_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        if not update.message:
            return

        raw = (context.args[0] if context.args else "BTCUSDT").upper().strip()
        symbol = raw if raw.endswith("USDT") else f"{raw}USDT"
        side = "short" if len(context.args) > 1 and context.args[1].lower() == "short" else "long"
        hours = settings.signal_chart_hours if (settings := self.settings_manager.settings) else 5

        await update.message.reply_text(f"⏳ TA-график <b>{symbol}</b> ({side})…", parse_mode=ParseMode.HTML)

        oi_bars = None
        if self.scanner is not None:
            try:
                oi_bars = self.scanner.get_five_min_oi_bars("bybit", symbol)
            except Exception:
                oi_bars = None

        try:
            png, ta = await asyncio.wait_for(
                render_annotated_chart(symbol, side=side, hours=hours, oi_bars=oi_bars),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            await update.message.reply_text(f"Таймаут загрузки свечей для {symbol}.")
            return
        except Exception:
            logger.exception("Manual chart failed for %s", symbol)
            await update.message.reply_text(f"Ошибка построения графика {symbol}.")
            return

        if not png or ta is None:
            await update.message.reply_text(f"Нет данных Bybit для {symbol}.")
            return

        caption = ta_telegram_caption_html(ta)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=coinglass_url(symbol, "bybit"))],
        ])
        await self._send_chart(
            update.effective_chat.id,
            png,
            caption,
            is_priority=False,
            keyboard=keyboard,
        )

    async def on_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        if self.scanner is None:
            await update.message.reply_text("Сканер ещё не инициализирован.")
            return

        d = self.scanner.get_diagnostics()
        s = self.settings_manager.settings
        signals_state = "✅ ВКЛ" if d["signals_enabled"] else "⏸ ВЫКЛ"
        warmup = (
            f"Готово к сигналам: <b>{d['pairs_ready']}</b> пар "
            f"(нужна история от {s.pulse_period_minutes} мин для пульса)"
        )
        if d["pairs_ready"] == 0:
            warmup += (
                "\n⚠️ Подождите накопления истории или уменьшите период: "
                "<code>/set period 5</code>"
            )

        text = (
            "<b>📡 Диагностика сканера</b>\n\n"
            f"Сигналы: {signals_state}\n"
            f"Пар в памяти: <b>{d['pairs_tracked']}</b>\n"
            f"С ценой и OI: <b>{d['pairs_with_oi']}</b>\n"
            f"{warmup}\n"
            f"Макс. точек истории: <b>{d['max_history_points']}</b>\n\n"
            f"<b>Пороги:</b>\n"
            f"LONG {d['long_period_minutes']}м | SHORT {d['short_period_minutes']}м | "
            f"Пульс {d['pulse_period_minutes']}м\n"
            f"OI ≥ {d['oi_rise_percent']}% | Цена ≥ {d['price_rise_percent']}%\n"
            f"Мин. OI: {d['min_open_interest']:,.0f} $ | Score ≥ {d['min_signal_score']}\n"
            f"Топ монет: {d['top_n_symbols'] or 'все'}\n\n"
        )
        if self.analysis_engine is not None:
            ad = self.analysis_engine.get_diagnostics()
            analysis_on = (
                s.analysis_enabled and self.config.analysis_chat_configured
            )
            chat_note = (
                "чат OK"
                if self.config.analysis_chat_configured
                else "⚠️ нет TELEGRAM_ANALYSIS_CHAT_ID"
            )
            text += (
                f"<b>🧠 Анализ ликвидаций:</b> {'ON' if analysis_on else 'OFF'} ({chat_note})\n"
                f"Запланировано: <b>{ad['scheduled']}</b> | "
                f"Отправлено: <b>{ad['sent']}</b> | "
                f"из сигналов <b>{ad.get('from_signal', 0)}</b> | "
                f"В очереди: <b>{ad['pending']}</b>\n"
                f"Отсечено: порог <b>{ad['skipped_threshold']}</b> | "
                f"тренд <b>{ad.get('skipped_trend', 0)}</b> | "
                f"CD <b>{ad.get('skipped_cooldown', 0)}</b> | "
                f"лимит/ч <b>{ad.get('skipped_rate_limit', 0)}</b> | "
                f"conf <b>{ad['skipped_confidence']}</b> | "
                f"ошибки <b>{ad['errors']}</b>\n"
                f"Тренд+liq+OI/CVD · conf≥{ad['analysis_min_confidence']:.0f}% · "
                f"delay {ad['analysis_delay_seconds']}с\n\n"
            ).replace(",", " ")
        text += (
            "<i>Если сигналов нет — попробуйте:</i>\n"
            "<code>/set period 5</code>\n"
            "<code>/set oi 1</code>\n"
            "<code>/set price 0.5</code>\n"
            "<code>/resume</code> (если на паузе)"
        ).replace(",", " ")
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=self._reply_keyboard())

    async def on_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await self._set_bot_paused(update, paused=True)

    async def on_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await self._set_bot_paused(update, paused=False)

    async def _set_bot_paused(self, update: Update, *, paused: bool) -> None:
        settings = self.settings_manager.settings
        if paused:
            if not settings.bot_paused:
                self._pause_snapshot = {
                    key: bool(getattr(settings, key))
                    for key in self._BOT_PAUSE_KEYS
                }
            self._set_all_notification_channels(False)
            text = (
                "⏸ <b>Бот остановлен</b> — все каналы выключены.\n"
                "Тонкая настройка: кнопка <b>🎛 Каналы</b>.\n\n"
                "Сканер продолжает собирать данные в фоне.\n"
                "Нажмите <b>▶️ Старт</b> или /resume для восстановления."
            )
        else:
            restore = self._pause_snapshot
            if restore is None:
                restore = {
                    "signals_enabled": True,
                    "liquidation_alerts_enabled": True,
                    "analysis_enabled": True,
                    "anomaly_enabled": settings.anomaly_enabled,
                    "scenario_watch_enabled": True,
                    "manual_ta_alerts_enabled": True,
                }
            self.settings_manager.update(bot_paused=False, **restore)
            self._pause_snapshot = None
            self._sync_bot_paused_from_channels()
            text = (
                "▶️ <b>Бот запущен</b> — каналы восстановлены:\n"
                f"• сигналы: <b>{'ВКЛ' if restore.get('signals_enabled') else 'ВЫКЛ'}</b>\n"
                f"• ликвидации: <b>{'ВКЛ' if restore.get('liquidation_alerts_enabled') else 'ВЫКЛ'}</b>\n"
                f"• анализ: <b>{'ВКЛ' if restore.get('analysis_enabled') else 'ВЫКЛ'}</b>\n"
                f"• аномалии: <b>{'ВКЛ' if restore.get('anomaly_enabled') else 'ВЫКЛ'}</b>\n"
                f"• сценарии: <b>{'ВКЛ' if restore.get('scenario_watch_enabled') else 'ВЫКЛ'}</b>\n"
                f"• алерты руч. TA: <b>{'ВКЛ' if restore.get('manual_ta_alerts_enabled') else 'ВЫКЛ'}</b>\n\n"
                "Или настройте выборочно: <b>🎛 Каналы</b>."
            )
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=self._reply_keyboard(),
        )

    def _signals_status_line(self) -> str:
        if self.settings_manager.settings.bot_paused:
            return "⏸ <b>Бот на паузе</b> — все каналы выкл · 🎛 Каналы для выборочного вкл"
        settings = self.settings_manager.settings
        parts: list[str] = []
        for field, _, label in self._NOTIFICATION_CHANNELS:
            short = label.split(" ", 1)[0]
            on = bool(getattr(settings, field))
            parts.append(f"{short} {'✅' if on else '❌'}")
        return "🔔 " + " · ".join(parts)

    def _signals_toggle_button_label(self) -> str:
        if self.settings_manager.settings.bot_paused:
            return "▶️ Старт"
        return "⏸ Стоп"

    async def on_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update) or update.message is None:
            return
        text = (update.message.text or "").strip()
        if text in {"⏸ Стоп", "Стоп", "⏸ Стоп сигналы", "⏸ Стоп бот", "Стоп бот"}:
            await self._set_bot_paused(update, paused=True)
        elif text in {"▶️ Старт", "Старт", "▶️ Старт сигналы", "▶️ Старт бот", "Старт бот"}:
            await self._set_bot_paused(update, paused=False)
        elif text in {"🎛 Каналы", "Каналы", "🎛 Уведомления", "Уведомления"}:
            await self._show_notifications_panel(update)
        elif text in {"📊 Биржи", "Биржи"}:
            await update.message.reply_text(
                self._build_exchanges_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._exchanges_keyboard(),
            )
        elif text in {"⚙ Настройки", "🔧 Настройки", "Настройки"}:
            await update.message.reply_text(
                await self._build_settings_panel_text_async(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_keyboard(),
            )
        elif text in {"💧 Ликвидация", "Ликвидация"}:
            await update.message.reply_text(
                self._build_liquidation_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._liquidation_keyboard(),
            )
        elif text in {"🧠 Анализ", "Анализ"}:
            await update.message.reply_text(
                self._build_analysis_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._analysis_keyboard(),
            )
        elif text in {"📈 Статус", "Статус"}:
            await update.message.reply_text(
                await self._build_settings_panel_text_async(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._reply_keyboard(),
            )
        elif text in {"📐 Ручной анализ", "Ручной анализ"}:
            await self._start_manual_ta_wizard(update, context)
        elif text in {"📋 Команды", "Команды", "❓ Помощь", "Помощь"}:
            await update.message.reply_text(
                self._build_help_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._reply_keyboard(),
            )

    @staticmethod
    def _build_help_text() -> str:
        return (
            "<b>📋 Доступные команды</b>\n\n"
            "/start — главное меню\n"
            "/status — текущие настройки\n"
            "/settings — inline-настройки порогов\n"
            "/set help — точная настройка через команды\n"
            "/test — тестовые сигналы LONG + SHORT\n"
            "/chart SYMBOL [long|short] — TA-график с уровнями и планом\n"
            "/ta — справка по ручному TA-чату\n"
            "📐 <b>Ручной анализ</b> — скрин + тикер → разбор в отдельный чат\n"
            "/scan — диагностика сканера\n"
            "/pause — остановить все каналы\n"
            "/resume — восстановить каналы\n"
            "🎛 <b>Каналы</b> — выборочно вкл/выкл каждое направление\n"
            "/history [N] — последние N сигналов (нужен Redis)\n"
            "/help — эта справка\n\n"
            "💧 <b>Ликвидация</b> — пороги REKT-алертов в обычный чат\n"
            "🧠 <b>Анализ</b> — разборы в отдельный чат (liq, OI, тренд, цена)\n\n"
            "🟢 LONG = рост цены | 🔴 SHORT = падение\n"
            "🔥 score 1–2 = приоритет (звук)\n\n"
            "Все настройки применяются сразу."
        )

    def _build_exchanges_text(self) -> str:
        s = self.settings_manager.settings
        return (
            "<b>📊 Биржи</b> (применяется сразу)\n"
            f"Binance: {'✅ включена' if s.enabled_binance else '❌ выключена'}\n"
            f"Bybit: {'✅ включена' if s.enabled_bybit else '❌ выключена'}"
        )

    def _build_liquidation_panel_text(self) -> str:
        s = self.settings_manager.settings
        tier_note = (
            f"альт <b>${s.liquidation_alt_min_usd:,.0f}</b> · "
            f"mid <b>${s.liquidation_mid_min_usd:,.0f}</b> · "
            f"крупн. <b>${s.liquidation_min_usd:,.0f}</b>"
            if s.liquidation_tier_enabled
            else f"единый порог <b>${s.liquidation_min_usd:,.0f}</b>"
        )
        return (
            "<b>💧 Ликвидации</b> (обычный чат сигналов)\n"
            f"Статус: <b>{'ON' if s.liquidation_alerts_enabled else 'OFF'}</b>\n\n"
            f"Пороги: {tier_note}\n"
            f"Tier по OI: <b>{'ON' if s.liquidation_tier_enabled else 'OFF'}</b>\n"
            f"Окно всплеска: <b>{s.liquidation_burst_window_seconds:g}с</b> · "
            f"скользящее: <b>{int(s.liquidation_sliding_window_seconds)}с</b>\n"
            f"Cooldown: <b>{s.liquidation_cooldown_seconds}с</b> · "
            f"все монеты: <b>{'ON' if s.liquidation_all_symbols else 'OFF'}</b>\n"
            f"Подсказка разворота: <b>{'ON' if s.liquidation_show_reversal_hint else 'OFF'}</b>\n\n"
            "<i>Кнопки применяются сразу</i>"
        ).replace(",", " ")

    def _build_analysis_panel_text(self) -> str:
        s = self.settings_manager.settings
        chat_ok = self.config.analysis_chat_configured
        chat_line = "чат OK" if chat_ok else "⚠️ нет TELEGRAM_ANALYSIS_CHAT_ID"
        oi_line = (
            f"мин. OI <b>${s.analysis_min_oi_usd:,.0f}</b>"
            if s.analysis_min_oi_usd > 0
            else "мин. OI <b>любой</b>"
        )
        price_line = (
            f"движение цены ≥<b>{s.analysis_min_price_move_pct:g}%</b>"
            if s.analysis_min_price_move_pct > 0
            else "движение цены <b>любое</b>"
        )
        diag = ""
        if self.analysis_engine is not None:
            ad = self.analysis_engine.get_diagnostics()
            diag = (
                f"\n\n<b>Диагностика:</b> заплан. <b>{ad['scheduled']}</b> · "
                f"отправлено <b>{ad['sent']}</b> · "
                f"отсечено conf <b>{ad['skipped_confidence']}</b>"
            )
        return (
            "<b>🧠 Анализ ликвидаций</b> (отдельный чат)\n"
            f"Статус: <b>{'ON' if s.analysis_enabled and chat_ok else 'OFF'}</b> ({chat_line})\n\n"
            f"Liq: альт <b>${s.analysis_alt_min_liq_usd:,.0f}</b> · "
            f"стандарт <b>${s.analysis_min_liq_usd:,.0f}</b> · "
            f"мейджор <b>${s.analysis_major_min_liq_usd:,.0f}</b>\n"
            f"{oi_line} · {price_line} · тренд ≥<b>{s.analysis_min_trend_pct:g}%</b>\n"
            f"Conf ≥<b>{s.analysis_min_confidence:.0f}%</b> · "
            f"delay <b>{s.analysis_delay_seconds}с</b> · "
            f"CD <b>{s.analysis_cooldown_seconds}с</b> · "
            f"макс <b>{s.analysis_max_per_hour}</b>/ч\n"
            f"Триггер от сигналов: <b>{'ON' if s.analysis_signal_trigger_enabled else 'OFF'}</b> · "
            f"график: <b>{'ON' if s.analysis_chart_enabled else 'OFF'}</b> · "
            f"альты: <b>{'OFF' if s.analysis_skip_alt_tier else 'ON'}</b>"
            f"{diag}\n\n"
            "<i>Сильные объёмные монеты с прыжком цены 2–3% + liq от $10k</i>"
        ).replace(",", " ")

    def _exchange_effective_line(self, name: str, exchange: str) -> str:
        s = self.settings_manager.settings
        prefix = "bybit" if "bybit" in exchange.lower() else "binance"
        thresholds = s.for_exchange(exchange)
        has_override = any(
            getattr(s, f"{prefix}_{field}") is not None
            for field in (
                "oi_rise_percent",
                "oi_drop_percent",
                "price_rise_percent",
                "price_drop_percent",
                "long_period_minutes",
                "short_period_minutes",
            )
        )
        tag = "свои" if has_override else "глобальные"
        return (
            f"{name} (<i>{tag}</i>): "
            f"L<b>{thresholds.long_period_minutes}</b>м/"
            f"S<b>{thresholds.short_period_minutes}</b>м, "
            f"OI↑<b>{thresholds.oi_rise_percent}</b>% "
            f"OI↓<b>{thresholds.oi_drop_percent}</b>%"
        )

    def _build_settings_panel_text(self) -> str:
        s = self.settings_manager.settings
        top_label = "все" if not s.top_n_symbols else str(s.top_n_symbols)
        pulse_oi = (
            max(s.pulse_oi_rise_percent, s.oi_rise_percent)
            if s.respect_global_floors
            else s.pulse_oi_rise_percent
        )
        pulse_price = (
            max(s.pulse_price_rise_percent, s.price_rise_percent)
            if s.respect_global_floors
            else s.pulse_price_rise_percent
        )
        mega_tiers = (
            [t for t in s.flash_price_tiers if t >= s.price_rise_percent]
            if s.respect_global_floors
            else list(s.flash_price_tiers)
        )
        mega_label = ",".join(f"{int(t)}%" if t == int(t) else str(t) for t in mega_tiers) or "—"
        return (
            "<b>⚙ Настройки сканера</b>\n"
            f"{self._signals_status_line()}\n"
            "<i>Кнопки задают минимум OI <b>и</b> цены вместе</i>\n\n"
            f"📅 LONG: <b>{s.long_period_minutes} мин</b> | SHORT: <b>{s.short_period_minutes} мин</b>\n"
            f"⚡ Пульс: <b>{s.pulse_period_minutes} мин</b> "
            f"(OI≥<b>{pulse_oi}</b>% / цена≥<b>{pulse_price}</b>%)\n"
            f"🚀 Мега: <b>{','.join(str(m) for m in s.flash_window_minutes)} мин</b> "
            f"(от <b>{mega_label}</b>)\n"
            f"📈 Рост OI: <b>≥ {s.oi_rise_percent}%</b> | 📉 Падение: <b>≥ {s.oi_drop_percent}%</b>\n"
            f"🟢 LONG цена: <b>≥ {s.price_rise_percent}%</b> | 🔴 SHORT: <b>≥ {s.price_drop_percent}%</b>\n"
            f"💰 Мин. OI: <b>{s.min_open_interest:,.0f}</b> | Приток: <b>{s.min_oi_change_soft_usd:,.0f}–{s.min_oi_change_usd:,.0f} $</b>\n"
            f"📊 Tier OI: мейджор <b>${s.major_min_open_interest:,.0f}</b> | "
            f"топ <b>${s.min_open_interest:,.0f}</b> | альт <b>${s.alt_min_open_interest:,.0f}</b>\n"
            f"🏆 Топ монет: <b>{top_label}</b> | Tier: <b>{'ON' if s.tier_enabled else 'OFF'}</b> "
            f"(мейджор≤<b>{s.major_min_signal_score:.0f}</b> / стандарт≤<b>{s.standard_min_signal_score:.0f}</b> / альт≤<b>{s.alt_min_signal_score:.0f}</b>)\n"
            f"🔥 Приоритет: ≤<b>{s.priority_score_max}</b>/10 | CD: <b>{s.signal_cooldown_seconds}с</b>\n"
            f"{self._exchange_effective_line('Binance', 'Binance')}\n"
            f"{self._exchange_effective_line('Bybit', 'Bybit')}\n"
            f"Binance: <b>{'ON' if s.enabled_binance else 'OFF'}</b> | "
            f"Bybit: <b>{'ON' if s.enabled_bybit else 'OFF'}</b>\n"
            f"🚨 Вертикальный памп: <b>{'ON' if s.breakout_enabled else 'OFF'}</b> "
            f"(флет ≤{s.breakout_max_flat_percent}% → ±{s.breakout_min_spike_percent}% "
            f"· мейджоры ±{s.major_breakout_min_spike_percent}% за {s.breakout_spike_minutes}м)\n"
            f"💧 Liq-cascade: <b>{'ON' if s.liq_cascade_enabled else 'OFF'}</b> "
            f"(≥<b>{s.major_liq_cascade_min_usd:,.0f}</b>$ мейджоры / "
            f"<b>{s.liq_cascade_min_usd:,.0f}</b>$ · цена≥<b>{s.major_liq_cascade_min_price_percent}%</b>)\n"
            f"↩️ Резкий разворот: <b>{'ON' if s.reversal_enabled else 'OFF'}</b> "
            f"(±{s.reversal_min_prior_move_pct}% → ∓{s.reversal_min_reversal_pct}% за {s.reversal_spike_minutes}м"
            f"{'' if not s.reversal_block_long_after_dump else f' · блок LONG после −{s.reversal_block_min_dump_pct:g}%'})\n"
            f"📉 Импульс: <b>{'ON' if s.impulse_enabled else 'OFF'}</b> "
            f"({','.join(str(m) for m in s.impulse_window_minutes)}м · "
            f"{'/'.join(f'{int(t)}%' if t == int(t) else str(t) for t in s.impulse_price_tiers)})\n"
            f"💧 Ликвидации: <b>{'ON' if s.liquidation_alerts_enabled else 'OFF'}</b> "
            f"(alt <b>${s.liquidation_alt_min_usd:,.0f}</b> / mid <b>${s.liquidation_mid_min_usd:,.0f}</b> / "
            f"крупн. <b>${s.liquidation_min_usd:,.0f}</b> · окно <b>{int(s.liquidation_sliding_window_seconds)}с</b>)\n"
            f"🎯 Фильтр вероятности: <b>{'ON' if s.probability_filter_enabled else 'OFF'}</b> "
            f"(мин. <b>{s.min_probability_percent:.0f}%</b>)\n"
            f"✅ Только готовые входы: <b>{'ON' if s.actionable_signals_only else 'OFF'}</b> "
            f"(TA≥<b>{s.actionable_min_ta_score}</b>/10 · триггер ≤<b>{s.actionable_max_trigger_dist_pct:g}%</b> · "
            f"⏱ <b>{s.actionable_min_signal_score}–{s.actionable_max_signal_score}</b>/10)\n"
            f"🏷 Бейдж готовности: <b>{'ON' if s.actionable_show_readiness_badge else 'OFF'}</b> "
            f"(по TA, без ⏱ ранности сканера)\n"
            f"📈 TA-график к сигналам: <b>{'ON' if s.signal_chart_enabled else 'OFF'}</b> "
            f"· режим <b>{s.signal_chart_source}</b> · {s.signal_chart_hours}ч\n"
            f"📋 Playbook (Hot): <b>{'ON' if s.signal_playbook_enabled else 'OFF'}</b> · "
            f"Pro → анализ: <b>{'ON' if s.signal_pro_to_analysis_chat else 'OFF'}</b> · "
            f"Цели ✅: <b>{'ON' if s.target_watcher_enabled else 'OFF'}</b>\n"
            f"🛡 Quality gate: <b>{'ON' if s.signal_quality_gate_enabled else 'OFF'}</b> · "
            f"CVD: <b>{'ON' if s.signal_cvd_gate_enabled else 'OFF'}</b> · "
            f"HTF: <b>{'ON' if s.signal_htf_gate_enabled else 'OFF'}</b> · "
            f"WATCH→TRIGGER: <b>{'ON' if s.scenario_watch_enabled else 'OFF'}</b>\n"
            f"🧠 Чат анализов: <b>{'ON' if s.analysis_enabled and self.config.analysis_chat_configured else 'OFF'}</b> "
            f"(тренд+liq+OI/CVD · liq ≥<b>${s.analysis_alt_min_liq_usd:,.0f}</b>–<b>${s.analysis_major_min_liq_usd:,.0f}</b> · "
            f"тренд≥<b>{getattr(s, 'analysis_min_trend_pct', 2.0):.0f}%</b> · "
            f"макс <b>{getattr(s, 'analysis_max_per_hour', 4)}</b>/ч · conf≥<b>{s.analysis_min_confidence:.0f}%</b> · "
            f"{'альты OFF' if s.analysis_skip_alt_tier else 'альты ON'}"
            f"{'' if not (s.anomaly_enabled and self.config.anomaly_chat_configured) else f' · аномалии {s.anomaly_max_per_minute}/мин'})\n\n"
            "<i>В уведомлении % — фактическое движение, не порог</i>\n"
            "Точная настройка: /set help"
        ).replace(",", " ")

    @staticmethod
    def _format_analysis_outcome_stats(summary: AnalysisOutcomeSummary) -> str:
        if summary.total_completed == 0 and summary.pending == 0:
            return "\n📊 <b>Исходы анализов (7д):</b> ещё нет данных"
        lines = [f"\n📊 <b>Исходы анализов ({summary.days}д):</b>"]
        if summary.total_completed > 0:
            rate_60 = summary.success_rate_60m
            rate_60_text = f"{rate_60}%" if rate_60 is not None else "—"
            lines.append(
                f"✅ 60м: <b>{rate_60_text}</b> "
                f"({summary.success_60m}/{summary.total_completed})"
            )
            if summary.success_rate_30m is not None:
                rate_15 = (
                    round(summary.success_15m / summary.total_completed * 100, 1)
                    if summary.total_completed
                    else None
                )
                rate_15_text = f"{rate_15}%" if rate_15 is not None else "—"
                lines.append(
                    f"30м: {summary.success_rate_30m}% · "
                    f"15м: {rate_15_text} "
                    f"({summary.success_15m}/{summary.total_completed})"
                )
        if summary.pending > 0:
            lines.append(f"⏳ в проверке: <b>{summary.pending}</b>")
        return "\n".join(lines)

    async def _build_settings_panel_text_async(self) -> str:
        text = self._build_settings_panel_text()
        if (
            self.analysis_outcome_tracker is not None
            and self.settings_manager.settings.analysis_outcome_tracking_enabled
        ):
            try:
                summary = await self.analysis_outcome_tracker.get_summary(days=7)
                text += self._format_analysis_outcome_stats(summary)
            except Exception:
                logger.exception("Failed to load analysis outcome stats")
        return text

    def _mark(self, label: str, is_active: bool) -> str:
        return f"✅ {label}" if is_active else label

    async def _safe_edit_message_text(
        self,
        query: CallbackQuery,
        text: str,
        parse_mode: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except BadRequest as exc:
            if "Message is not modified" in str(exc):
                return
            raise

    async def on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return

        payload = query.data or ""
        if payload.startswith("symcopy:"):
            sym = payload.split(":", 1)[1].upper()
            if sym and query.message:
                await query.answer("Тикер ниже — нажмите, чтобы скопировать")
                try:
                    await query.message.reply_text(
                        f"📋 <code>{sym}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                except BadRequest:
                    await query.message.reply_text(sym)
            else:
                await query.answer("Тикер не найден", show_alert=True)
            return
        if payload.startswith("sigpro:"):
            token = payload.split(":", 1)[1]
            pro_text = await self._load_signal_pro(token)
            if pro_text and query.message:
                await query.answer("Полный разбор")
                try:
                    await query.message.reply_text(
                        pro_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                except BadRequest:
                    await query.message.reply_text(
                        _plain_caption(pro_text),
                        disable_web_page_preview=True,
                    )
            else:
                await query.answer("Разбор устарел — дождитесь нового сигнала", show_alert=True)
            return
        if await self._handle_channels_callback(update, query, payload):
            return
        if payload == MTW_CANCEL_CALLBACK:
            if not self._is_admin(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            self._clear_mta_wizard(context)
            await query.answer("Отменено")
            try:
                await query.edit_message_text("❌ Ручной анализ отменён.")
            except BadRequest:
                pass
            return

        if payload.startswith(MTW_CALLBACK_PREFIX):
            if not self._is_admin(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            parsed = parse_mtw_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval = parsed
            await self._finish_mta_wizard(update, context, symbol, interval, query=query)
            return

        if payload.startswith(MTCW_CALLBACK_PREFIX):
            if not self._is_admin(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            parsed = parse_mtcw_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval, chart_source = parsed
            await self._finish_mta_wizard(
                update,
                context,
                symbol,
                interval,
                query=query,
                chart_source=chart_source,
            )
            return

        if payload.startswith(MTA_CALLBACK_PREFIX):
            if not self._can_use_manual_ta(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            parsed = parse_mta_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval = parsed
            await query.answer("Выберите вид графика")
            try:
                await query.edit_message_text(
                    f"📐 <b>{symbol}</b> · {interval}m\nВыберите тип графика:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._manual_ta_chart_source_keyboard(
                        symbol,
                        interval,
                        wizard=False,
                    ),
                )
            except BadRequest:
                if query.message:
                    await query.message.reply_text(
                        f"📐 <b>{symbol}</b> · {interval}m\nВыберите тип графика:",
                        parse_mode=ParseMode.HTML,
                        reply_markup=self._manual_ta_chart_source_keyboard(
                            symbol,
                            interval,
                            wizard=False,
                        ),
                    )
            return

        if payload.startswith(MTA_INTENT_CALLBACK_PREFIX):
            if not self._can_use_manual_ta(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            parsed = parse_mta_intent_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval, user_side = parsed
            chat_id = update.effective_chat.id if update.effective_chat else 0
            last = self._manual_ta_last.get((chat_id, symbol, interval), {})
            await self._process_manual_ta_intent(
                update,
                symbol,
                interval,
                user_side,
                query=query,
                chart_source=last.get("chart_source"),
            )
            return

        if payload.startswith(MTA_MUTE_CALLBACK_PREFIX):
            if not self._can_use_manual_ta(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            parsed = parse_mta_mute_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval, action = parsed
            chat_id = update.effective_chat.id if update.effective_chat else 0
            if action == "mute":
                self._mute_manual_ta_symbol(chat_id, symbol)
                await query.answer(f"🔕 {symbol} — алерты выкл на 24ч", show_alert=False)
                note = (
                    f"🔕 <b>{symbol}</b> — алерты отключены на <b>24 часа</b>.\n"
                    "Пробой / ретест / объём по этой монете не придут.\n"
                    "Включить снова: кнопка под графиком или новый разбор."
                )
            elif action == "unmute":
                self._unmute_manual_ta_symbol(chat_id, symbol)
                await query.answer(f"🔔 {symbol} снова активна", show_alert=False)
                note = f"🔔 <b>{symbol}</b> — алерты снова можно включать."
            else:
                removed = self._stop_manual_ta_alerts_for_symbol(chat_id, symbol)
                await query.answer(
                    f"⏹ Снято алертов: {removed}" if removed else "Алертов не было",
                    show_alert=False,
                )
                note = (
                    f"⏹ <b>{symbol}</b> — активные алерты сняты ({removed}).\n"
                    "Можно поставить новые кнопками под графиком."
                )
            if query.message:
                try:
                    await query.message.reply_text(note, parse_mode=ParseMode.HTML)
                except Exception:
                    logger.exception("Failed to send manual TA mute confirmation")
            return

        if payload.startswith(MTA_ALERT_CALLBACK_PREFIX):
            if not self._can_use_manual_ta(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            if self._bot_notifications_blocked():
                await query.answer("Бот на паузе — сначала ▶️ Старт", show_alert=True)
                return
            if not self.settings_manager.settings.manual_ta_alerts_enabled:
                await query.answer("Алерты ручного TA выкл — 🎛 Каналы", show_alert=True)
                return
            parsed = parse_mta_alert_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval, side, mode = parsed
            chat_id = update.effective_chat.id if update.effective_chat else 0
            if self._is_manual_ta_muted(chat_id, symbol):
                await query.answer(
                    f"🔕 {symbol} на паузе 24ч — сначала включите монету",
                    show_alert=True,
                )
                return
            last = self._manual_ta_last.get((chat_id, symbol, interval), {})
            trigger = 0.0
            if side == "short":
                trigger = float(last.get("breakdown") or 0)
            else:
                trigger = float(last.get("breakout") or 0)
            if trigger <= 0 and mode in {"breakout", "retest"}:
                await query.answer("Нет уровня для алерта в этом сетапе.", show_alert=True)
                return
            if trigger <= 0 and mode == "volume":
                # Для объёмного алерта без явного уровня используем текущую цену как справочную.
                trigger = float(last.get("price") or 0) or float(last.get("breakout") or last.get("breakdown") or 0)
            self._manual_ta_alerts[(chat_id, symbol, interval, mode)] = {
                "side": side,
                "trigger": trigger,
                "created_at": time.time(),
                "expires_at": time.time() + 3 * 3600,
                "mode": mode,
            }
            await query.answer("Алерт включён на 3 часа", show_alert=False)
            if query.message:
                try:
                    mode_ru = {"breakout": "пробой", "retest": "ретест", "volume": "ускорение объёма"}.get(mode, mode)
                    await query.message.reply_text(
                        (
                            f"🔔 Алерт активирован: <b>{symbol}</b> {interval}m\n"
                            f"Тип: <b>{mode_ru}</b>\n"
                            f"Уровень: <b>{trigger:.6g}</b> ({'ниже' if side == 'short' else 'выше'})\n"
                            "Срок: 3 часа.\n"
                            "<i>Отмена: ⏹ Стоп алерты или 🔕 Монета OFF под графиком.</i>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    logger.exception("Failed to send manual TA alert confirmation")
            return

        if payload.startswith(MTC_CALLBACK_PREFIX):
            if not self._can_use_manual_ta(update):
                await query.answer("Нет доступа.", show_alert=True)
                return
            parsed = parse_mtc_callback(payload)
            if parsed is None:
                await query.answer("Некорректный запрос.", show_alert=True)
                return
            symbol, interval, chart_source = parsed
            await self._process_manual_ta_request(
                update,
                symbol,
                interval,
                query=query,
                chart_source=chart_source,
            )
            return

        if not self._is_admin(update):
            await query.answer("Нет доступа.", show_alert=True)
            return

        changed_label = ""

        handlers: dict[str, tuple[str, type, str]] = {
            "set_period:": ("oi_period_minutes", int, "Период"),
            "set_oi_rise:": ("oi_rise_percent", float, "Рост OI"),
            "set_oi_drop:": ("oi_drop_percent", float, "Падение OI"),
            "set_price_rise:": ("price_rise_percent", float, "Рост цены"),
            "set_price_drop:": ("price_drop_percent", float, "Падение цены"),
            "set_min_oi:": ("min_open_interest", float, "Мин. OI"),
            "set_min_oi_change:": ("min_oi_change_usd", float, "Приток OI"),
            "set_min_volume:": ("min_volume", float, "Мин. объём"),
            "set_min_score:": ("min_signal_score", float, "Мин. сигнал"),
            "set_cooldown:": ("signal_cooldown_seconds", int, "Cooldown"),
            "set_priority:": ("priority_score_max", int, "Приоритет score"),
            "set_top:": ("top_n_symbols", int, "Топ монет"),
        }

        for prefix, (field, caster, label) in handlers.items():
            if payload.startswith(prefix):
                raw = payload.split(":", 1)[1]
                if field == "top_n_symbols":
                    value = int(raw)
                    if value <= 0:
                        self.settings_manager.update(top_n_symbols=None)
                        changed_label = "Топ монет → все"
                    else:
                        self.settings_manager.update(top_n_symbols=value)
                        changed_label = f"Топ монет → {value}"
                else:
                    value = caster(raw)
                    if field == "oi_period_minutes":
                        self.settings_manager.update(
                            oi_period_minutes=value,
                            long_period_minutes=value,
                            short_period_minutes=value,
                        )
                        changed_label = f"Период LONG/SHORT → {value}м"
                    else:
                        self.settings_manager.update(**{field: value})
                        changed_label = f"{label} → {value}"
                break

        if changed_label:
            await query.answer(f"✅ {changed_label}", show_alert=False)
            await self._safe_edit_message_text(
                query,
                self._build_settings_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_keyboard(),
            )
            return

        liq_changed = await self._handle_liquidation_callback(query, payload)
        if liq_changed:
            return

        an_changed = await self._handle_analysis_callback(query, payload)
        if an_changed:
            return

        await query.answer()

        if payload == "toggle_binance":
            current = self.settings_manager.settings.enabled_binance
            self.settings_manager.update(enabled_binance=not current)
            await self._safe_edit_message_text(
                query,
                self._build_exchanges_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._exchanges_keyboard(),
            )
        elif payload == "toggle_bybit":
            current = self.settings_manager.settings.enabled_bybit
            self.settings_manager.update(enabled_bybit=not current)
            await self._safe_edit_message_text(
                query,
                self._build_exchanges_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._exchanges_keyboard(),
            )
        elif payload == "toggle_signals":
            current = self.settings_manager.settings.signals_enabled
            self.settings_manager.update(signals_enabled=not current)
            self._sync_bot_paused_from_channels()
            if not current:
                self._pause_snapshot = None
            state = "включены" if not current else "остановлены"
            await query.answer(f"✅ Сигналы {state}", show_alert=False)
            await self._safe_edit_message_text(
                query,
                self._build_settings_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_keyboard(),
            )
        elif payload == "toggle_actionable":
            current = self.settings_manager.settings.actionable_signals_only
            self.settings_manager.update(actionable_signals_only=not current)
            state = "ON" if not current else "OFF"
            await query.answer(f"✅ Готовый вход → {state}", show_alert=False)
            await self._safe_edit_message_text(
                query,
                self._build_settings_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_keyboard(),
            )
        elif payload == "refresh_settings":
            self.settings_manager.reload()
            await self._safe_edit_message_text(
                query,
                self._build_settings_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_keyboard(),
            )
        elif payload == "open_channels":
            await self._safe_edit_message_text(
                query,
                self._build_notifications_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._notifications_keyboard(),
            )
        elif payload == "open_liq":
            await self._safe_edit_message_text(
                query,
                self._build_liquidation_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._liquidation_keyboard(),
            )
        elif payload == "open_analysis":
            await self._safe_edit_message_text(
                query,
                self._build_analysis_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._analysis_keyboard(),
            )
        else:
            await self._safe_edit_message_text(query, "Неизвестное действие.")

    async def _handle_liquidation_callback(self, query: CallbackQuery, payload: str) -> bool:
        if not payload.startswith("liq:"):
            return False

        action = payload[4:]
        label = ""

        if action == "on":
            current = self.settings_manager.settings.liquidation_alerts_enabled
            self.settings_manager.update(liquidation_alerts_enabled=not current)
            self._sync_bot_paused_from_channels()
            if not current:
                self._pause_snapshot = None
            label = f"Ликвидации → {'ON' if not current else 'OFF'}"
        elif action == "tier":
            current = self.settings_manager.settings.liquidation_tier_enabled
            self.settings_manager.update(liquidation_tier_enabled=not current)
            label = f"Tier liq → {'ON' if not current else 'OFF'}"
        elif action == "all":
            current = self.settings_manager.settings.liquidation_all_symbols
            self.settings_manager.update(liquidation_all_symbols=not current)
            label = f"Все монеты → {'ON' if not current else 'OFF'}"
        elif action == "hint":
            current = self.settings_manager.settings.liquidation_show_reversal_hint
            self.settings_manager.update(liquidation_show_reversal_hint=not current)
            label = f"Подсказка → {'ON' if not current else 'OFF'}"
        elif action == "ref":
            self.settings_manager.reload()
            label = "Обновлено"
        elif action.startswith("m:"):
            value = float(action[2:])
            self.settings_manager.update(liquidation_min_usd=value)
            label = f"Крупные → ${value:,.0f}".replace(",", " ")
        elif action.startswith("a:"):
            value = float(action[2:])
            self.settings_manager.update(liquidation_alt_min_usd=value)
            label = f"Альты liq → ${value:,.0f}".replace(",", " ")
        elif action.startswith("d:"):
            value = float(action[2:])
            self.settings_manager.update(liquidation_mid_min_usd=value)
            label = f"Mid liq → ${value:,.0f}".replace(",", " ")
        elif action.startswith("cd:"):
            value = int(action[3:])
            self.settings_manager.update(liquidation_cooldown_seconds=value)
            label = f"Cooldown → {value}с"
        elif action.startswith("win:"):
            value = float(action[4:])
            self.settings_manager.update(liquidation_burst_window_seconds=value)
            label = f"Окно → {value:g}с"
        else:
            await query.answer("Неизвестное действие.", show_alert=True)
            return True

        await query.answer(f"✅ {label}", show_alert=False)
        await self._safe_edit_message_text(
            query,
            self._build_liquidation_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._liquidation_keyboard(),
        )
        return True

    async def _handle_analysis_callback(self, query: CallbackQuery, payload: str) -> bool:
        if not payload.startswith("an:"):
            return False

        action = payload[3:]
        label = ""

        if action == "on":
            current = self.settings_manager.settings.analysis_enabled
            self.settings_manager.update(analysis_enabled=not current)
            self._sync_bot_paused_from_channels()
            if not current:
                self._pause_snapshot = None
            label = f"Анализ → {'ON' if not current else 'OFF'}"
        elif action == "skalt":
            current = self.settings_manager.settings.analysis_skip_alt_tier
            self.settings_manager.update(analysis_skip_alt_tier=not current)
            label = f"Альты → {'OFF' if not current else 'ON'}"
        elif action == "chart":
            current = self.settings_manager.settings.analysis_chart_enabled
            self.settings_manager.update(analysis_chart_enabled=not current)
            label = f"График → {'ON' if not current else 'OFF'}"
        elif action == "sig":
            current = self.settings_manager.settings.analysis_signal_trigger_enabled
            self.settings_manager.update(analysis_signal_trigger_enabled=not current)
            label = f"Триггер сигналов → {'ON' if not current else 'OFF'}"
        elif action == "ref":
            self.settings_manager.reload()
            label = "Обновлено"
        elif action.startswith("m:"):
            value = float(action[2:])
            self.settings_manager.update(analysis_min_liq_usd=value)
            label = f"Стандарт liq → ${value:,.0f}".replace(",", " ")
        elif action.startswith("maj:"):
            value = float(action[4:])
            self.settings_manager.update(analysis_major_min_liq_usd=value)
            label = f"Мейджор liq → ${value:,.0f}".replace(",", " ")
        elif action.startswith("alt:"):
            value = float(action[4:])
            self.settings_manager.update(analysis_alt_min_liq_usd=value)
            label = f"Альт liq → ${value:,.0f}".replace(",", " ")
        elif action.startswith("oi:"):
            value = float(action[3:])
            self.settings_manager.update(analysis_min_oi_usd=value)
            label = (
                f"Мин. OI → ${value:,.0f}".replace(",", " ")
                if value > 0
                else "Мин. OI → любой"
            )
        elif action.startswith("p:"):
            value = float(action[2:])
            self.settings_manager.update(analysis_min_price_move_pct=value)
            label = (
                f"Движение цены → ≥{value:g}%"
                if value > 0
                else "Движение цены → любое"
            )
        elif action.startswith("tr:"):
            value = float(action[3:])
            self.settings_manager.update(analysis_min_trend_pct=value)
            label = f"Тренд → ≥{value:g}%"
        elif action.startswith("cf:"):
            value = float(action[3:])
            self.settings_manager.update(analysis_min_confidence=value)
            label = f"Conf → ≥{value:.0f}%"
        elif action.startswith("dl:"):
            value = int(action[3:])
            self.settings_manager.update(analysis_delay_seconds=value)
            label = f"Delay → {value}с"
        elif action.startswith("mh:"):
            value = int(action[3:])
            self.settings_manager.update(analysis_max_per_hour=value)
            label = f"Макс/ч → {value}"
        elif action.startswith("cd:"):
            value = int(action[3:])
            self.settings_manager.update(analysis_cooldown_seconds=value)
            label = f"Cooldown → {value}с"
        else:
            await query.answer("Неизвестное действие.", show_alert=True)
            return True

        await query.answer(f"✅ {label}", show_alert=False)
        await self._safe_edit_message_text(
            query,
            self._build_analysis_panel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._analysis_keyboard(),
        )
        return True

    def _reply_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [
                    KeyboardButton(self._signals_toggle_button_label()),
                    KeyboardButton("🎛 Каналы"),
                ],
                [KeyboardButton("💧 Ликвидация"), KeyboardButton("🧠 Анализ")],
                [KeyboardButton("📐 Ручной анализ"), KeyboardButton("🔧 Настройки")],
                [KeyboardButton("📊 Биржи"), KeyboardButton("📋 Команды")],
            ],
            resize_keyboard=True,
        )

    def _exchanges_keyboard(self) -> InlineKeyboardMarkup:
        s = self.settings_manager.settings
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    self._mark(f"Binance {'ON' if s.enabled_binance else 'OFF'}", s.enabled_binance),
                    callback_data="toggle_binance",
                ),
                InlineKeyboardButton(
                    self._mark(f"Bybit {'ON' if s.enabled_bybit else 'OFF'}", s.enabled_bybit),
                    callback_data="toggle_bybit",
                ),
            ],
        ])

    def _settings_keyboard(self) -> InlineKeyboardMarkup:
        s = self.settings_manager.settings
        signals_btn = (
            "🔔 Сигналы ON" if s.signals_enabled else "🔕 Сигналы OFF"
        )
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎛 Каналы", callback_data="open_channels")],
            [
                InlineKeyboardButton(
                    self._mark(
                        f"Готовый вход {'ON' if s.actionable_signals_only else 'OFF'}",
                        s.actionable_signals_only,
                    ),
                    callback_data="toggle_actionable",
                ),
            ],
            [InlineKeyboardButton(signals_btn, callback_data="toggle_signals")],
            [
                InlineKeyboardButton(self._mark("1м", s.oi_period_minutes == 1), callback_data="set_period:1"),
                InlineKeyboardButton(self._mark("5м", s.oi_period_minutes == 5), callback_data="set_period:5"),
                InlineKeyboardButton(self._mark("15м", s.oi_period_minutes == 15), callback_data="set_period:15"),
                InlineKeyboardButton(self._mark("30м", s.oi_period_minutes == 30), callback_data="set_period:30"),
            ],
            [
                InlineKeyboardButton(self._mark("OI+0.5%", s.oi_rise_percent == 0.5), callback_data="set_oi_rise:0.5"),
                InlineKeyboardButton(self._mark("+1%", s.oi_rise_percent == 1.0), callback_data="set_oi_rise:1.0"),
                InlineKeyboardButton(self._mark("+3%", s.oi_rise_percent == 3.0), callback_data="set_oi_rise:3.0"),
                InlineKeyboardButton(self._mark("+5%", s.oi_rise_percent == 5.0), callback_data="set_oi_rise:5.0"),
                InlineKeyboardButton(self._mark("+10%", s.oi_rise_percent == 10.0), callback_data="set_oi_rise:10.0"),
            ],
            [
                InlineKeyboardButton(self._mark("OI-0.5%", s.oi_drop_percent == 0.5), callback_data="set_oi_drop:0.5"),
                InlineKeyboardButton(self._mark("-1%", s.oi_drop_percent == 1.0), callback_data="set_oi_drop:1.0"),
                InlineKeyboardButton(self._mark("-3%", s.oi_drop_percent == 3.0), callback_data="set_oi_drop:3.0"),
                InlineKeyboardButton(self._mark("-5%", s.oi_drop_percent == 5.0), callback_data="set_oi_drop:5.0"),
                InlineKeyboardButton(self._mark("-10%", s.oi_drop_percent == 10.0), callback_data="set_oi_drop:10.0"),
            ],
            [
                InlineKeyboardButton(self._mark("🟢+0.5%", s.price_rise_percent == 0.5), callback_data="set_price_rise:0.5"),
                InlineKeyboardButton(self._mark("+1%", s.price_rise_percent == 1.0), callback_data="set_price_rise:1.0"),
                InlineKeyboardButton(self._mark("+2%", s.price_rise_percent == 2.0), callback_data="set_price_rise:2.0"),
                InlineKeyboardButton(self._mark("+3%", s.price_rise_percent == 3.0), callback_data="set_price_rise:3.0"),
                InlineKeyboardButton(self._mark("+5%", s.price_rise_percent == 5.0), callback_data="set_price_rise:5.0"),
            ],
            [
                InlineKeyboardButton(self._mark("🔴-0.5%", s.price_drop_percent == 0.5), callback_data="set_price_drop:0.5"),
                InlineKeyboardButton(self._mark("-1%", s.price_drop_percent == 1.0), callback_data="set_price_drop:1.0"),
                InlineKeyboardButton(self._mark("-2%", s.price_drop_percent == 2.0), callback_data="set_price_drop:2.0"),
                InlineKeyboardButton(self._mark("-3%", s.price_drop_percent == 3.0), callback_data="set_price_drop:3.0"),
                InlineKeyboardButton(self._mark("-5%", s.price_drop_percent == 5.0), callback_data="set_price_drop:5.0"),
            ],
            [
                InlineKeyboardButton(self._mark("OI 80k", s.min_open_interest == 80000), callback_data="set_min_oi:80000"),
                InlineKeyboardButton(self._mark("150k", s.min_open_interest == 150000), callback_data="set_min_oi:150000"),
                InlineKeyboardButton(self._mark("300k", s.min_open_interest == 300000), callback_data="set_min_oi:300000"),
            ],
            [
                InlineKeyboardButton(self._mark("Приток 25k", s.min_oi_change_usd == 25000), callback_data="set_min_oi_change:25000"),
                InlineKeyboardButton(self._mark("45k", s.min_oi_change_usd == 45000), callback_data="set_min_oi_change:45000"),
                InlineKeyboardButton(self._mark("100k", s.min_oi_change_usd == 100000), callback_data="set_min_oi_change:100000"),
            ],
            [
                InlineKeyboardButton(self._mark("Score≥1", s.min_signal_score == 1), callback_data="set_min_score:1"),
                InlineKeyboardButton(self._mark("≥2", s.min_signal_score == 2), callback_data="set_min_score:2"),
                InlineKeyboardButton(self._mark("≥3", s.min_signal_score == 3), callback_data="set_min_score:3"),
            ],
            [
                InlineKeyboardButton(self._mark("CD 30с", s.signal_cooldown_seconds == 30), callback_data="set_cooldown:30"),
                InlineKeyboardButton(self._mark("60с", s.signal_cooldown_seconds == 60), callback_data="set_cooldown:60"),
                InlineKeyboardButton(self._mark("120с", s.signal_cooldown_seconds == 120), callback_data="set_cooldown:120"),
            ],
            [
                InlineKeyboardButton(self._mark("Top50", s.top_n_symbols == 50), callback_data="set_top:50"),
                InlineKeyboardButton(self._mark("Top100", s.top_n_symbols == 100), callback_data="set_top:100"),
                InlineKeyboardButton(self._mark("Все", s.top_n_symbols is None), callback_data="set_top:0"),
            ],
            [
                InlineKeyboardButton(self._mark("🔥≤1", s.priority_score_max == 1), callback_data="set_priority:1"),
                InlineKeyboardButton(self._mark("≤2", s.priority_score_max == 2), callback_data="set_priority:2"),
                InlineKeyboardButton(self._mark("≤3", s.priority_score_max == 3), callback_data="set_priority:3"),
            ],
            [
                InlineKeyboardButton(self._mark(f"Binance {'ON' if s.enabled_binance else 'OFF'}", s.enabled_binance), callback_data="toggle_binance"),
                InlineKeyboardButton(self._mark(f"Bybit {'ON' if s.enabled_bybit else 'OFF'}", s.enabled_bybit), callback_data="toggle_bybit"),
            ],
            [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_settings")],
            [
                InlineKeyboardButton("💧 Ликвидация", callback_data="open_liq"),
                InlineKeyboardButton("🧠 Анализ", callback_data="open_analysis"),
            ],
        ])

    def _liquidation_keyboard(self) -> InlineKeyboardMarkup:
        s = self.settings_manager.settings
        on_btn = (
            "💧 Ликвидации ON" if s.liquidation_alerts_enabled else "💧 Ликвидации OFF"
        )
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(on_btn, callback_data="liq:on")],
            [
                InlineKeyboardButton(
                    self._mark("$10k", s.liquidation_min_usd == 10_000),
                    callback_data="liq:m:10000",
                ),
                InlineKeyboardButton(
                    self._mark("$20k", s.liquidation_min_usd == 20_000),
                    callback_data="liq:m:20000",
                ),
                InlineKeyboardButton(
                    self._mark("$35k", s.liquidation_min_usd == 35_000),
                    callback_data="liq:m:35000",
                ),
                InlineKeyboardButton(
                    self._mark("$50k", s.liquidation_min_usd == 50_000),
                    callback_data="liq:m:50000",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("альт $10k", s.liquidation_alt_min_usd == 10_000),
                    callback_data="liq:a:10000",
                ),
                InlineKeyboardButton(
                    self._mark("$20k", s.liquidation_alt_min_usd == 20_000),
                    callback_data="liq:a:20000",
                ),
                InlineKeyboardButton(
                    self._mark("mid $10k", s.liquidation_mid_min_usd == 10_000),
                    callback_data="liq:d:10000",
                ),
                InlineKeyboardButton(
                    self._mark("$35k", s.liquidation_mid_min_usd == 35_000),
                    callback_data="liq:d:35000",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("Tier ON", s.liquidation_tier_enabled),
                    callback_data="liq:tier",
                ),
                InlineKeyboardButton(
                    self._mark("Все монеты", s.liquidation_all_symbols),
                    callback_data="liq:all",
                ),
                InlineKeyboardButton(
                    self._mark("Разворот", s.liquidation_show_reversal_hint),
                    callback_data="liq:hint",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("CD 30с", s.liquidation_cooldown_seconds == 30),
                    callback_data="liq:cd:30",
                ),
                InlineKeyboardButton(
                    self._mark("60с", s.liquidation_cooldown_seconds == 60),
                    callback_data="liq:cd:60",
                ),
                InlineKeyboardButton(
                    self._mark("120с", s.liquidation_cooldown_seconds == 120),
                    callback_data="liq:cd:120",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("окно 1с", s.liquidation_burst_window_seconds == 1.0),
                    callback_data="liq:win:1",
                ),
                InlineKeyboardButton(
                    self._mark("2с", s.liquidation_burst_window_seconds == 2.0),
                    callback_data="liq:win:2",
                ),
                InlineKeyboardButton(
                    self._mark("5с", s.liquidation_burst_window_seconds == 5.0),
                    callback_data="liq:win:5",
                ),
            ],
            [InlineKeyboardButton("🔄 Обновить", callback_data="liq:ref")],
        ])

    def _analysis_keyboard(self) -> InlineKeyboardMarkup:
        s = self.settings_manager.settings
        on_btn = "🧠 Анализ ON" if s.analysis_enabled else "🧠 Анализ OFF"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(on_btn, callback_data="an:on")],
            [
                InlineKeyboardButton(
                    self._mark("liq $10k", s.analysis_min_liq_usd == 10_000),
                    callback_data="an:m:10000",
                ),
                InlineKeyboardButton(
                    self._mark("$20k", s.analysis_min_liq_usd == 20_000),
                    callback_data="an:m:20000",
                ),
                InlineKeyboardButton(
                    self._mark("$35k", s.analysis_min_liq_usd == 35_000),
                    callback_data="an:m:35000",
                ),
                InlineKeyboardButton(
                    self._mark("$50k", s.analysis_min_liq_usd == 50_000),
                    callback_data="an:m:50000",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("мейдж $10k", s.analysis_major_min_liq_usd == 10_000),
                    callback_data="an:maj:10000",
                ),
                InlineKeyboardButton(
                    self._mark("$35k", s.analysis_major_min_liq_usd == 35_000),
                    callback_data="an:maj:35000",
                ),
                InlineKeyboardButton(
                    self._mark("альт $10k", s.analysis_alt_min_liq_usd == 10_000),
                    callback_data="an:alt:10000",
                ),
                InlineKeyboardButton(
                    self._mark("$20k", s.analysis_alt_min_liq_usd == 20_000),
                    callback_data="an:alt:20000",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("OI 500k", s.analysis_min_oi_usd == 500_000),
                    callback_data="an:oi:500000",
                ),
                InlineKeyboardButton(
                    self._mark("1M", s.analysis_min_oi_usd == 1_000_000),
                    callback_data="an:oi:1000000",
                ),
                InlineKeyboardButton(
                    self._mark("OI любой", s.analysis_min_oi_usd == 0),
                    callback_data="an:oi:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("цена ≥2%", s.analysis_min_price_move_pct == 2.0),
                    callback_data="an:p:2",
                ),
                InlineKeyboardButton(
                    self._mark("≥3%", s.analysis_min_price_move_pct == 3.0),
                    callback_data="an:p:3",
                ),
                InlineKeyboardButton(
                    self._mark("любая", s.analysis_min_price_move_pct == 0),
                    callback_data="an:p:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("тренд 2%", s.analysis_min_trend_pct == 2.0),
                    callback_data="an:tr:2",
                ),
                InlineKeyboardButton(
                    self._mark("3%", s.analysis_min_trend_pct == 3.0),
                    callback_data="an:tr:3",
                ),
                InlineKeyboardButton(
                    self._mark("1.5%", s.analysis_min_trend_pct == 1.5),
                    callback_data="an:tr:1.5",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("conf 42%", s.analysis_min_confidence == 42.0),
                    callback_data="an:cf:42",
                ),
                InlineKeyboardButton(
                    self._mark("48%", s.analysis_min_confidence == 48.0),
                    callback_data="an:cf:48",
                ),
                InlineKeyboardButton(
                    self._mark("55%", s.analysis_min_confidence == 55.0),
                    callback_data="an:cf:55",
                ),
                InlineKeyboardButton(
                    self._mark("58%", s.analysis_min_confidence == 58.0),
                    callback_data="an:cf:58",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("delay 60с", s.analysis_delay_seconds == 60),
                    callback_data="an:dl:60",
                ),
                InlineKeyboardButton(
                    self._mark("90с", s.analysis_delay_seconds == 90),
                    callback_data="an:dl:90",
                ),
                InlineKeyboardButton(
                    self._mark("120с", s.analysis_delay_seconds == 120),
                    callback_data="an:dl:120",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("3/ч", s.analysis_max_per_hour == 3),
                    callback_data="an:mh:3",
                ),
                InlineKeyboardButton(
                    self._mark("5/ч", s.analysis_max_per_hour == 5),
                    callback_data="an:mh:5",
                ),
                InlineKeyboardButton(
                    self._mark("6/ч", s.analysis_max_per_hour == 6),
                    callback_data="an:mh:6",
                ),
                InlineKeyboardButton(
                    self._mark("CD 1ч", s.analysis_cooldown_seconds == 3600),
                    callback_data="an:cd:3600",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._mark("Сигналы", s.analysis_signal_trigger_enabled),
                    callback_data="an:sig",
                ),
                InlineKeyboardButton(
                    self._mark("График", s.analysis_chart_enabled),
                    callback_data="an:chart",
                ),
                InlineKeyboardButton(
                    self._mark("Без альтов", s.analysis_skip_alt_tier),
                    callback_data="an:skalt",
                ),
            ],
            [InlineKeyboardButton("🔄 Обновить", callback_data="an:ref")],
        ])

    @staticmethod
    def _scanner_direction_bits(signal: Signal) -> tuple[str, str]:
        """Эмодзи + подпись направления сканера (не всегда = вход в сделку)."""
        is_long = signal.side == "long"
        st = signal.signal_type
        if st in {"reversal_pump", "reversal_dump"}:
            return "↩️", "разворот ↑" if is_long else "разворот ↓"
        if st in {"impulse_pump", "impulse_dump"}:
            return "⚡", "импульс ↑" if is_long else "импульс ↓"
        if st in {"trend_pump", "trend_dump"}:
            return "📊", "тренд ↑" if is_long else "тренд ↓"
        if st in {"vertical_pump", "vertical_dump"}:
            return "🚨", "вертикаль ↑" if is_long else "вертикаль ↓"
        return ("🟢", "LONG") if is_long else ("🔴", "SHORT")

    @staticmethod
    def _signal_type_header(signal: Signal, *, inline: bool = False) -> str:
        flash_tier = signal.details.get("flash_tier")
        labels = {
            "vertical_pump": "🚨 ВЕРТИКАЛЬНЫЙ ПАМП",
            "vertical_dump": "🚨 ВЕРТИКАЛЬНЫЙ СЛИВ",
            "liq_cascade_pump": "💧 LIQ-CASCADE LONG",
            "liq_cascade_dump": "💧 LIQ-CASCADE SHORT",
            "reversal_pump": "↩️ РАЗВОРОТ ВВЕРХ",
            "reversal_dump": "↩️ РАЗВОРОТ ВНИЗ",
            "impulse_pump": "📈 ИМПУЛЬС ВВЕРХ",
            "impulse_dump": "📉 ИМПУЛЬС ВНИЗ",
            "trend_pump": "📈 ТРЕНД → ОТСКОК",
            "trend_dump": "📉 ТРЕНД → СЛИВ",
            "mega_pump": "🚀 МЕГА-ПАМП",
            "mega_dump": "💥 МЕГА-ДАМП",
            "pulse_pump": "⚡ РАННИЙ ПУЛЬС",
            "pulse_dump": "⚡ РАННИЙ ПУЛЬС",
            "short_squeeze": "💥 ШОРТ-СКВИЗ",
            "pump": "📈 ПАМП + OI",
            "dump": "📉 ДАМП + OI",
            "oi_pump": "📈 РОСТ OI",
            "oi_dump": "📉 ПАДЕНИЕ OI",
        }
        label = labels.get(signal.signal_type, "")
        if flash_tier:
            tier_text = f"+{flash_tier:g}%" if signal.side == "long" else f"{-flash_tier:g}%"
            label = f"{label} {tier_text}"
        if not label:
            return ""
        return label if inline else f"<b>{label}</b>\n"

    @staticmethod
    def _symbol_link_and_copy(signal: Signal, *, inline: bool = False, copy_only: bool = False) -> str:
        """Тикер для caption: одна строка <code> для tap-to-copy (CoinGlass — в кнопках)."""
        sym = signal.symbol
        if copy_only:
            return f"<code>{sym}</code>"
        link = f'<a href="{signal.link}"><b>{sym}</b></a>'
        if inline:
            return f"{link} <code>{sym}</code>"
        return f"{link}\n<code>{sym}</code>"

    def _format_vertical_breakout_message(self, signal: Signal, *, compact: bool = False) -> str:
        if compact:
            exchange_key = "bybit" if "bybit" in signal.exchange.lower() else "binance"
            exchange_emoji, exchange_name = EXCHANGE_LABEL[exchange_key]
            is_long = signal.side == "long"
            side_emoji = "🟢" if is_long else "🔴"
            spike_pct = signal.details.get("spike_percent", signal.price_change_percent)
            spike_text = f"+{float(spike_pct):.2f}%" if isinstance(spike_pct, (int, float)) else str(spike_pct)
            oi_usd = format_oi_usd(signal.oi_change_usd)
            title = "🚨 ВЕРТ. ПАМП" if is_long else "🚨 ВЕРТ. СЛИВ"
            if signal.signal_type in {"impulse_pump", "impulse_dump"}:
                win = signal.details.get("impulse_window_min", signal.oi_period_minutes)
                title = f"📈 ИМПУЛЬС {win}м" if is_long else f"📉 ИМПУЛЬС {win}м"
            elif signal.signal_type in {"trend_pump", "trend_dump"}:
                prior = signal.details.get("trend_prior_pct", "—")
                title = f"📈 ТРЕНД→отскок ({prior}%)" if is_long else f"📉 ТРЕНД→слив ({prior}%)"
            return (
                f"<b>{title}</b> · {exchange_emoji} {exchange_name} {signal.oi_period_minutes}м\n"
                f"{side_emoji} {self._symbol_link_and_copy(signal, copy_only=True)}\n"
                f"взлёт {spike_text} · OI {abs(signal.oi_change_percent):.2f}% ({oi_usd})\n"
            )

        exchange_key = "bybit" if "bybit" in signal.exchange.lower() else "binance"
        exchange_emoji, exchange_name = EXCHANGE_LABEL[exchange_key]
        is_long = signal.side == "long"
        side_emoji = "🟢" if is_long else "🔴"
        side_label = "LONG" if is_long else "SHORT"

        flat_pct = signal.details.get("flat_range_percent", "—")
        spike_pct = signal.details.get("spike_percent", signal.price_change_percent)
        velocity = signal.details.get("velocity_ratio", "—")
        flat_min = int(signal.details.get("consolidation_minutes", 25))
        spike_min = signal.oi_period_minutes

        if isinstance(spike_pct, (int, float)):
            spike_text = f"+{spike_pct:.2f}%" if spike_pct > 0 else f"{spike_pct:.2f}%"
        else:
            spike_text = str(spike_pct)

        oi_pct = abs(signal.oi_change_percent)
        oi_usd = format_oi_usd(signal.oi_change_usd)

        title = "🚨 <b>ВЕРТИКАЛЬНЫЙ ПАМП</b>" if is_long else "🚨 <b>ВЕРТИКАЛЬНЫЙ СЛИВ</b>"
        if signal.signal_type in {"impulse_pump", "impulse_dump"}:
            win = signal.details.get("impulse_window_min", signal.oi_period_minutes)
            title = f"📈 <b>ИМПУЛЬС ВВЕРХ ({win}м)</b>" if is_long else f"📉 <b>ИМПУЛЬС ВНИЗ ({win}м)</b>"
        elif signal.signal_type in {"trend_pump", "trend_dump"}:
            prior = signal.details.get("trend_prior_pct", "—")
            leg = signal.details.get("trend_leg_pct", spike_pct)
            title = (
                f"📈 <b>ТРЕНД → ОТСКОК</b> (ход {prior}%)"
                if is_long
                else f"📉 <b>ТРЕНД → СЛИВ</b> (тренд +{prior}% → {leg}%)"
            )
        subtitle = "⚡ <b>Выход из проторговки</b> (вне порогов)"
        if signal.signal_type in {"trend_pump", "trend_dump"}:
            bits = ["тренд → перегрев → импульс"]
            if signal.details.get("oi_unwind"):
                bits.append("OI↓ unwind")
            if signal.details.get("liq_long_usd", 0) >= 1:
                bits.append(f"liq ${float(signal.details.get('liq_long_usd', 0)):,.0f}")
            subtitle = "📊 <b>" + " · ".join(bits) + "</b>"
        return (
            f"{title}\n"
            f"{subtitle}\n"
            f"{exchange_emoji} <b>{exchange_name} – {spike_min}м</b>\n"
            f"{side_emoji} <b>{side_label}</b>\n"
            f"{self._symbol_link_and_copy(signal)}\n\n"
            f"📊 Флет <b>{flat_min}м</b>: диапазон <b>{flat_pct}%</b>\n"
            f"🚀 Взлёт за <b>{spike_min}м</b>: <b>{spike_text}</b>\n"
            f"⚡ Ускорение: <b>{velocity}×</b> к флету\n"
            f"📈 OI: <b>{oi_pct:.2f}%</b> (<b>{oi_usd}</b>)\n\n"
            f"<i>Ранний вход в вертикаль, как на графике</i>\n\n"
            f"{self._market_structure_section(signal)}"
            f"{self._bybit_real_data_section(signal)}"
        )

    def _format_signal_message(self, signal: Signal, *, is_priority: bool = False, compact: bool = False) -> str:
        if compact:
            return self._format_signal_message_compact(signal, is_priority=is_priority)

        exchange_key = "bybit" if "bybit" in signal.exchange.lower() else "binance"
        exchange_emoji, exchange_name = EXCHANGE_LABEL[exchange_key]
        period_label = f"{signal.oi_period_minutes}м"

        is_long = signal.side == "long"
        side_emoji = "🟢" if is_long else "🔴"
        side_label = "LONG" if is_long else "SHORT"

        if signal.oi_direction == "up":
            oi_verb = "вырос"
            oi_icon = "📈"
        elif signal.oi_direction == "down":
            oi_verb = "снизился"
            oi_icon = "📉"
        else:
            oi_verb = "изменился"
            oi_icon = "📊"

        oi_pct = abs(signal.oi_change_percent)
        oi_usd = format_oi_usd(signal.oi_change_usd)

        price_pct = signal.price_change_percent or 0.0
        if price_pct > 0:
            price_text = f"+{price_pct:.2f}%"
        else:
            price_text = f"{price_pct:.2f}%"

        header = ""
        if is_priority:
            header = "🔥 <b>РАННИЙ СИГНАЛ</b>\n"
        type_header = self._signal_type_header(signal)

        return (
            f"{header}"
            f"{type_header}"
            f"{exchange_emoji} <b>{exchange_name} – {period_label}</b>\n"
            f"{side_emoji} <b>{side_label}</b>\n"
            f"{self._symbol_link_and_copy(signal)}\n"
            f"{oi_icon} ОИ {oi_verb} на <b>{oi_pct:.2f}%</b> (<b>{oi_usd}</b>)\n"
            f"{side_emoji} 💲 Изменение цены: <b>{price_text}</b>\n"
            f"⏱ Ранность: <b>{signal.signal_score}</b>/10 "
            f"(1=рано, 10=поздно) | сегодня: <b>{signal.signals_today}</b>\n\n"
            f"{self._market_structure_section(signal)}"
            f"{self._bybit_real_data_section(signal)}"
        )

    def _format_signal_message_compact(self, signal: Signal, *, is_priority: bool = False) -> str:
        exchange_key = "bybit" if "bybit" in signal.exchange.lower() else "binance"
        exchange_emoji, exchange_name = EXCHANGE_LABEL[exchange_key]
        dir_emoji, dir_label = self._scanner_direction_bits(signal)
        price_pct = signal.price_change_percent or 0.0
        price_text = f"+{price_pct:.2f}%" if price_pct > 0 else f"{price_pct:.2f}%"
        oi_usd = format_oi_usd(signal.oi_change_usd)

        header_bits: list[str] = []
        if is_priority:
            header_bits.append("🔥")
        type_label = self._signal_type_header(signal, inline=True)
        if type_label:
            header_bits.append(type_label)
        header_bits.append(
            f"{exchange_emoji} {exchange_name} {signal.oi_period_minutes}м · "
            f"{dir_emoji} {dir_label}"
        )

        lines: list[str] = [" · ".join(header_bits)]
        lines.append(self._symbol_link_and_copy(signal, copy_only=True))

        prior = signal.details.get("reversal_prior_move_pct")
        leg = signal.details.get("reversal_leg_pct")
        impulse_move = signal.details.get("impulse_move_pct")

        context_bits: list[str] = []
        if impulse_move is not None:
            context_bits.append(
                f"движение <b>{float(impulse_move):+.1f}%</b> за {signal.oi_period_minutes}м"
            )
        elif prior is not None and leg is not None:
            context_bits.append(
                f"↩️ <b>{float(prior):+.1f}%</b> → <b>{float(leg):+.1f}%</b>"
            )
        else:
            context_bits.append(f"цена <b>{price_text}</b>")

        context_bits.append(
            f"OI <b>{abs(signal.oi_change_percent):.2f}%</b> ({oi_usd})"
        )
        context_bits.append(f"⏱ <b>{signal.signal_score}</b>/10")

        bybit = format_bybit_real_data_compact(signal.details)
        if bybit:
            context_bits.append(bybit.replace("📊 ", ""))

        ms = format_market_structure_compact(
            signal.details.get("market_structure"),
            warnings_only=True,
        )
        if ms:
            context_bits.append(ms.replace("📐 ", ""))

        lines.append(" · ".join(context_bits))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _market_structure_section(signal: Signal) -> str:
        block = format_market_structure_block(signal.details.get("market_structure"))
        return f"{block}\n" if block else ""

    @staticmethod
    def _bybit_real_data_section(signal: Signal) -> str:
        if "bybit" not in signal.exchange.lower():
            return ""
        block = format_bybit_real_data_block(signal.details)
        return f"{block}\n" if block else ""

    def _is_admin(self, update: Update) -> bool:
        user_id = None
        if update.effective_user:
            user_id = update.effective_user.id
        return user_id == self.config.telegram_admin_id
