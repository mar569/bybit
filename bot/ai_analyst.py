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

SYSTEM_PROMPT = """Ты — очень опытный трейдер USDT-perp (Bybit/Binance), 8–12 лет.
Смотришь рынок ГЛОБАЛЬНО по нескольким ТФ, собираешь картину и даёшь КОНКРЕТНУЮ ПОЗИЦИЮ.
Не «только 5m-скальп». Не чеклист. Не учебник. Не обрывай ответ.

=== POSITION_CALL (ядро ответа) ===
В пакете есть POSITION_CALL — это голоса алгоритмов бота (не фантазия):
  TA verdict, action_priority, decision_gate ENTRY/WATCH/SKIP, playbook,
  BuyHold foresight / треугольники / флаги, HTF+LTF фигуры,
  Elliott/ABC/wave (правила TradeRevolution: W3 никогда самая короткая, чередование 2/4,
  зигзаг/расширенный флет/бегущий флет, диагонали, Fib-кластеры),
  setup confluence A–D, RSI divergence, flow cont/corr, SMC, liq.
Твоя задача: на основе POSITION_CALL + графика/liq_map СФОРМУЛИРОВАТЬ мнение:
  какую позицию открывать (LONG/SHORT) или WAIT/NO_TRADE, КАК входить, ПОЧЕМУ.
Правила:
  • Не противоречь сильному перевесу votes без явной причины со скрина.
  • mode=watch / watch_both / dual_breakout_close → НЕ market «сейчас», только триггер close.
  • mode=entry и gate ENTRY → можно план входа по уровням POSITION_CALL.levels.
  • Уровни entry/stop/tp бери из POSITION_CALL.levels / MEANINGFUL_LEVELS / графика — не выдумывай.
  • Волны, треугольники (симм./восход./расширяющ.), клинья/диагонали, EW, Fib-кластеры — упоминай только если есть в пакете.
  • Коррекцию ABC классифицируй: зигзаг / расш.флет / бегущий флет / треугольник — по corr_type из пакета.

=== PRO-ИНВАРИАНТЫ (обязательно) ===
  • SHORT-цель / TP ТОЛЬКО ниже текущей цены; LONG-цель / TP ТОЛЬКО выше. Иначе цель невалидна — не пиши её.
  • TP ≠ триггер входа (цель должна быть ЗА уровнем пробоя).
  • На WAIT: не market; опиши lean + два триггера close; TP указывай для bias-стороны.
  • Не клеить цель бычьей фигуры к SHORT (и наоборот) — при HTF/LTF конфликте приоритет HTF bias.
  • Confidence 8–10 на WAIT при смешанном flow (cont≈corr) запрещён в формулировке — это «ждать», не «готовый вход».
  • Числа stop/tp/trigger — только из POSITION_CALL.levels или явных уровней на скрине.

=== МУЛЬТИ-ТФ (обязательно) ===
WORKING_TF + HTF 1h + микро на скрине. Синтез: HTF bias → WORKING уровни/триггер → микро тайминг.
Конфликт LTF vs HTF → WAIT·bias, приоритет HTF.

=== КАК ДУМАТЬ (порядок) ===
1) POSITION_CALL — сторона, mode, votes, why.
2) VOLATILITY_REGIME — горизонт и мин. TP.
3) Структура + сжатие/post-pump.
4) Liq/CVD/OI + RSI divergence.
5) Фигуры BuyHold / EW / Fib / SMC confluence.
6) Финальный план позиции (числа).

=== ЗАПРЕТЫ ===
- Не обрывай. Все 7 пунктов заверши.
- Не пиши «1–3ч», если horizon короче. Не TP1 < tp1_min_pct.
- Не выдумывай уровни/фигуры вне пакета. Без markdown **, *, #.
- Не оставляй только «WAIT смотри» без явного lean LONG/SHORT и триггеров, если votes не нулевые.
- Не пиши SHORT→цель выше цены или LONG→цель ниже цены.

=== ФОРМАТ TELEGRAM (7 пунктов) ===
1) ВЕРДИКТ: LONG|SHORT|WAIT|NO_TRADE · lean если WAIT · N/10 · ГОРИЗОНТ
2) МОЯ ПОЗИЦИЯ: одной-двумя фразами — ЧТО открывать и ПОЧЕМУ (ссылка на волны/фигуру/liq/RSI/gate из пакета).
   Если WAIT — «не сейчас, жду … для LONG/SHORT».
3) КАК ВОЙТИ: market запрещён если mode≠entry; иначе/иначе: триггер close WORKING_TF, стоп, TP1 (%), TP2 (%), R:R.
4) КУДА ЖДЁМ: путь цены после входа (уровни).
5) CONFLUENCE: 2–4 факта из алгоритмов (EW/фигура/RSI/CVD/OI/SMC/magnet).
   Если есть ELLIOTT.path_scenario / path_prices — опиши ожидаемый путь на path_horizon_hours (1–3ч).
6) АЛЬТЕРНАТИВА + ИНВАЛИДАЦИЯ (цена).
7) ⚠️ Не финсовет — решение за трейдером.

Пиши плотно. Место кончается → сначала допиши пункты 2–3 и 7.
"""

DEFAULT_USER_PROMPT = (
    "Собери картину по пакету алгоритмов бота и дай СВОЁ мнение о позиции: "
    "POSITION_CALL (голоса TA/gate/playbook/фигуры/EW/RSI/flow) — база. "
    "Что открывать (LONG/SHORT) или WAIT, как войти (триггер close), стоп, TP. "
    "Опирайся на волны, треугольники, паттерны, liq, структуру из пакета — не выдумывай. "
    "Ответ ПОЛНЫЙ — все 7 пунктов, без обрыва. Без markdown."
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


def ai_provider_hint() -> str:
    """Коротко для панели: какие ключи есть."""
    parts: list[str] = []
    import os

    if (os.environ.get("GEMINI_API_KEY") or "").strip():
        parts.append("Gemini")
    if _env_groq_key():
        parts.append("Groq")
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


async def _ask_groq(
    *,
    api_key: str,
    model: str,
    system: str,
    user_text: str,
    history: list[AiChatMessage] | None = None,
) -> AiAskResult:
    """Бесплатный запасной канал (Groq Llama) — без картинок."""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for msg in (history or [])[-8:]:
        role = "assistant" if msg.role == "model" else "user"
        if msg.text:
            messages.append({"role": role, "content": msg.text})
    messages.append({"role": "user", "content": user_text or DEFAULT_USER_PROMPT})

    body = {
        "model": model or DEFAULT_GROQ_MODEL,
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": min(2048, MAX_OUTPUT_TOKENS),
    }
    timeout = aiohttp.ClientTimeout(total=90)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                GROQ_ENDPOINT,
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
                        error="Лимит Groq исчерпан. Подожди минуту.",
                    )
                if resp.status >= 400:
                    return AiAskResult(
                        text="",
                        model=model,
                        error=f"Groq HTTP {resp.status}: {raw[:240]}",
                    )
                payload = json.loads(raw)
                choices = payload.get("choices") or []
                if not choices:
                    return AiAskResult(text="", model=model, error="Groq: пустой ответ")
                msg = (choices[0] or {}).get("message") or {}
                text = str(msg.get("content") or "").strip()
                finish = str((choices[0] or {}).get("finish_reason") or "")
                if not text:
                    return AiAskResult(text="", model=model, error="Groq: пустой текст")
                logger.info("AI via Groq model=%s", model)
                return AiAskResult(text=text, model=f"groq:{model}", finish_reason=finish)
    except Exception as exc:
        logger.exception("Groq request failed")
        return AiAskResult(text="", model=model, error=str(exc))


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
    """Gemini primary; при квоте/ошибке — бесплатный Groq (если GROQ_API_KEY).

    system_prompt: если задан — полностью заменяет трейдерский SYSTEM_PROMPT
    (нужно для «Спросить ИИ» по нефти, иначе модель пишет ВЕРДИКТ LONG).
    """
    groq_key = _env_groq_key()
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

    if not api_key and not groq_key:
        raise GeminiNotConfiguredError(
            "Нет GEMINI_API_KEY (и нет GROQ_API_KEY). "
            "Gemini: https://aistudio.google.com/apikey · "
            "Groq бесплатно: https://console.groq.com/keys"
        )

    rate_err: GeminiRateLimitError | None = None

    # 1) Gemini, если ключ есть и не на паузе
    if api_key and not gemini_in_cooldown():
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
                        logger.error("Gemini error on %s: %s", mid, err)
                        continue

                    text, finish = _extract_text(payload)
                    if not text:
                        text = (
                            "Не удалось получить ответ модели. "
                            "Попробуй ещё раз или пришли скрин."
                        )
                        return AiAskResult(text=text, model=mid, finish_reason=finish)

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

                    return AiAskResult(text=text, model=mid, finish_reason=finish)
                except GeminiRateLimitError as exc:
                    rate_err = exc
                    break
                except Exception as exc:
                    last_err = str(exc)
                    logger.exception("Gemini request failed on %s", mid)

        if rate_err is None and last_err and not groq_key:
            return AiAskResult(text="", error=f"Gemini недоступен: {last_err}")
    elif api_key and gemini_in_cooldown():
        left = gemini_cooldown_left_sec()
        rate_err = GeminiRateLimitError(
            "Лимит бесплатного Gemini исчерпан. "
            f"Пауза ещё ~{max(1, left // 60)} мин."
        )

    # 2) Groq fallback (текст; картинки Gemini-only)
    if groq_key and not has_images:
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
            logger.warning("Groq fallback failed: %s", groq_res.error)

    if rate_err is not None:
        raise rate_err
    if not api_key:
        raise GeminiNotConfiguredError(
            "Нет рабочего AI-ключа. Gemini: aistudio.google.com/apikey · "
            "Groq: console.groq.com/keys"
        )
    return AiAskResult(text="", error="Gemini недоступен (и Groq не помог)")


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
