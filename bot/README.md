# Crypto Futures Scanner — Telegram-бот

Личный сканер pump/dump сигналов для USDT perpetual фьючерсов на **Binance** и **Bybit**. Бот анализирует Open Interest, цену и объём в реальном времени и присылает уведомления в Telegram. Решение о сделке всегда принимаете вы.

> API-ключи бирж для сканирования **не нужны** — используются публичные REST и WebSocket API v5.

## Что умеет бот

- **Pump / Dump / OI screener** — рост или падение OI и цены за настраиваемый период (1–30 мин)
- **LONG / SHORT** — 🟢 зелёный = лонг, 🔴 красный = шорт
- **Все USDT perpetual** — список монет обновляется автоматически (500+ на Binance, 700+ на Bybit)
- **CoinGlass** — кликабельная монета + кнопка под каждым сигналом
- **Сила сигнала 1–10** — 1 = ранний вход, 10 = поздно
- **Приоритетные сигналы** (score ≤ 2) — звук, закрепление, метка 🔥 РАННИЙ СИГНАЛ
- **Пауза сигналов** — ⏸ Стоп / ▶️ Старт (конец торгового дня без перезапуска бота)
- **Динамические настройки** — меняются мгновенно, сохраняются в `bot/settings.json`
- **Топ-N монет** по объёму — меньше шума от неликвидных пар
- **Отдельные пороги** для Binance и Bybit
- **Redis** (опционально) — история сигналов и дедупликация

## Источники данных

| Биржа   | Цена / объём              | Open Interest                    |
|---------|---------------------------|----------------------------------|
| Bybit   | REST v5 tickers + WS      | в tickers + WS `tickers.{symbol}` |
| Binance | WS `!ticker@arr`          | WS `{symbol}@openInterest`       |

## Быстрый старт

### 1. Настройка `.env`

```bash
cp .env.example .env
```

Обязательно:

```env
TELEGRAM_TOKEN=ваш_токен_от_BotFather
TELEGRAM_ADMIN_ID=ваш_telegram_user_id
```

Опционально:

```env
TELEGRAM_ALERT_CHAT_ID=-1001234567890   # группа/канал для алертов
```

### 2. Запуск

```bash
docker compose build
docker compose up -d
docker compose logs -f bot
```

### 3. Telegram

1. `/start` — главное меню и кнопки внизу чата
2. `/test` — тестовые LONG + SHORT (проверка формата)
3. `/status` — текущие настройки
4. Дождитесь накопления истории (по умолчанию **15 мин**), затем придут реальные сигналы

## Кнопки в чате

| Кнопка | Действие |
|--------|----------|
| **⏸ Стоп** / **▶️ Старт** | Выключить / включить уведомления |
| **📊 Биржи** | Вкл/выкл Binance и Bybit |
| **🔧 Настройки** | Inline-меню порогов |

Состояние паузы сохраняется в `settings.json` и переживает перезапуск Docker.

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/status` | Текущие настройки |
| `/settings` | Inline-настройки |
| `/set help` | Точная настройка через команды |
| `/test` | Тестовые сигналы LONG + SHORT |
| `/pause` | Остановить уведомления |
| `/resume` | Возобновить уведомления |
| `/history [N]` | Последние N сигналов (нужен Redis) |
| `/help` | Справка |

### Примеры `/set`

```text
/set period 15
/set oi 5
/set oi_drop 5
/set price 1
/set price_drop 1
/set top 50          # только топ-50 по объёму (0 = все)
/set min_oi 100000
/set cooldown 60
/set score 1
/set signals off     # пауза
/set signals on      # возобновить

/set binance oi 3    # свой порог для Binance
/set bybit period 30
/set binance reset   # сбросить пороги биржи к глобальным
```

## Формат уведомления

```text
🔥 РАННИЙ СИГНАЛ          ← только для score ≤ 2
⚫ ByBit – 15м
🟢 LONG
BTCUSDT                   ← ссылка на CoinGlass
📈 ОИ вырос на 8.42% (1.52 млн. $)
🟢 💲 Изменение цены: +3.15%
🔊 Сигнал за сутки: 2
[📊 CoinGlass]            ← кнопка
```

Для шорта вместо 🟢 LONG будет 🔴 SHORT и отрицательное изменение цены.

## Настройки (`bot/settings.json`)

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `oi_period_minutes` | 15 | Период анализа OI/цены (1–30 мин) |
| `oi_rise_percent` | 5.0 | Порог роста OI (%) |
| `oi_drop_percent` | 5.0 | Порог падения OI (%) |
| `price_rise_percent` | 1.0 | Порог роста цены — LONG (%) |
| `price_drop_percent` | 1.0 | Порог падения цены — SHORT (%) |
| `min_open_interest` | 100000 | Минимальный OI |
| `min_volume` | 0 | Минимальный объём 24ч |
| `min_signal_score` | 1 | Мин. сила сигнала (1–10) |
| `priority_score_max` | 2 | Приоритет (звук + pin) для score ≤ N |
| `top_n_symbols` | null | Топ монет по объёму (null = все) |
| `signal_cooldown_seconds` | 60 | Пауза между повторами по одной монете |
| `signals_enabled` | true | Вкл/выкл уведомления |
| `enabled_binance` | true | Сканировать Binance |
| `enabled_bybit` | true | Сканировать Bybit |

Переопределения по биржам: `binance_oi_rise_percent`, `bybit_oi_period_minutes` и т.д. (`null` = глобальное значение).

## Как формируется сигнал

1. Для каждой пары накапливается история (цена, OI, объём).
2. За выбранный период считаются Δ OI (%), Δ OI ($), Δ цены (%).
3. Если пороги превышены и `signal_score` ≥ `min_signal_score` — отправляется уведомление.
4. Дедупликация: повтор по той же монете не чаще `signal_cooldown_seconds`.
5. При `signals_enabled = false` уведомления не отправляются, но данные продолжают собираться.

## Тюнинг

**Слишком много сигналов:**
- Увеличьте `oi_rise_percent`, `price_rise_percent`, `min_signal_score`
- Включите `top_n_symbols` (50 или 100)
- Увеличьте `signal_cooldown_seconds`

**Сигналов нет:**
- Уменьшите пороги: `/set oi 0.5`, `/set price 0.5`
- Уменьшите период: `/set period 5`
- Проверьте: `/status` → `signals_enabled` должен быть ВКЛ
- Подождите накопления истории за выбранный период

**Быстрый тест:**
```text
/set period 1
/set oi 0.5
/set price 0.5
/test
```

## Структура проекта

```text
bot/
  main.py              # точка входа
  scanner_engine.py    # логика сигналов
  telegram_bot.py      # Telegram UI
  settings.py          # настройки (JSON)
  set_parser.py        # парсер /set
  exchanges/
    bybit.py           # Bybit API v5
    binance.py         # Binance Futures
docker-compose.yml
.env
```

## Безопасность

- Не коммитьте `.env` в публичный репозиторий.
- `TELEGRAM_ADMIN_ID` — только этот пользователь управляет ботом.

## Полное ТЗ

Детальное техническое задание — в файле [`docs/TZ.md`](../docs/TZ.md).
