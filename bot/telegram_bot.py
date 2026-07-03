from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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
from telegram.error import BadRequest, RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Config
from .models import Signal
from .scanner_engine import format_oi_usd, SignalEngine
from .set_parser import SET_HELP, parse_set_command
from .settings import SettingsManager
from .test_signals import build_test_signals

logger = logging.getLogger(__name__)

EXCHANGE_LABEL = {
    "binance": ("🟡", "Binance"),
    "bybit": ("⚫", "ByBit"),
}


class TelegramBot:
    def __init__(self, config: Config, settings_manager: SettingsManager) -> None:
        self.config = config
        self.settings_manager = settings_manager
        self.scanner: SignalEngine | None = None
        self._last_send_time: dict[int, float] = {}
        self._minute_send_times: dict[int, list[float]] = {}
        self._send_lock = asyncio.Lock()
        self.application: Application | None = None
        self._run_task: asyncio.Task | None = None
        self.redis: redis.Redis | None = None

    async def start(self) -> None:
        self.application = Application.builder().token(self.config.telegram_token).build()
        self.application.add_handler(CommandHandler("start", self.on_start))
        self.application.add_handler(CommandHandler("help", self.on_help))
        self.application.add_handler(CommandHandler("status", self.on_status))
        self.application.add_handler(CommandHandler("settings", self.on_settings))
        self.application.add_handler(CommandHandler("history", self.on_history))
        self.application.add_handler(CommandHandler("set", self.on_set))
        self.application.add_handler(CommandHandler("test", self.on_test))
        self.application.add_handler(CommandHandler("scan", self.on_scan))
        self.application.add_handler(CommandHandler("pause", self.on_pause))
        self.application.add_handler(CommandHandler("resume", self.on_resume))
        self.application.add_handler(CallbackQueryHandler(self.on_callback_query))
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
                    f"{','.join(str(m) for m in s.flash_window_minutes)}м"
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

        logger.info("Telegram bot started")

    async def stop(self) -> None:
        if self.application is None:
            return
        await self.application.updater.stop_polling()
        await self.application.stop()
        await self.application.shutdown()
        if self.redis is not None:
            try:
                await self.redis.close()
            except Exception:
                pass

    async def _send_to_chat(
        self,
        chat_id: int,
        message: str,
        keyboard: InlineKeyboardMarkup,
        is_priority: bool,
    ) -> bool:
        if self.application is None:
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

                    if (
                        is_priority
                        and settings.pin_in_private_chat
                        and chat_id > 0
                    ):
                        try:
                            await self.application.bot.pin_chat_message(
                                chat_id=chat_id,
                                message_id=sent.message_id,
                                disable_notification=True,
                            )
                        except Exception as exc:
                            logger.warning("Pin failed for chat %s: %s", chat_id, exc)
                    return True
                except RetryAfter as exc:
                    if attempt == 0:
                        logger.warning("Telegram flood control chat %s, wait %ss", chat_id, exc.retry_after)
                        await asyncio.sleep(float(exc.retry_after) + 1.0)
                        continue
                    logger.warning("Telegram flood control chat %s, message dropped", chat_id)
                    return False
                except BadRequest as exc:
                    if "Chat not found" in str(exc):
                        logger.warning(
                            "Chat not found for id %s — проверьте TELEGRAM_ALERT_CHAT_ID",
                            chat_id,
                        )
                    else:
                        logger.error("Telegram BadRequest for chat %s: %s", chat_id, exc)
                    return False
                except Exception:
                    logger.exception("Failed to send signal to chat %s", chat_id)
                    return False
        return False

    async def dispatch_signal(self, signal: Signal, *, skip_dedupe: bool = False) -> None:
        if self.application is None:
            return
        if not skip_dedupe and not self.settings_manager.settings.signals_enabled:
            return

        priority_max = self.settings_manager.settings.priority_score_max
        is_vertical = signal.signal_type in {"vertical_pump", "vertical_dump"}
        is_priority = (
            is_vertical
            or signal.signal_score <= priority_max
            or signal.signal_type in {"mega_pump", "mega_dump", "short_squeeze"}
        )
        if is_vertical:
            message = self._format_vertical_breakout_message(signal)
        else:
            message = self._format_signal_message(signal, is_priority=is_priority)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 CoinGlass", url=signal.link)],
        ])
        notify_chat_id = self.config.notification_chat_id

        key = (
            f"last_breakout:{signal.exchange}:{signal.symbol}"
            if is_vertical
            else f"last_signal:{signal.exchange}:{signal.symbol}"
        )
        should_send = True
        if not skip_dedupe and self.redis is not None:
            try:
                last = await self.redis.get(key)
                if last is not None:
                    cooldown = (
                        self.settings_manager.settings.breakout_cooldown_seconds
                        if is_vertical
                        else self.settings_manager.settings.signal_cooldown_seconds
                    )
                    if time.time() - float(last) < cooldown:
                        should_send = False
            except Exception:
                should_send = True

        if not should_send:
            return

        sent_any = await self._send_to_chat(notify_chat_id, message, keyboard, is_priority)

        if sent_any and not skip_dedupe and self.redis is not None:
            try:
                ex = (
                    self.settings_manager.settings.breakout_cooldown_seconds + 5
                    if is_vertical
                    else self.settings_manager.settings.signal_cooldown_seconds + 5
                )
                await self.redis.set(
                    key,
                    str(time.time()),
                    ex=ex,
                )
            except Exception:
                logger.exception("Failed to update last_signal in Redis")

        if self.redis is not None:
            try:
                raw = json.dumps(asdict(signal), ensure_ascii=False)
                await self.redis.rpush("signals", raw)
                await self.redis.ltrim("signals", -500, -1)
            except Exception:
                logger.exception("Failed to persist signal to Redis")

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

    async def on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            self._build_settings_panel_text(),
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
        await self._set_signals_enabled(update, enabled=False)

    async def on_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await self._set_signals_enabled(update, enabled=True)

    async def _set_signals_enabled(self, update: Update, *, enabled: bool) -> None:
        self.settings_manager.update(signals_enabled=enabled)
        if enabled:
            text = (
                "▶️ <b>Сигналы включены</b>\n"
                "Уведомления снова приходят при срабатывании порогов."
            )
        else:
            text = (
                "⏸ <b>Сигналы остановлены</b>\n"
                "Уведомления не приходят. Сканер продолжает собирать данные в фоне.\n"
                "Нажмите <b>▶️ Старт</b> или /resume, когда будете готовы."
            )
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=self._reply_keyboard(),
        )

    def _signals_status_line(self) -> str:
        if self.settings_manager.settings.signals_enabled:
            return "🔔 Сигналы: <b>ВКЛ</b> — уведомления приходят"
        return "🔕 Сигналы: <b>ВЫКЛ</b> — уведомления приостановлены"

    def _signals_toggle_button_label(self) -> str:
        if self.settings_manager.settings.signals_enabled:
            return "⏸ Стоп"
        return "▶️ Старт"

    async def on_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update) or update.message is None:
            return
        text = (update.message.text or "").strip()
        if text in {"⏸ Стоп", "Стоп", "⏸ Стоп сигналы"}:
            await self._set_signals_enabled(update, enabled=False)
        elif text in {"▶️ Старт", "Старт", "▶️ Старт сигналы"}:
            await self._set_signals_enabled(update, enabled=True)
        elif text in {"📊 Биржи", "Биржи"}:
            await update.message.reply_text(
                self._build_exchanges_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._exchanges_keyboard(),
            )
        elif text in {"⚙ Настройки", "🔧 Настройки", "Настройки"}:
            await update.message.reply_text(
                self._build_settings_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._settings_keyboard(),
            )
        elif text in {"📈 Статус", "Статус"}:
            await update.message.reply_text(
                self._build_settings_panel_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._reply_keyboard(),
            )
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
            "/scan — диагностика сканера\n"
            "/pause — остановить уведомления\n"
            "/resume — возобновить уведомления\n"
            "/history [N] — последние N сигналов (нужен Redis)\n"
            "/help — эта справка\n\n"
            "🟢 LONG = рост цены | 🔴 SHORT = падение\n"
            "🔥 score 1–2 = приоритет (звук + закреп в личке)\n\n"
            "Все настройки применяются сразу."
        )

    def _build_exchanges_text(self) -> str:
        s = self.settings_manager.settings
        return (
            "<b>📊 Биржи</b> (применяется сразу)\n"
            f"Binance: {'✅ включена' if s.enabled_binance else '❌ выключена'}\n"
            f"Bybit: {'✅ включена' if s.enabled_bybit else '❌ выключена'}"
        )

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
            "<i>Кнопки задают минимум для всех режимов</i>\n\n"
            f"📅 LONG: <b>{s.long_period_minutes} мин</b> | SHORT: <b>{s.short_period_minutes} мин</b>\n"
            f"⚡ Пульс: <b>{s.pulse_period_minutes} мин</b> "
            f"(OI≥<b>{pulse_oi}</b>% / цена≥<b>{pulse_price}</b>%)\n"
            f"🚀 Мега: <b>{','.join(str(m) for m in s.flash_window_minutes)} мин</b> "
            f"(от <b>{mega_label}</b>)\n"
            f"📈 Рост OI: <b>≥ {s.oi_rise_percent}%</b> | 📉 Падение: <b>≥ {s.oi_drop_percent}%</b>\n"
            f"🟢 LONG цена: <b>≥ {s.price_rise_percent}%</b> | 🔴 SHORT: <b>≥ {s.price_drop_percent}%</b>\n"
            f"💰 Мин. OI: <b>{s.min_open_interest:,.0f}</b> | Приток OI: <b>{s.min_oi_change_usd:,.0f} $</b>\n"
            f"🏆 Топ монет: <b>{top_label}</b> | Ранность≥<b>{s.min_signal_score}</b>/10\n"
            f"🔥 Приоритет: ≤<b>{s.priority_score_max}</b>/10 | CD: <b>{s.signal_cooldown_seconds}с</b>\n"
            f"{self._exchange_effective_line('Binance', 'Binance')}\n"
            f"{self._exchange_effective_line('Bybit', 'Bybit')}\n"
            f"Binance: <b>{'ON' if s.enabled_binance else 'OFF'}</b> | "
            f"Bybit: <b>{'ON' if s.enabled_bybit else 'OFF'}</b>\n"
            f"🚨 Вертикальный памп: <b>{'ON' if s.breakout_enabled else 'OFF'}</b> "
            f"(флет ≤{s.breakout_max_flat_percent}% → +{s.breakout_min_spike_percent}% за {s.breakout_spike_minutes}м)\n\n"
            "<i>В уведомлении % — фактическое движение, не порог</i>\n"
            "Точная настройка: /set help"
        ).replace(",", " ")

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
        if not self._is_admin(update):
            await query.answer("Нет доступа.", show_alert=True)
            return

        payload = query.data or ""
        changed_label = ""

        handlers: dict[str, tuple[str, type, str]] = {
            "set_period:": ("oi_period_minutes", int, "Период"),
            "set_oi_rise:": ("oi_rise_percent", float, "Рост OI"),
            "set_oi_drop:": ("oi_drop_percent", float, "Падение OI"),
            "set_price_rise:": ("price_rise_percent", float, "Рост цены"),
            "set_price_drop:": ("price_drop_percent", float, "Падение цены"),
            "set_min_oi:": ("min_open_interest", float, "Мин. OI"),
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
            state = "включены" if not current else "остановлены"
            await query.answer(f"✅ Сигналы {state}", show_alert=False)
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
        else:
            await self._safe_edit_message_text(query, "Неизвестное действие.")

    def _reply_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton(self._signals_toggle_button_label())],
                [KeyboardButton("📊 Биржи"), KeyboardButton("🔧 Настройки")],
                [KeyboardButton("📋 Команды")],
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
                InlineKeyboardButton(self._mark("OI 50k", s.min_open_interest == 50000), callback_data="set_min_oi:50000"),
                InlineKeyboardButton(self._mark("100k", s.min_open_interest == 100000), callback_data="set_min_oi:100000"),
                InlineKeyboardButton(self._mark("500k", s.min_open_interest == 500000), callback_data="set_min_oi:500000"),
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
        ])

    def _signal_type_header(self, signal: Signal) -> str:
        flash_tier = signal.details.get("flash_tier")
        labels = {
            "vertical_pump": "🚨 ВЕРТИКАЛЬНЫЙ ПАМП",
            "vertical_dump": "🚨 ВЕРТИКАЛЬНЫЙ СЛИВ",
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
        return f"<b>{label}</b>\n" if label else ""

    def _format_vertical_breakout_message(self, signal: Signal) -> str:
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
        return (
            f"{title}\n"
            f"⚡ <b>Выход из проторговки</b> (вне порогов)\n"
            f"{exchange_emoji} <b>{exchange_name} – {spike_min}м</b>\n"
            f"{side_emoji} <b>{side_label}</b>\n"
            f"<a href=\"{signal.link}\"><b>{signal.symbol}</b></a>\n\n"
            f"📊 Флет <b>{flat_min}м</b>: диапазон <b>{flat_pct}%</b>\n"
            f"🚀 Взлёт за <b>{spike_min}м</b>: <b>{spike_text}</b>\n"
            f"⚡ Ускорение: <b>{velocity}×</b> к флету\n"
            f"📈 OI: <b>{oi_pct:.2f}%</b> (<b>{oi_usd}</b>)\n\n"
            f"<i>Ранний вход в вертикаль, как на графике</i>"
        )

    def _format_signal_message(self, signal: Signal, *, is_priority: bool = False) -> str:
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
            f"<a href=\"{signal.link}\"><b>{signal.symbol}</b></a>\n"
            f"{oi_icon} ОИ {oi_verb} на <b>{oi_pct:.2f}%</b> (<b>{oi_usd}</b>)\n"
            f"{side_emoji} 💲 Изменение цены: <b>{price_text}</b>\n"
            f"⏱ Ранность: <b>{signal.signal_score}</b>/10 "
            f"(1=рано, 10=поздно) | сегодня: <b>{signal.signals_today}</b>"
        )

    def _is_admin(self, update: Update) -> bool:
        user_id = None
        if update.effective_user:
            user_id = update.effective_user.id
        return user_id == self.config.telegram_admin_id
