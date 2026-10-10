"""HTTP-клиент Hummingbot API (Condor / hbot / MCP используют тот же API).

Документация: https://hummingbot.org/hummingbot-api/
Swagger на инстансе: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class HummingbotApiError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HummingbotApiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout_sec: float = 25.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(username, password)
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.request(
                    method,
                    url,
                    auth=self._auth,
                    json=json_body,
                    headers={"Accept": "application/json"},
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise HummingbotApiError(
                            f"HTTP {resp.status}: {text[:400]}",
                            status=resp.status,
                        )
                    if not text.strip():
                        return None
                    return await resp.json()
        except aiohttp.ClientError as exc:
            raise HummingbotApiError(str(exc)) from exc

    async def system_info(self) -> dict[str, Any]:
        data = await self._request("GET", "/system/info")
        return data if isinstance(data, dict) else {}

    async def bots_status(self) -> dict[str, Any]:
        data = await self._request("GET", "/bot-orchestration/status")
        if isinstance(data, dict) and "data" in data:
            return data["data"] if isinstance(data["data"], dict) else {}
        return data if isinstance(data, dict) else {}

    async def mqtt_status(self) -> dict[str, Any]:
        data = await self._request("GET", "/bot-orchestration/mqtt")
        if isinstance(data, dict) and "data" in data:
            inner = data["data"]
            return inner if isinstance(inner, dict) else {}
        return data if isinstance(data, dict) else {}

    async def portfolio_state_cached(self) -> dict[str, Any]:
        body = {"refresh": False, "skip_gateway": True}
        data = await self._request("POST", "/portfolio/state", json_body=body)
        return data if isinstance(data, dict) else {}

    async def accounts_distribution(self) -> dict[str, Any]:
        data = await self._request("GET", "/portfolio/accounts-distribution")
        return data if isinstance(data, dict) else {}


def client_from_config(config: object) -> HummingbotApiClient | None:
    url = (getattr(config, "hummingbot_api_url", None) or "").strip()
    user = (getattr(config, "hummingbot_api_username", None) or "").strip()
    password = getattr(config, "hummingbot_api_password", None) or ""
    if not url or not user or not password:
        return None
    return HummingbotApiClient(url, user, password)


def format_hbot_status_html(
    *,
    info: dict[str, Any],
    bots: dict[str, Any],
    mqtt: dict[str, Any],
    accounts_dist: dict[str, Any],
) -> str:
    from html import escape

    api_v = escape(str(info.get("api_version") or "—"))
    hb_v = escape(str(info.get("hummingbot_version") or "—"))
    docker_ok = "✅" if info.get("docker_available") else "❌"

    mqtt_on = mqtt.get("mqtt_connected")
    mqtt_line = "✅ MQTT" if mqtt_on else "❌ MQTT"
    active = mqtt.get("active_bots") or []
    discovered = mqtt.get("discovered_bots") or []
    if isinstance(active, dict):
        active = list(active.keys())
    if not isinstance(active, list):
        active = []
    if not isinstance(discovered, list):
        discovered = []

    lines = [
        "<b>🤖 Hummingbot API</b>",
        f"API <code>{api_v}</code> · lib <code>{hb_v}</code> · Docker {docker_ok}",
        f"{mqtt_line} · активных ботов: <b>{len(active)}</b> · обнаружено: <b>{len(discovered)}</b>",
    ]

    if bots:
        names = list(bots.keys())[:8]
        if names:
            lines.append("Боты: " + ", ".join(f"<code>{escape(n)}</code>" for n in names))

    total = accounts_dist.get("total_portfolio_value")
    if total is not None:
        try:
            lines.append(f"Портфель (кэш API): <b>${float(total):,.2f}</b>")
        except (TypeError, ValueError):
            pass

    lines.append(
        "<i>Condor — отдельный Telegram-бот к этому же API. "
        "Paper PMM: hbot create simple_pmm … · docs: hummingbot.org</i>"
    )
    return "\n".join(lines)
