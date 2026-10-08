"""Gemini Free Tier client for the Telegram AI analyst (REST via aiohttp)."""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# После 429 / дневной квоты — не жечь ключ авто-blurbs и ретраями.
_gemini_cooldown_until: float = 0.0
_GEMINI_COOLDOWN_SEC = 45 * 60.0


def gemini_in_cooldown() -> bool:
    return time.time() < _gemini_cooldown_until


def gemini_cooldown_left_sec() -> int:
    return max(0, int(_gemini_cooldown_until - time.time()))


def mark_gemini_rate_limited(*, seconds: float | None = None) -> None:
    """Пауза авто-вызовов Gemini после исчерпания квоты."""
    global _gemini_cooldown_until
    sec = float(seconds if seconds is not None else _GEMINI_COOLDOWN_SEC)
    until = time.time() + max(60.0, sec)
    if until > _gemini_cooldown_until:
        _gemini_cooldown_until = until
        logger.warning(
            "Gemini cooldown ON for ~%d min (quota/rate limit)",
            int(sec // 60),
        )

SYSTEM_PROMPT = """Ты — аналитик краткосрочных сделок по криптоактивам.
Сведи локальный рабочий ТФ с подтверждением среднего ТФ и контекстом старших ТФ.
POSITION_CALL, decision gate и предоставленные уровни — основные факты; не придумывай цену,
объём, метрику или доступность данных. Если метрика отсутствует/не поддерживается тарифом,
так и скажи и не считай её подтверждением. Futures taker flow не называй CVD.
Учитывай цену, структуру, объём, OI, funding, long/short, ликвидации, фьючерсный и спотовый
taker flow; расхождения объясняй кратко. Низкий объём при обновлении экстремума — предупреждение,
но сам по себе не сигнал на разворот. Не используй волну Эллиотта и разметку ABC.

Ответ строго в 4 коротких строках:
1. ДЕЙСТВИЕ: LONG / SHORT / WAIT · рабочий ТФ · уверенность.
2. КАРТИНА: локальная структура и что подтверждают/не подтверждают старшие ТФ.
3. ПЛАН: триггер, стоп, ближайшая цель или «ждать»; только уровни из контекста.
4. ФАКТОРЫ: до 3 важных совпадений/конфликтов из объёма, OI, funding, L/S, flow и ликвидаций.
Если данных недостаточно — WAIT и назови конкретно, чего не хватает. Без вводных абзацев,
длинного обучения, повторов и markdown-разметки."""

DEFAULT_USER_PROMPT = (
    "Дай краткий локальный торговый вывод по фактам из пакета. "
    "Сверь рабочий ТФ со средним и старшими; укажи действие, план/триггер и главные "
    "подтверждения либо конфликты. Не додумывай отсутствующие данные."
)

DEFAULT_MODEL = "gemini-3.6-flash"
# Мало фолбэков: каждый лишний POST жрёт бесплатную квоту Gemini.
FALLBACK_MODELS = (
    "gemini-3.1-flash-lite",
    "gemini-2.0-flash",
)
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_RELAY_BASE_URL = "https://api.relaymodels.com/v1"
DEFAULT_RELAY_MODEL = "gemini-3.6-flash"

MAX_OUTPUT_TOKENS = 4096


def _env_groq_key() -> str | None:
    import os

    key = (os.environ.get("GROQ_API_KEY") or "").strip()
    return key or None


def _env_groq_model() -> str:
    import os

    return (os.environ.get("GROQ_MODEL") or "").strip() or DEFAULT_GROQ_MODEL


def groq_configured() -> bool:
    return bool(_env_groq_key())


def _env_relay_key() -> str | None:
    import os

    key = (os.environ.get("RELAY_API_KEY") or "").strip()
    return key or None


def _env_relay_model() -> str:
    import os

    return (os.environ.get("RELAY_MODEL") or "").strip() or DEFAULT_RELAY_MODEL


def _env_relay_base_url() -> str:
    import os

    raw = (os.environ.get("RELAY_BASE_URL") or "").strip()
    return raw.rstrip("/") or DEFAULT_RELAY_BASE_URL


def relay_configured() -> bool:
    return bool(_env_relay_key())


def fallback_llm_configured() -> bool:
    """Текстовый запасной канал без Gemini (Groq или RelayModels)."""
    return groq_configured() or relay_configured()


def ai_provider_order() -> tuple[str, ...]:
    from .ai_providers import parse_ai_provider_order

    return parse_ai_provider_order()


def _openai_error_message(raw: str, status: int) -> str:
    try:
        payload = json.loads(raw)
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:320]
        if isinstance(err, str):
            return err[:320]
    except Exception:
        pass
    return raw[:320] if raw else f"HTTP {status}"


def ai_provider_hint() -> str:
    """Коротко для панели: какие ключи есть."""
    parts: list[str] = []
    import os

    if (os.environ.get("GEMINI_API_KEY") or "").strip():
        parts.append("Gemini")
    if _env_groq_key():
        parts.append("Groq")
    if _env_relay_key():
        parts.append("Relay")
    return "+".join(parts) if parts else "нет ключей"

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
    finish_reason: str = ""


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


def _is_transient_gemini_payload(status: int, body: str) -> bool:
    """503 / high demand — пробуем другую модель, не считаем фаталом."""
    low = (body or "").lower()
    return status in {503, 500, 502, 504} or (
        "high demand" in low
        or "unavailable" in low
        or "try again later" in low
        or "overloaded" in low
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


def _extract_text(payload: dict[str, Any]) -> tuple[str, str]:
    cands = payload.get("candidates") or []
    if not cands:
        feedback = payload.get("promptFeedback") or {}
        block = feedback.get("blockReason") or feedback.get("block_reason")
        if block:
            return f"Ответ заблокирован модерацией Gemini ({block}).", "BLOCK"
        return "", ""
    cand0 = cands[0] or {}
    finish = str(cand0.get("finishReason") or cand0.get("finish_reason") or "")
    content = cand0.get("content") or {}
    parts = content.get("parts") or []
    chunks = [str(p.get("text") or "") for p in parts if p.get("text")]
    return "\n".join(chunks).strip(), finish


def _looks_truncated(text: str, finish_reason: str) -> bool:
    if (finish_reason or "").upper() in {"MAX_TOKENS", "LENGTH"}:
        return True
    t = (text or "").rstrip()
    if not t:
        return False
    # обрыв на полуслове / без финального дисклеймера и без пункта 3+
    if "Не финсовет" not in t and "не финсовет" not in t.lower():
        if "МОЯ ПОЗИЦИЯ" not in t.upper() and "2)" not in t:
            return True
        if "3)" not in t and "КАК ВОЙТИ" not in t.upper() and "ЧТО ДЕЛАТЬ" not in t.upper():
            return True
        if t[-1:] not in ".!…)" and not t.endswith("трейдером."):
            return True
    return False


async def _post_gemini(
    session: aiohttp.ClientSession,
    *,
    api_key: str,
    model: str,
    body: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    url = GEMINI_ENDPOINT.format(model=model)
    async with session.post(url, params={"key": api_key}, json=body) as resp:
        raw = await resp.text()
        if _is_rate_limit_payload(resp.status, raw):
            mark_gemini_rate_limited()
            left = gemini_cooldown_left_sec()
            raise GeminiRateLimitError(
                "Лимит бесплатного Gemini исчерпан. "
                f"Автопауза ~{max(1, left // 60)} мин "
                "(или до завтра, если дневная квота). "
                "Спросить ИИ ответит по новостям бота без Gemini."
            )
        if _is_model_error_payload(resp.status, raw):
            return None, raw[:300]
        if resp.status >= 400:
            return None, f"HTTP {resp.status}: {raw[:400]}"
        try:
            payload = json.loads(raw)
        except Exception as exc:
            return None, f"bad json: {exc}"
        if not isinstance(payload, dict):
            return None, "bad payload type"
        return payload, ""


async def _ask_openai_chat(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    system: str,
    user_text: str,
    history: list[AiChatMessage] | None = None,
    provider_label: str = "openai",
) -> AiAskResult:
    """OpenAI-совместимый chat/completions (Groq, RelayModels) — без картинок."""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for msg in (history or [])[-8:]:
        role = "assistant" if msg.role == "model" else "user"
        if msg.text:
            messages.append({"role": role, "content": msg.text})
    messages.append({"role": "user", "content": user_text or DEFAULT_USER_PROMPT})

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": min(2048, MAX_OUTPUT_TOKENS),
    }
    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as resp:
                raw = await resp.text()
                if resp.status == 429:
                    return AiAskResult(
                        text="",
                        model=model,
                        error=f"Лимит {provider_label} исчерпан. Подожди минуту.",
                    )
                if resp.status >= 400:
                    detail = _openai_error_message(raw, resp.status)
                    return AiAskResult(
                        text="",
                        model=model,
                        error=f"{provider_label} HTTP {resp.status}: {detail}",
                    )
                payload = json.loads(raw)
                choices = payload.get("choices") or []
                if not choices:
                    return AiAskResult(
                        text="", model=model, error=f"{provider_label}: пустой ответ",
                    )
                msg = (choices[0] or {}).get("message") or {}
                text = str(msg.get("content") or "").strip()
                finish = str((choices[0] or {}).get("finish_reason") or "")
                if not text:
                    return AiAskResult(
                        text="", model=model, error=f"{provider_label}: пустой текст",
                    )
                logger.info("AI via %s model=%s", provider_label, model)
                tag = provider_label.lower().replace(" ", "")
                return AiAskResult(
                    text=text,
                    model=f"{tag}:{model}",
                    finish_reason=finish,
                )
    except Exception as exc:
        logger.exception("%s request failed", provider_label)
        return AiAskResult(text="", model=model, error=str(exc))


async def _ask_groq(
    *,
    api_key: str,
    model: str,
    system: str,
    user_text: str,
    history: list[AiChatMessage] | None = None,
) -> AiAskResult:
    return await _ask_openai_chat(
        endpoint=GROQ_ENDPOINT,
        api_key=api_key,
        model=model or DEFAULT_GROQ_MODEL,
        system=system,
        user_text=user_text,
        history=history,
        provider_label="Groq",
    )


async def _ask_relay(
    *,
    api_key: str,
    model: str,
    system: str,
    user_text: str,
    history: list[AiChatMessage] | None = None,
) -> AiAskResult:
    base = _env_relay_base_url()
    endpoint = f"{base}/chat/completions"
    return await _ask_openai_chat(
        endpoint=endpoint,
        api_key=api_key,
        model=model or DEFAULT_RELAY_MODEL,
        system=system,
        user_text=user_text,
        history=history,
        provider_label="RelayModels",
    )


async def _run_gemini_provider(
    *,
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    history: list[AiChatMessage] | None,
    images: list[bytes] | None,
    system_prompt: str | None,
) -> tuple[AiAskResult | None, GeminiRateLimitError | None, str]:
    """Google Gemini API. Возвращает (успех | None, rate limit | None, last_err)."""
    contents = _build_contents(
        list(history or []),
        prompt,
        list(images or []),
    )
    body: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.35,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        },
    }

    primary = model or DEFAULT_MODEL
    candidates = [primary] + [m for m in FALLBACK_MODELS if m != primary]
    last_err = ""
    rate_err: GeminiRateLimitError | None = None

    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for mid in candidates:
            try:
                payload, err = await _post_gemini(
                    session, api_key=api_key, model=mid, body=body,
                )
                if payload is None:
                    last_err = err
                    if err and (
                        "not found" in err.lower()
                        or "not supported" in err.lower()
                        or "404" in err
                    ):
                        logger.warning("Gemini model %s unavailable: %s", mid, err)
                        continue
                    status_code = 0
                    if err.startswith("HTTP "):
                        try:
                            status_code = int(err.split(":", 1)[0].split()[1])
                        except (IndexError, ValueError):
                            status_code = 0
                    if _is_transient_gemini_payload(status_code, err):
                        logger.warning(
                            "Gemini busy on %s — next model: %s",
                            mid,
                            (err or "")[:160],
                        )
                        continue
                    logger.error("Gemini error on %s: %s", mid, err)
                    continue

                text, finish = _extract_text(payload)
                if not text:
                    text = (
                        "Не удалось получить ответ модели. "
                        "Попробуй ещё раз или пришли скрин."
                    )
                    return (
                        AiAskResult(text=text, model=mid, finish_reason=finish),
                        None,
                        "",
                    )

                if _looks_truncated(text, finish) and not system_prompt:
                    cont_body = {
                        "system_instruction": {"parts": [{"text": system}]},
                        "contents": contents
                        + [
                            {"role": "model", "parts": [{"text": text}]},
                            {
                                "role": "user",
                                "parts": [{
                                    "text": (
                                        "Продолжи С ТОГО МЕСТА где оборвалось. "
                                        "Допиши недостающие пункты, особенно "
                                        "2) МОЯ ПОЗИЦИЯ и 3) КАК ВОЙТИ, затем 6–7. "
                                        "Не повторяй пункт 1 целиком. Без markdown."
                                    )
                                }],
                            },
                        ],
                        "generationConfig": {
                            "temperature": 0.3,
                            "maxOutputTokens": MAX_OUTPUT_TOKENS,
                        },
                    }
                    payload2, err2 = await _post_gemini(
                        session, api_key=api_key, model=mid, body=cont_body,
                    )
                    if payload2 is not None:
                        more, finish2 = _extract_text(payload2)
                        if more:
                            text = (text.rstrip() + "\n" + more.lstrip()).strip()
                            finish = finish2 or finish
                    elif err2:
                        logger.warning(
                            "Gemini continuation failed on %s: %s", mid, err2
                        )

                return (
                    AiAskResult(text=text, model=mid, finish_reason=finish),
                    None,
                    "",
                )
            except GeminiRateLimitError as exc:
                rate_err = exc
                break
            except Exception as exc:
                last_err = str(exc)
                logger.exception("Gemini request failed on %s", mid)

    return None, rate_err, last_err


async def ask_gemini(
    *,
    api_key: str | None,
    model: str,
    context_text: str,
    user_text: str,
    history: list[AiChatMessage] | None = None,
    images: list[bytes] | None = None,
    system_prompt: str | None = None,
) -> AiAskResult:
    """ИИ по порядку AI_PROVIDER_ORDER (по умолчанию gemini → relay → groq).

    system_prompt: если задан — полностью заменяет трейдерский SYSTEM_PROMPT
    (нужно для «Спросить ИИ» по нефти, иначе модель пишет ВЕРДИКТ LONG).
    """
    groq_key = _env_groq_key()
    relay_key = _env_relay_key()
    has_images = bool(images)
    if system_prompt:
        system = system_prompt.strip()
        if context_text:
            system = system + "\n\n=== КОНТЕКСТ БОТА ===\n" + context_text
    else:
        system = (
            SYSTEM_PROMPT
            + "\n\n=== ПАКЕТ АЛГОРИТМОВ БОТА ===\n"
            + (context_text or "(пакет пуст)")
        )
    prompt = user_text or DEFAULT_USER_PROMPT

    if not api_key and not fallback_llm_configured():
        raise GeminiNotConfiguredError(
            "Нет GEMINI_API_KEY (и нет GROQ/RELAY). "
            "Gemini: https://aistudio.google.com/apikey · "
            "Groq: https://console.groq.com/keys · "
            "RelayModels: https://relaymodels.com"
        )
    if has_images and not (api_key or "").strip():
        return AiAskResult(
            text="",
            error="Для скринов нужен GEMINI_API_KEY (Relay/Groq — только текст).",
        )

    errors: list[str] = []
    rate_err: GeminiRateLimitError | None = None

    for pid in ai_provider_order():
        if pid == "gemini":
            if not api_key:
                continue
            if gemini_in_cooldown():
                left = gemini_cooldown_left_sec()
                rate_err = GeminiRateLimitError(
                    "Лимит бесплатного Gemini исчерпан. "
                    f"Пауза ещё ~{max(1, left // 60)} мин."
                )
                errors.append(str(rate_err))
                continue
            result, rate_err, last_err = await _run_gemini_provider(
                api_key=api_key,
                model=model,
                system=system,
                prompt=prompt,
                history=history,
                images=images,
                system_prompt=system_prompt,
            )
            if result is not None:
                return result
            if rate_err is not None:
                errors.append(str(rate_err))
                continue
            if last_err:
                errors.append(last_err)
                logger.warning("Gemini failed: %s", last_err[:200])
            continue

        if has_images:
            continue

        if pid == "relay" and relay_key:
            relay_res = await _ask_relay(
                api_key=relay_key,
                model=_env_relay_model(),
                system=system,
                user_text=prompt,
                history=history,
            )
            if relay_res.text and not relay_res.error:
                return relay_res
            if relay_res.error:
                errors.append(relay_res.error)
                logger.warning("RelayModels failed: %s", relay_res.error)

        if pid == "groq" and groq_key:
            groq_res = await _ask_groq(
                api_key=groq_key,
                model=_env_groq_model(),
                system=system,
                user_text=prompt,
                history=history,
            )
            if groq_res.text and not groq_res.error:
                return groq_res
            if groq_res.error:
                errors.append(groq_res.error)
                logger.warning("Groq failed: %s", groq_res.error)

    if rate_err is not None and not fallback_llm_configured():
        raise rate_err

    hint = errors[-1] if errors else "все провайдеры недоступны"
    return AiAskResult(text="", error=f"ИИ недоступен: {hint}")


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
