"""Gemini Free Tier client for the Telegram AI analyst (REST via aiohttp)."""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — живой intraday-трейдер USDT-perp (Bybit/Binance). Отвечаешь в Telegram.

Формат ответа (ОБЯЗАТЕЛЬНО, без markdown):
1) Первая строка: ВЕРДИКТ: LONG|SHORT|WAIT|NO TRADE · уверенность N/10 · горизонт 1–3ч
2) Сразу блок ЧТО ДЕЛАТЬ (2–4 короткие строки): входить или нет, как (лимит/ждать), зона, стоп, TP1.
   Если WAIT — напиши конкретно чего ждать (какой уровень / съём магнита), не «смотри рынок».
3) Короткий контекст (цена, фаза, магнит сверху/снизу).
4) 1 альтернативный сценарий + инвалидация.
5) Последняя строка: ⚠️ Не финсовет — решение за трейдером.

Запрещено:
- Markdown: **, *, __, #, ``` , списки с *
- Вода, «как ИИ», длинные эссе
- Оставлять вопрос без явного действия

Правила анализа:
- Не выдумывай уровни вне пакета/картинки.
- Heatmap Model 1 / liq magnet = стоп-магнит хай-левереджа «здесь и сейчас», не гарантия разворота.
- Если heatmap-картинка битая/404 — опирайся на LIQ_MAGNET из пакета и скажи это одной фразой.
- WAIT лучше плохого входа.
"""

DEFAULT_MODEL = "gemini-3.6-flash"
FALLBACK_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
)
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)


@dataclass
class AiChatMessage:
    role: str  # user | model
    text: str = ""
    images: list[bytes] = field(default_factory=list)


@dataclass
class AiAskResult:
    text: str
    model: str = ""
    error: str | None = None


class GeminiRateLimitError(Exception):
    """Free-tier quota / rate limit exhausted."""


class GeminiNotConfiguredError(Exception):
    """Missing GEMINI_API_KEY."""


def _is_rate_limit_payload(status: int, body: str) -> bool:
    low = body.lower()
    return status == 429 or "resource_exhausted" in low or "quota" in low


def _is_model_error_payload(status: int, body: str) -> bool:
    low = body.lower()
    return status in {400, 404} and (
        "not found" in low
        or "not_found" in low
        or "no longer available" in low
        or "not supported" in low
        or ("invalid" in low and "model" in low)
    )


def _image_part(png: bytes) -> dict[str, Any]:
    return {
        "inline_data": {
            "mime_type": "image/png",
            "data": base64.b64encode(png).decode("ascii"),
        }
    }


def _build_contents(
    history: list[AiChatMessage],
    user_text: str,
    images: list[bytes],
) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for msg in history[-12:]:
        parts: list[dict[str, Any]] = []
        if msg.text:
            parts.append({"text": msg.text})
        for img in msg.images[:3]:
            parts.append(_image_part(img))
        if not parts:
            continue
        role = "user" if msg.role == "user" else "model"
        contents.append({"role": role, "parts": parts})

    parts = []
    if user_text:
        parts.append({"text": user_text})
    for img in images[:4]:
        parts.append(_image_part(img))
    if not parts:
        parts.append({"text": "Продолжи анализ."})
    contents.append({"role": "user", "parts": parts})
    return contents


def _extract_text(payload: dict[str, Any]) -> str:
    cands = payload.get("candidates") or []
    if not cands:
        feedback = payload.get("promptFeedback") or {}
        block = feedback.get("blockReason") or feedback.get("block_reason")
        if block:
            return f"Ответ заблокирован модерацией Gemini ({block})."
        return ""
    content = cands[0].get("content") or {}
    parts = content.get("parts") or []
    chunks = [str(p.get("text") or "") for p in parts if p.get("text")]
    return "\n".join(chunks).strip()


async def ask_gemini(
    *,
    api_key: str | None,
    model: str,
    context_text: str,
    user_text: str,
    history: list[AiChatMessage] | None = None,
    images: list[bytes] | None = None,
) -> AiAskResult:
    if not api_key:
        raise GeminiNotConfiguredError(
            "Нет GEMINI_API_KEY. Бесплатный ключ: https://aistudio.google.com/apikey"
        )

    system = SYSTEM_PROMPT + "\n\n=== ПАКЕТ АЛГОРИТМОВ БОТА ===\n" + (context_text or "(пакет пуст)")
    contents = _build_contents(
        list(history or []),
        user_text or "Сделай разбор: что ждать 1–3 часа и какую позицию рассматривать.",
        list(images or []),
    )
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.45,
            "maxOutputTokens": 2048,
        },
    }

    primary = model or DEFAULT_MODEL
    candidates = [primary] + [m for m in FALLBACK_MODELS if m != primary]
    last_err = ""

    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for mid in candidates:
            url = GEMINI_ENDPOINT.format(model=mid)
            try:
                async with session.post(url, params={"key": api_key}, json=body) as resp:
                    raw = await resp.text()
                    if _is_rate_limit_payload(resp.status, raw):
                        raise GeminiRateLimitError(
                            "Лимит бесплатного Gemini исчерпан. "
                            "Подожди минуту/до завтра (дневная квота)."
                        )
                    if _is_model_error_payload(resp.status, raw):
                        logger.warning("Gemini model %s unavailable: %s", mid, raw[:300])
                        last_err = raw[:300]
                        continue
                    if resp.status >= 400:
                        last_err = f"HTTP {resp.status}: {raw[:400]}"
                        logger.error("Gemini error on %s: %s", mid, last_err)
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception as exc:
                        last_err = f"bad json: {exc}"
                        continue
                    text = _extract_text(payload if isinstance(payload, dict) else {})
                    if not text:
                        text = "Не удалось получить ответ модели. Попробуй ещё раз или пришли скрин."
                    return AiAskResult(text=text, model=mid)
            except GeminiRateLimitError:
                raise
            except Exception as exc:
                last_err = str(exc)
                logger.exception("Gemini request failed on %s", mid)

    return AiAskResult(text="", error=f"Gemini недоступен: {last_err}")


def sanitize_ai_reply_for_telegram(text: str) -> str:
    """Strip markdown so Telegram HTML doesn't show raw ** / * / #."""
    import re

    out = (text or "").strip()
    if not out:
        return out
    # **bold** / __bold__ → plain
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    # *italic* / _italic_ (avoid eating underscores in tickers like BANK_USDT rarely)
    out = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", out)
    # headings #### Title
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.MULTILINE)
    # bullet stars / dashes at line start → •
    out = re.sub(r"^[\t ]*[-*•]\s+", "• ", out, flags=re.MULTILINE)
    # leftover lone ** 
    out = out.replace("**", "")
    # collapse 3+ blank lines
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
