"""Короткий LLM-pass на alert: подтвердить / снять слои, не перерисовывать TA."""
from __future__ import annotations

import json
import logging
from html import escape

from .ai_analyst import AiAskResult, ask_gemini, gemini_in_cooldown
from .ai_context import serialize_ta
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)

VALIDATE_SYSTEM = """Ты — ревьюер алгоритмического разбора криптофьючерса.
Тебе дан JSON+факты бота (reading present/absent, market state, сценарий C, gates).
Задача: сказать, что РЕАЛЬНО на графике, что НАТЯНУТО, согласен ли с grade сценария C.

Правила:
- Не придумывай уровни, OB, Fib, объём, OI — только из пакета.
- Если absent говорит «нет OB» — не описывай OB.
- Если exhaustion active — не агитируй continuation long.
- Максимум 5 коротких строк HTML для Telegram (<b>, <i>, без markdown).
- Формат:
🤖 <b>ИИ-ревью</b>
✅ Подтверждаю: …
❌ Снимаю/сомневаюсь: …
📌 Итог: WAIT/LONG/SHORT · согласен/не согласен с ботом · 1 фраза почему.
"""


def build_alert_validate_context(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
    signal_side: str = "",
    signal_type: str = "",
) -> str:
    blob = serialize_ta(ta)
    blob["alert"] = {
        "symbol": symbol.upper(),
        "signal_side": signal_side,
        "signal_type": signal_type,
    }
    blob["market_state_summary"] = getattr(ta, "market_state_summary", "")
    blob["scenario_engine"] = {
        "id": getattr(ta, "scenario_engine_id", ""),
        "quality": getattr(ta, "scenario_engine_quality", ""),
    }
    blob["entry_quality"] = getattr(ta, "entry_quality", "")
    metrics = ta.market_metrics or {}
    if isinstance(metrics, dict) and metrics.get("methodology_weights"):
        blob["methodology_weights"] = metrics["methodology_weights"]
    return json.dumps(blob, ensure_ascii=False, indent=0)[:12000]


def format_validate_result_html(result: AiAskResult) -> str:
    if result.error or not (result.text or "").strip():
        return ""
    text = result.text.strip()
    if "<b>" not in text.lower():
        text = f"🤖 <b>ИИ-ревью</b>\n{escape(text)}"
    return text[:3500]


async def run_alert_llm_validate(
    *,
    api_key: str | None,
    model: str,
    ta: TAAnalysisResult,
    symbol: str,
    signal_side: str = "",
    signal_type: str = "",
) -> str:
    if gemini_in_cooldown() and not api_key:
        return ""
    ctx = build_alert_validate_context(
        ta,
        symbol=symbol,
        signal_side=signal_side,
        signal_type=signal_type,
    )
    user = (
        f"Проверь alert по {symbol.upper()}. "
        f"Сканер: side={signal_side} type={signal_type}. "
        "Не дублируй весь отчёт — только ревью present/absent и сценарий C."
    )
    try:
        result = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=user,
            system_prompt=VALIDATE_SYSTEM,
            images=None,
            history=None,
        )
    except Exception:
        logger.debug("Alert LLM validate failed for %s", symbol, exc_info=True)
        return ""
    return format_validate_result_html(result)
