from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from typing import Any

import redis.asyncio as redis
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .config import Config
from .models import Signal
from .settings import SettingsManager

logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self, config: Config, settings_manager: SettingsManager) -> None:
        self.config = config
        self.settings_manager = settings_manager
        self.application: Application | None = None
        self._run_task: asyncio.Task | None = None
        self.redis: redis.Redis | None = None

    async def start(self) -> None:
        self.application = Application.builder().token(self.config.telegram_token).build()
        self.application.add_handler(CommandHandler("start", self.on_start))
        # init redis (optional) from REDIS_URL env or default docker service name
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        try:
            self.redis = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            logger.info("Redis client initialized: %s", redis_url)
        except Exception:
            self.redis = None
            logger.info("Redis not available, continuing without persistence")
        self.application.add_handler(CommandHandler("help", self.on_help))
        self.application.add_handler(CommandHandler("status", self.on_status))
        self.application.add_handler(CommandHandler("settings", self.on_settings))
        self.application.add_handler(CallbackQueryHandler(self.on_callback_query))

        await self.application.initialize()
        await self.application.start()
        # start polling in background
        try:
            await self.application.updater.start_polling()
        except Exception:
            # fallback: run polling via run_polling in background task
            self._run_task = asyncio.create_task(self.application.run_polling())

        try:
            await self.application.bot.send_message(
                chat_id=self.config.telegram_admin_id,
                text="✅ Бот успешно запущен и готов к работе.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning("Failed to send startup welcome message: %s", exc)

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
                await self.redis.wait_closed()
            except Exception:
                pass

    async def dispatch_signal(self, signal: Signal) -> None:
        if self.application is None:
            return
        message = self._format_signal_message(signal)
        try:
            # Redis-based dedupe: skip send if last sent within cooldown
            should_send = True
            if self.redis is not None:
                try:
                    key = f"last_signal:{signal.exchange}:{signal.symbol}"
                    last = await self.redis.get(key)
                    if last is not None:
                        last_ts = float(last)
                        cooldown = self.settings_manager.settings.signal_cooldown_seconds
                        if time.time() - last_ts < cooldown:
                            should_send = False
                    # set/update last send time after sending
                except Exception:
                    should_send = True

            if should_send:
                await self.application.bot.send_message(
                    chat_id=self.config.telegram_admin_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                if self.redis is not None:
                    try:
                        await self.redis.set(key, str(time.time()), ex=self.settings_manager.settings.signal_cooldown_seconds + 5)
                    except Exception:
                        logger.exception("Failed to update last_signal in Redis")
        except Exception as exc:
            logger.exception("Error sending signal message: %s", exc)
        # persist last signals to Redis (list)
        if self.redis is not None:
            try:
                raw = json.dumps(asdict(signal), ensure_ascii=False)
                await self.redis.rpush("signals", raw)
                # keep list bounded to last 500
                await self.redis.ltrim("signals", -500, -1)
            except Exception:
                logger.exception("Failed to persist signal to Redis")

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text("Сканер запущен.", reply_markup=self._main_menu())

    async def on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        help_text = (
            "Доступные команды:\n"
            "/start — главное меню\n"
            "/status — показать текущие настройки\n"
            "/settings — открыть настройки сканера (inline)\n"
            "/help — показать эту подсказку\n\n"
            "Настройки через inline-кнопки: период OI, пороги роста/падения, мин OI, мин объём, включение бирж."
        )
        await update.message.reply_text(help_text)

    async def on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        settings = self.settings_manager.settings
        text = (
            f"<b>Текущие настройки сканера</b>\n"
            f"Период OI: {settings.oi_period_minutes} мин\n"
            f"Порог роста OI: {settings.oi_rise_percent}%\n"
            f"Порог падения OI: {settings.oi_drop_percent}%\n"
            f"Порог роста цены: {settings.price_rise_percent}%\n"
            f"Порог падения цены: {settings.price_drop_percent}%\n"
            f"Мин. OI: {settings.min_open_interest:.0f}\n"
            f"Откат сигнала: {settings.signal_cooldown_seconds} сек\n"
            f"Интервал обновления: {settings.scan_interval_seconds} сек\n"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

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
                parts.append(f"{obj.get('exchange')} {obj.get('symbol')} {obj.get('signal_type').upper()} | OI {obj.get('oi_change_percent')}% | Price {obj.get('price_change_percent')}% | Score {obj.get('signal_score')}/10 <a href=\"{obj.get('link')}\">link</a>")
            text = "\n\n".join(parts)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as exc:
            logger.exception("Error reading history: %s", exc)
            await update.message.reply_text("Ошибка при получении истории.")
        settings = self.settings_manager.settings
        # (status message moved to on_status)

    async def on_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update):
            await update.message.reply_text("Нет доступа.")
            return
        await update.message.reply_text(
            "Выберите настройку:",
            reply_markup=self._settings_keyboard(),
        )

    async def on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        payload = query.data or ""
        if payload.startswith("set_period:"):
            value = int(payload.split(":", 1)[1])
            self.settings_manager.update(oi_period_minutes=value)
            await query.edit_message_text(f"Период анализа установлен: {value} мин")
        elif payload.startswith("set_volume_spike:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(volume_spike_multiplier=value)
            await query.edit_message_text(f"Volume spike multiplier установлен: {value}x")
        elif payload.startswith("set_price_pump:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(price_pump_threshold_pct=value)
            await query.edit_message_text(f"Price pump threshold установлен: {value}%")
        elif payload.startswith("set_min_score:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(min_signal_score=value)
            await query.edit_message_text(f"Минимальная сила сигнала установлена: {value}")
        elif payload.startswith("set_top_n:"):
            value = int(payload.split(":", 1)[1])
            self.settings_manager.update(top_n_symbols=value)
            await query.edit_message_text(f"Top N symbols установлен: {value}")
        elif payload.startswith("set_oi_rise:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(oi_rise_percent=value)
            await query.edit_message_text(f"Порог роста OI установлен: {value}%")
        elif payload.startswith("set_oi_drop:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(oi_drop_percent=value)
            await query.edit_message_text(f"Порог падения OI установлен: {value}%")
        elif payload.startswith("set_price_rise:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(price_rise_percent=value)
            await query.edit_message_text(f"Порог роста цены установлен: {value}%")
        elif payload.startswith("set_price_drop:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(price_drop_percent=value)
            await query.edit_message_text(f"Порог падения цены установлен: {value}%")
        elif payload.startswith("set_min_oi:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(min_open_interest=value)
            await query.edit_message_text(f"Минимальный Open Interest установлен: {int(value):,}".replace(",", " "))
        elif payload.startswith("set_min_volume:"):
            value = float(payload.split(":", 1)[1])
            self.settings_manager.update(min_volume=value)
            await query.edit_message_text(f"Минимальный объём установлен: {int(value):,}".replace(",", " "))
        elif payload == "toggle_binance":
            current = self.settings_manager.settings.enabled_binance
            self.settings_manager.update(enabled_binance=not current)
            await query.edit_message_text(f"Binance включён: {not current}")
        elif payload == "toggle_bybit":
            current = self.settings_manager.settings.enabled_bybit
            self.settings_manager.update(enabled_bybit=not current)
            await query.edit_message_text(f"Bybit включён: {not current}")
        elif payload == "show_settings":
            await self.on_status(update, context)
        else:
            await query.edit_message_text("Неизвестное действие.")

    def _settings_keyboard(self) -> InlineKeyboardMarkup:
        s = self.settings_manager.settings
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Период 5 мин", callback_data="set_period:5"), InlineKeyboardButton("15 мин", callback_data="set_period:15")],
            [InlineKeyboardButton("30 мин", callback_data="set_period:30")],
            [InlineKeyboardButton("OI +1%", callback_data="set_oi_rise:1.0"), InlineKeyboardButton("+5%", callback_data="set_oi_rise:5.0")],
            [InlineKeyboardButton("+10%", callback_data="set_oi_rise:10.0")],
            [InlineKeyboardButton("OI -1%", callback_data="set_oi_drop:1.0"), InlineKeyboardButton("-5%", callback_data="set_oi_drop:5.0")],
            [InlineKeyboardButton("-10%", callback_data="set_oi_drop:10.0")],
            [InlineKeyboardButton("Цена +0.5%", callback_data="set_price_rise:0.5"), InlineKeyboardButton("+1%", callback_data="set_price_rise:1.0")],
            [InlineKeyboardButton("+2%", callback_data="set_price_rise:2.0")],
            [InlineKeyboardButton("Цена -0.5%", callback_data="set_price_drop:0.5"), InlineKeyboardButton("-1%", callback_data="set_price_drop:1.0")],
            [InlineKeyboardButton("-2%", callback_data="set_price_drop:2.0")],
            [InlineKeyboardButton("Мин OI 100k", callback_data="set_min_oi:100000"), InlineKeyboardButton("250k", callback_data="set_min_oi:250000")],
            [InlineKeyboardButton("500k", callback_data="set_min_oi:500000")],
            [InlineKeyboardButton("Мин объём 0", callback_data="set_min_volume:0"), InlineKeyboardButton("100k", callback_data="set_min_volume:100000")],
            [InlineKeyboardButton("500k", callback_data="set_min_volume:500000")],
            [InlineKeyboardButton("Vol spike x2", callback_data="set_volume_spike:2.0"), InlineKeyboardButton("x5", callback_data="set_volume_spike:5.0")],
            [InlineKeyboardButton("Price pump 3%", callback_data="set_price_pump:3.0"), InlineKeyboardButton("8%", callback_data="set_price_pump:8.0")],
            [InlineKeyboardButton("Min score 1.5", callback_data="set_min_score:1.5"), InlineKeyboardButton("2.0", callback_data="set_min_score:2.0")],
            [InlineKeyboardButton("Top 50", callback_data="set_top_n:50"), InlineKeyboardButton("Top 80", callback_data="set_top_n:80")],
            [InlineKeyboardButton(f"Binance: {'ON' if s.enabled_binance else 'OFF'}", callback_data="toggle_binance"), InlineKeyboardButton(f"Bybit: {'ON' if s.enabled_bybit else 'OFF'}", callback_data="toggle_bybit")],
            [InlineKeyboardButton("Показать настройки", callback_data="show_settings")],
        ])

    def _main_menu(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Scanner", callback_data="menu_scanner")],
            [InlineKeyboardButton("⚙ Настройки", callback_data="menu_settings")],
            [InlineKeyboardButton("🔔 Уведомления", callback_data="menu_notifications")],
            [InlineKeyboardButton("ℹ Help", callback_data="menu_help")],
        ])

    def _format_signal_message(self, signal: Signal) -> str:
        parts = [
            f"<b>Сигнал {signal.exchange}: {signal.signal_type.upper()}</b>",
            f"Монета: <a href=\"{signal.link}\">{signal.symbol}</a>",
            f"Период: {signal.oi_period_minutes} мин",
            f"OI: {signal.oi_direction} {signal.oi_change_percent}% ({signal.oi_change_value:+,.0f})",
        ]
        if signal.price_direction:
            parts.append(f"Цена: {signal.price_direction} {signal.price_change_percent}% ({signal.price_change_value:+.2f})")
        if signal.volume_change_percent is not None:
            parts.append(f"Объем 24ч Δ: {signal.volume_change_percent}%")
        if signal.spread is not None:
            parts.append(f"Spread: {signal.spread}")
        if signal.funding_rate is not None:
            parts.append(f"Funding rate: {signal.funding_rate}")
        parts.append(f"Текущая цена: {signal.current_price:.4f}" if signal.current_price is not None else "Цена: —")
        parts.append(f"Open Interest: {signal.current_open_interest:.0f}" if signal.current_open_interest is not None else "OI: —")
        parts.append(f"Сила сигнала: {signal.signal_score}/10")
        parts.append("<i>Сигнал для проверки позиции. Решение принимает пользователь.</i>")
        return "\n".join(parts)

    def _is_admin(self, update: Update) -> bool:
        user_id = None
        if update.effective_user:
            user_id = update.effective_user.id
        return user_id == self.config.telegram_admin_id
