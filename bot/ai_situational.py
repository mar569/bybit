"""ИИ-разбор поверх фактов бота (не с воздуха)."""
from __future__ import annotations

import logging

from .ai_alert_validate import build_alert_validate_context
from .ai_analyst import AiAskResult, ask_gemini, sanitize_ai_reply_for_telegram
from .ai_providers import text_ai_available
from .situational_brief import ai_situation_hint, classify_situation
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)

SITUATIONAL_SYSTEM = """Ты опытный intraday-трейдер по USDT-перпам. Пишешь по-русски, 4–6 предложений.

ЖЁСТКО:
- Уровни, зоны, триггеры, стоп и TP — ТОЛЬКО из JSON-пакета бота.
- Нет данных в пакете — скажи «в пакете нет уровня», не выдумывай.
- Не используй: режим C, setup D, bias, A/B/C, «фигура» если её нет в reading/pattern.
- Стиль зависит от situation_kind в запросе (импульс, консолидация, supply-шорт, range, …).
  Не подменяй ситуацию чужим шаблоном: если kind не htf_resistance_short — не описывай «лесенку в supply».
- Для supply-шорта: H4 зона, лесенка, L/S, M15-поглощение — только если это следует из JSON.
- Если импульс/перегрев — явно: не догонять.
- Формат: HTML для Telegram (<b>, <i>), без markdown, без списков из 10 пунктов.

Структура:
🧠 <b>Разбор</b>
2–4 предложения ситуации + что ждать + один план (лонг/шорт/WAIT) + стоп/цель если есть в пакете.
"""


async def run_situational_ai_reading(
    *,
    api_key: str | None,
    model: str,
    ta: TAAnalysisResult,
    symbol: str,
    signal_side: str = "",
    signal_type: str = "",
) -> str:
    if not text_ai_available(api_key):
        return ""
    ctx = build_alert_validate_context(
        ta,
        symbol=symbol,
        signal_side=signal_side,
        signal_type=signal_type,
    )
    hint = ai_situation_hint(ta)
    situation = str(getattr(ta, "situation_kind", "") or "").strip() or classify_situation(ta)
    user = (
        f"Монета {symbol.upper()}. Ситуация алгоритма (situation_kind): {situation}. "
        f"Подсказка стиля: {hint} "
        "Используй consolidation, breakdown_level, breakout_level, entry_zone, "
        "invalidation, target_prices, reading_tf_stack, phase из JSON."
    )
    try:
        result: AiAskResult = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=user,
            system_prompt=SITUATIONAL_SYSTEM,
            images=None,
            history=None,
        )
    except Exception:
        logger.debug("Situational AI failed for %s", symbol, exc_info=True)
        return ""
    if result.error or not (result.text or "").strip():
        return ""
    text = sanitize_ai_reply_for_telegram(result.text.strip())
    if "🧠" not in text and "<b>" not in text.lower():
        text = f"🧠 <b>Разбор ИИ</b>\n{text}"
    return text[:3800]
