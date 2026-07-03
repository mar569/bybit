# Crypto Futures Scanner — быстрый запуск

Кратко: этот бот сканирует USDT perpetual фьючерсы на Binance и Bybit в реальном времени (приблизительно 1‑сек интервал данных по WebSocket), вычисляет индикаторы (OI Δ, ATR, RSI, EMA9/21, VWAP, скорость объёма, простая эвристика ликвидаций и др.) и шлёт уведомления в Telegram при достижении пользовательских порогов.

## Основные возможности

- Pump / Dump детектор (по OI и цене)
- Open Interest screener (настраиваемый период и пороги)
- Volume Spike (интервальный объём vs средний интервал)
- Price Pump за окно (по умолчанию 5 мин, порог 8%)
- CVD Divergence (эвристика по taker/CVD или OI+vol/price)
- Индикаторы: ATR, RSI, EMA(9/21), VWAP, скорость объёма
- Кликабельная ссылка CoinGlass в каждом уведомлении для ручной проверки
- Inline-настройки в Telegram: период OI, пороги, min OI, min volume, Volume spike x, Price pump %, Min signal score, Top N и включение бирж
- Redis: хранение истории сигналов и дедупликация (docker-compose включает Redis)

## Принцип формирования сигнала (кратко)

- Для каждой пары бот хранит историю снимков (price, open_interest, volume_24h, bid/ask).
- Для заданного периода (напр., 15 мин) вычисляется Δ OI (%) и Δ Price (%). Также рассчитываются индикаторы и interval volume (дельта 24h-volume между соседними snapshot'ами).
- Сигнал формируется, если комбинация условий (OI Δ, Price Δ, Volume Spike, индикаторные эвристики) превышает пороги и итоговый `signal_score` >= `min_signal_score`.
- Перед отправкой сообщения выполняется дедупликация по `last_signal:<exchange>:<symbol>` в Redis: если последний сигнал был отправлен менее, чем cooldown, он не будет повторно отправлен.

## Пользовательские пороги (в `bot/settings.json` или через inline-кнопки)

- `oi_period_minutes` — период OI (по умолчанию 15)
- `oi_rise_percent` / `oi_drop_percent` — базовые пороги OI
- `price_pump_threshold_pct` — рост цены за `price_pump_window_minutes` (по умолчанию 8% за 5 минут)
- `volume_spike_multiplier` — множитель для определения spike (по умолчанию 5x)
- `min_signal_score` — минимальная сила сигнала для отправки (по умолчанию 2)
- `min_open_interest` / `min_volume` — фильтры по ликвидности

## Пример уведомления

"""
🚀 SIGNAL PUMP — Binance: SOLUSDT
Монета: SOLUSDT (ссылка на CoinGlass)
Период: 15 мин
OI: up +8.12% (+1,420,000)
Цена: up +3.45% (+6.34)
Volume Δ (24h): +12.3%
ATR: 0.321, RSI: 67.2, EMA9: 182.1, EMA21: 178.9
Сила сигнала: 7/10
(volume_spike: true, price_pump_window: true, cvd_divergence: false)
"""

## Prerequisites (Ubuntu 24.04)

- Docker & Docker Compose (plugin)
- 2 CPU, 2GB RAM (рекомендовано больше для 300+ пар)

## Шаги развёртывания (проверенные)

1. Установить Docker и Compose (как root или через sudo):

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

2. Клонировать репозиторий и перейти в папку проекта:

```bash
git clone <your-repo-url>
cd Bybit_bot
```

3. Скопировать `.env.example` в `.env` и отредактировать (указать `TELEGRAM_TOKEN`, `TELEGRAM_ADMIN_ID` и опционально ключи бирж):

```bash
cp .env.example .env
nano .env
# заполните TELEGRAM_TOKEN и TELEGRAM_ADMIN_ID
```

4. (Опционально) проверьте `bot/settings.json` после первого запуска — там сохраняются inline-настройки. Можно заранее создать `bot/settings.json`, но не обязательно.

5. Запустить контейнеры (в корне проекта, там где `docker-compose.yml`):

```bash
docker compose build
docker compose up -d
```

6. Просмотреть логи бота:

```bash
docker compose logs -f bot
```

7. Проверить работу в Telegram:

- Отправьте `/start` боту (ваш `TELEGRAM_ADMIN_ID` должен совпадать) — бот покажет меню.
- `/status` — текущее состояние/пороги.
- `/settings` — откроется inline-меню для правок порогов.
- `/history [N]` — посмотреть последние N сигналов (при наличии Redis).

## Дополнительные заметки и источники данных

- Бот использует публичные WebSocket‑потоки Binance и Bybit для получения цен и Open Interest. Эти данные реальны и приходят в реальном времени от бирж.
- Ликвидации: в текущей версии реализована простая эвристика (если доступны поля `liquidation`/`cvd` в `additional` — они используются). Для точных данных по ликвидациям рекомендуется платный API (CoinGlass) или парсинг биржевых endpoint'ов, если у биржи есть соответствующие публичные каналы.
- Если вы хотите добавить MEXC или другие биржи — можно реализовать дополнительный `ExchangeScanner` в `bot/exchanges/` аналогично `binance.py` и `bybit.py`.

## Тюнинг сигналов

- Если сигналов слишком много — увеличьте `volume_spike_multiplier`, `price_pump_threshold_pct` или `min_signal_score`.
- Если сигналов нет — уменьшите пороги (например `volume_spike_multiplier` → 2.0, `price_pump_threshold_pct` → 3.0).

## Безопасность

- Токен Telegram и ключи бирж хранятся в `.env` — не коммитьте его в публичные репозитории.

## Что можно дальше

- подключить дополнительную биржу (MEXC),
- добавить вызовы платных API (CoinGlass) при наличии ключа,
- расширить историю сигналов и добавить экспорт в CSV.
