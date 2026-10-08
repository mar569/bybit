"""Порядок и доступность провайдеров ИИ (Gemini / Relay / Groq)."""
from __future__ import annotations

import os
from typing import Literal

ProviderId = Literal["gemini", "relay", "groq"]

DEFAULT_AI_PROVIDER_ORDER: tuple[ProviderId, ...] = ("gemini", "relay", "groq")
_VALID: frozenset[str] = frozenset({"gemini", "relay", "groq"})


def parse_ai_provider_order(raw: str | None = None) -> tuple[ProviderId, ...]:
    text = (raw if raw is not None else os.environ.get("AI_PROVIDER_ORDER", "")).strip()
    if not text:
        return DEFAULT_AI_PROVIDER_ORDER
    parts: list[ProviderId] = []
    for chunk in text.replace(";", ",").split(","):
        pid = chunk.strip().lower()
        if pid in _VALID and pid not in parts:
            parts.append(pid)  # type: ignore[arg-type]
    return tuple(parts) if parts else DEFAULT_AI_PROVIDER_ORDER


def text_ai_available(gemini_api_key: str | None) -> bool:
    """Есть ли хотя бы один текстовый канал (Gemini не в cooldown или Relay/Groq)."""
    from .ai_analyst import fallback_llm_configured, gemini_in_cooldown

    key = (gemini_api_key or "").strip()
    if key and not gemini_in_cooldown():
        return True
    return fallback_llm_configured()
