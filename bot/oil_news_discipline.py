"""Дисциплина новостей для входа: HOT / фон / уже отыграно.

Правило профессионала:
- Новость «открыть LONG/SHORT» можно только пока она HOT (≤~30м).
- Если бот увидел сюжет час спустя — это ФОН, не сигнал входа.
- Если цена уже ушла в сторону новости — не догонять.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence


# HOT: очки входа + реакция
DEFAULT_HOT_HOURS = 0.5  # 30 минут
# WARM: только фон / конфликт, без очков «открыть»
DEFAULT_WARM_HOURS = 2.0
# Уже отыграно: ход с момента новости
DEFAULT_PRICED_IN_PCT = 0.35


@dataclass(frozen=True)
class NewsTradeAssessment:
    """Как читать новости для сделки прямо сейчас."""

    mode: str  # hot | warm | cold | none
    freshest_age_h: float | None
    bias: str  # bullish | bearish | mixed | neutral
    for_entry: bool  # можно ли давать очки входа
    block_long: bool
    block_short: bool
    priced_in: bool
    rule_ru: str
    age_note: str


def _age_hours(ts: float, *, now: float) -> float | None:
    if ts <= 0:
        return None
    return max(0.0, (now - ts) / 3600.0)


def freshest_news_age_hours(
    news_items: Sequence[Any] | None,
    *,
    now: float | None = None,
) -> float | None:
    now_ts = now if now is not None else time.time()
    ages: list[float] = []
    for it in list(news_items or []):
        ts = float(getattr(it, "published_ts", 0) or 0)
        age = _age_hours(ts, now=now_ts)
        if age is not None:
            ages.append(age)
    return min(ages) if ages else None


def move_since_news_pct(
    bars: Sequence[Any] | None,
    *,
    news_ts: float,
    now: float | None = None,
) -> float | None:
    """% хода close от бара около новости → сейчас. + вверх, − вниз."""
    if not bars or news_ts <= 0:
        return None
    now_ts = now if now is not None else time.time()
    # Берём close бара, ближайшего к времени новости (не новее now)
    best = None
    best_dt = 1e18
    for b in bars:
        # open_time в секундах или мс
        ot = float(getattr(b, "open_time", 0) or 0)
        if ot > 1e12:
            ot /= 1000.0
        if ot <= 0:
            continue
        dt = abs(ot - news_ts)
        if ot <= now_ts + 120 and dt < best_dt:
            best_dt = dt
            best = b
    if best is None:
        return None
    # Если бар слишком далеко от новости (>45м) — ненадёжно
    if best_dt > 45 * 60:
        # fallback: ход за час
        if len(bars) < 2:
            return None
        a = float(getattr(bars[max(0, len(bars) - 5)], "close", 0) or 0)
        b = float(getattr(bars[-1], "close", 0) or 0)
        if a <= 0:
            return None
        return (b - a) / a * 100.0
    px0 = float(getattr(best, "close", 0) or 0)
    px1 = float(getattr(bars[-1], "close", 0) or 0)
    if px0 <= 0 or px1 <= 0:
        return None
    return (px1 - px0) / px0 * 100.0


def assess_news_for_trade(
    news_items: Sequence[Any] | None,
    *,
    news_bias: Any | None = None,
    bars: Sequence[Any] | None = None,
    now: float | None = None,
    hot_hours: float = DEFAULT_HOT_HOURS,
    warm_hours: float = DEFAULT_WARM_HOURS,
    priced_in_pct: float = DEFAULT_PRICED_IN_PCT,
) -> NewsTradeAssessment:
    """Единое решение: новость = вход / фон / уже в цене."""
    now_ts = now if now is not None else time.time()
    bias = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    age_h = freshest_news_age_hours(news_items, now=now_ts)

    if age_h is None and (not news_bias or bias == "neutral"):
        return NewsTradeAssessment(
            mode="none",
            freshest_age_h=None,
            bias="neutral",
            for_entry=False,
            block_long=False,
            block_short=False,
            priced_in=False,
            rule_ru="Свежих новостей нет — только график/уровень, без новостного входа.",
            age_note="нет",
        )

    # Без дат в items, но есть bias — считаем warm (осторожно)
    if age_h is None:
        age_h = warm_hours * 0.75
        age_note = "bias без даты"
    else:
        age_note = f"{age_h * 60:.0f}м" if age_h < 1 else f"{age_h:.1f}ч"

    # Режим по возрасту публикации (не по «когда бот увидел»)
    if age_h <= hot_hours:
        mode = "hot"
    elif age_h <= warm_hours:
        mode = "warm"
    else:
        mode = "cold"

    # Ход цены с момента новости
    priced_in = False
    move = None
    freshest_ts = 0.0
    for it in list(news_items or []):
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts > freshest_ts:
            freshest_ts = ts
    if freshest_ts > 0 and bars:
        move = move_since_news_pct(bars, news_ts=freshest_ts, now=now_ts)
        if move is not None:
            if bias == "bullish" and move >= priced_in_pct:
                priced_in = True
            elif bias == "bearish" and move <= -priced_in_pct:
                priced_in = True

    for_entry = mode == "hot" and not priced_in and bias in {"bullish", "bearish"}

    # Фон/конфликт: тёплый/холодный bias всё ещё мешает против тренда новости
    block_long = bias == "bearish" and mode in {"hot", "warm"}
    block_short = bias == "bullish" and mode in {"hot", "warm"}
    # Уже отыграно — нельзя догонять В сторону новости
    if priced_in:
        if bias == "bullish":
            block_long = True
            for_entry = False
        elif bias == "bearish":
            block_short = True
            for_entry = False

    # Текст правила
    if mode == "hot" and for_entry:
        dir_ru = "вверх" if bias == "bullish" else "вниз"
        rule_ru = (
            f"Новость свежая ({age_note}) → можно учитывать для входа {dir_ru}, "
            f"но только от уровня и после реакции 5–15м."
        )
    elif mode == "hot" and priced_in:
        rule_ru = (
            f"Новость свежая ({age_note}), но цена уже ушла "
            f"({move:+.2f}% с выхода) — не догонять, ждать откат/уровень."
        )
    elif mode == "warm":
        rule_ru = (
            f"Новость вышла {age_note} назад — для бота это уже ФОН, не сигнал «открыть». "
            f"Не входить по ней в сторону сюжета; против фона — тоже осторожно."
        )
    elif mode == "cold":
        rule_ru = (
            f"Новость старая ({age_note}) — не использовать для входа. "
            f"Ждать новый flash или чистый уровень."
        )
    else:
        rule_ru = "Нет торгового новостного драйвера."

    return NewsTradeAssessment(
        mode=mode,
        freshest_age_h=age_h,
        bias=bias if bias else "neutral",
        for_entry=for_entry,
        block_long=block_long,
        block_short=block_short,
        priced_in=priced_in,
        rule_ru=rule_ru,
        age_note=age_note,
    )


def news_is_hot_for_reaction(
    published_ts: float,
    *,
    now: float | None = None,
    hot_hours: float = DEFAULT_HOT_HOURS,
) -> bool:
    """Ставить таймер реакции только на реально свежий flash."""
    age = _age_hours(float(published_ts or 0), now=now if now is not None else time.time())
    if age is None:
        return False
    return age <= hot_hours
