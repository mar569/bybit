#!/usr/bin/env bash
# Hummingbot API + (опционально) Condor — отдельный стек, не внутри образа Bybit_bot.
# Запуск на хосте/VPS с Docker: bash deploy/hummingbot/install-api.sh
set -euo pipefail
echo "→ Официальный установщик Hummingbot Deploy (API ± Condor)…"
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash
echo ""
echo "После установки:"
echo "  • Swagger: http://127.0.0.1:8000/docs"
echo "  • Paper PMM (hbot на хосте с conda): см. github.com/hummingbot/hummingbot"
echo "  • В .env Bybit_bot:"
echo "      HUMMINGBOT_API_URL=http://host.docker.internal:8000"
echo "      HUMMINGBOT_API_USERNAME=<из hummingbot-api/.env USERNAME>"
echo "      HUMMINGBOT_API_PASSWORD=<из hummingbot-api/.env PASSWORD>"
