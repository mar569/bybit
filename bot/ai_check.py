"""Проверка ключей ИИ из .env: python -m bot.ai_check"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from .ai_analyst import ai_provider_hint, ai_provider_order, ask_gemini
from .ai_providers import text_ai_available
from .env_ru import apply_env_ru_aliases


def _load_dotenv() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    apply_env_ru_aliases()


async def _run() -> int:
    _load_dotenv()
    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip() or None
    gemini_model = (os.environ.get("GEMINI_MODEL") or "").strip() or "gemini-3.6-flash"
    print("Configured:", ai_provider_hint())
    print("Order:", ",".join(ai_provider_order()))
    if not ai_provider_hint() or ai_provider_hint() == "нет ключей":
        print("FAIL: нет ключей (GEMINI / GROQ / RELAY в .env)")
        return 1
    if not text_ai_available(gemini_key):
        print("WARN: Gemini в cooldown и нет Groq/Relay — текстовый ИИ может молчать")
    result = await ask_gemini(
        api_key=gemini_key,
        model=gemini_model,
        context_text="(тест бота)",
        user_text="Ответь одним словом: ок",
        history=None,
        images=None,
    )
    if result.error:
        print("FAIL:", result.error)
        return 2
    print("OK provider/model:", result.model)
    print("Reply:", (result.text or "")[:300])
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
