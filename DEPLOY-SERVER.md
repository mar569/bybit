# Развёртывание на Ubuntu-сервере с нуля

Репозиторий: [github.com/mar569/bybit](https://github.com/mar569/bybit)

Рекомендуемый путь: `/opt/bybit`. Код в контейнере собирается **без** `read.txt` и `docs/` (см. `.dockerignore`).

---

## 1. Docker (один раз)

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Выйдите из SSH и зайдите снова (или `newgrp docker`), чтобы группа `docker` применилась.

Проверка:

```bash
docker compose version
```

---

## 2. Клон и каталог

```bash
sudo mkdir -p /opt/bybit
sudo chown "$USER:$USER" /opt/bybit
cd /opt/bybit

git clone https://github.com/mar569/bybit.git .
# или обновление позже: git pull
```

---

## 3. Файл `.env`

```bash
cp .env.example .env
nano .env
```

**Минимум для крипто-бота:**

| Переменная | Где взять |
|------------|-----------|
| `ТЕЛЕГРАМ_ТОКЕН` | [@BotFather](https://t.me/BotFather) |
| `ТЕЛЕГРАМ_ID_АДМИНА` | [@userinfobot](https://t.me/userinfobot) или getUpdates |
| `ТЕЛЕГРАМ_ЧАТ_АНАЛИЗ` | группа для WATCH/разборов (id `-100…`) |
| `ТЕЛЕГРАМ_ЧАТ_АЛЕРТЫ` | опционально: редкий ENTRY (пусто = личка админу) |

Chat id: добавьте бота в группу → сообщение в группе → в браузере:

`https://api.telegram.org/bot<ТОКЕН>/getUpdates` → `"chat":{"id":-100…}`

**ИИ (опционально):**

- Gemini: [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → `КЛЮЧ_GEMINI`
- Groq: [console.groq.com/keys](https://console.groq.com/keys) → `КЛЮЧ_GROQ`

**Redis в Docker** — оставьте:

```env
АДРЕС_REDIS=redis://redis:6379/0
```

Латинские имена (`TELEGRAM_TOKEN`, …) тоже работают.

---

## 4. Настройки бота

```bash
# если файла нет после клона:
test -f bot/settings.json || cp bot/settings.json.example bot/settings.json
```

При первом запуске `settings.json` может мигрировать на новую версию (v97: тихий режим, график на WATCH).

---

## 5. Сборка и запуск

```bash
cd /opt/bybit
docker compose build
docker compose up -d
```

Логи:

```bash
docker compose logs -f bot
```

Ожидайте строки про Telegram, analysis chat, миграцию settings.

---

## 6. Проверка в Telegram

1. `/start` — только с аккаунта `ТЕЛЕГРАМ_ID_АДМИНА`
2. `/status` — сканер и чаты
3. Дождитесь WATCH в чате анализа (если включён quiet preset)

---

## Обновление версии

```bash
cd /opt/bybit
git pull
docker compose build
docker compose up -d
docker compose logs -f bot --tail=80
```

---

## Частые проблемы

| Симптом | Что сделать |
|---------|-------------|
| `permission denied` docker | `newgrp docker` или перелогин SSH |
| Бот молчит | Проверьте `.env`, `docker compose logs bot` |
| `settings.json` is a directory | `sudo rm -rf bot/settings.json` → `cp bot/settings.json.example bot/settings.json` |
| Redis снаружи не нужен | в `docker-compose.yml` можно убрать `ports: 6379` (только внутри сети compose) |

---

## Нефть / MT5

Канал `ТЕЛЕГРАМ_ЧАТ_НЕФТЬ` и MT5 — только если нужен модуль нефти (Windows MT5). На типичном Linux VPS достаточно крипто-части выше.
