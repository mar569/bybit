"""Сессия нефти Bybit TradFi UKOUSD.s — точное расписание GMT+3."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

try:
    from zoneinfo import ZoneInfo

    _MSK = ZoneInfo("Europe/Moscow")
except Exception:  # pragma: no cover
    _MSK = timezone(timedelta(hours=3), name="MSK")

# Bybit TradFi UKOUSD.s (Brent Cash), Local Time GMT+3 — из спецификации контракта:
# Пн: 01:00–24:00 | Вт–Пт: 03:00–24:00 | Сб–Вс: торговля недоступна
_MON_OPEN_H = 1
_WEEKDAY_OPEN_H = 3  # Tue–Fri


@dataclass(frozen=True)
class OilSessionStatus:
    is_weekend_gap: bool
    is_open: bool
    market_open_hint_ru: str
    next_open_msk: datetime | None
    next_open_label_ru: str
    caution_ru: str


def _as_msk(now: datetime | None) -> datetime:
    now = now or datetime.now(tz=_MSK)
    if now.tzinfo is None:
        return now.replace(tzinfo=_MSK)
    return now.astimezone(_MSK)


def next_ukousd_open_msk(*, now: datetime | None = None) -> datetime:
    """Следующее открытие UKOUSD.s по расписанию Bybit (MSK/GMT+3)."""
    now = _as_msk(now)
    wd = now.weekday()  # 0=Mon … 6=Sun

    def at_day(day: datetime, hour: int) -> datetime:
        return day.replace(hour=hour, minute=0, second=0, microsecond=0)

    # Суббота → понедельник 01:00
    if wd == 5:
        mon = (now + timedelta(days=2)).replace(
            hour=_MON_OPEN_H, minute=0, second=0, microsecond=0
        )
        return mon
    # Воскресенье → понедельник 01:00
    if wd == 6:
        mon = (now + timedelta(days=1)).replace(
            hour=_MON_OPEN_H, minute=0, second=0, microsecond=0
        )
        return mon
    # Понедельник до 01:00
    if wd == 0:
        open_today = at_day(now, _MON_OPEN_H)
        if now < open_today:
            return open_today
        # после 01:00 — следующий перерыв вторник 03:00 (если уже после полуночи вт)
        return at_day(now + timedelta(days=1), _WEEKDAY_OPEN_H)
    # Вт–Пт до 03:00 (ночной перерыв)
    open_today = at_day(now, _WEEKDAY_OPEN_H)
    if now < open_today:
        return open_today
    # Пятница после открытия → понедельник 01:00 (сб–вс закрыто)
    if wd == 4:
        return at_day(now + timedelta(days=3), _MON_OPEN_H)
    # Пн–чт после открытия → следующий будний open (вт–пт 03:00)
    return at_day(now + timedelta(days=1), _WEEKDAY_OPEN_H)


def is_ukousd_session_open(*, now: datetime | None = None) -> bool:
    """True если UKOUSD.s сейчас торгуется по Bybit GMT+3."""
    now = _as_msk(now)
    wd = now.weekday()
    h = now.hour + now.minute / 60.0
    if wd >= 5:
        return False
    if wd == 0:
        return h >= _MON_OPEN_H  # 01:00–24:00
    # Tue–Fri: 03:00–24:00
    return h >= _WEEKDAY_OPEN_H


def oil_session_status(*, now: datetime | None = None) -> OilSessionStatus:
    """Статус сессии Bybit TradFi UKOUSD.s (GMT+3)."""
    now = _as_msk(now)
    wd = now.weekday()
    is_open = is_ukousd_session_open(now=now)
    next_open = next_ukousd_open_msk(now=now)

    if wd >= 5 or (wd == 0 and not is_open):
        label = (
            f"Открытие: <b>{next_open.strftime('%d.%m в %H:%M')} МСК</b> (пн)"
        )
        caution = (
            "Выходные: UKOUSD.s на Bybit закрыт. Это план на открытие, не вход сейчас."
        )
        return OilSessionStatus(
            is_weekend_gap=True,
            is_open=False,
            market_open_hint_ru="Сейчас выходные — UKOUSD.s закрыт (Bybit)",
            next_open_msk=next_open,
            next_open_label_ru=label,
            caution_ru=caution,
        )

    if not is_open:
        # Ночной перерыв вт–пт 00:00–03:00
        label = (
            f"Ночной перерыв. Открытие <b>{next_open.strftime('%d.%m в %H:%M')} МСК</b>"
        )
        return OilSessionStatus(
            is_weekend_gap=False,
            is_open=False,
            market_open_hint_ru="Ночной перерыв UKOUSD.s (примерно до 03:00 MSK)",
            next_open_msk=next_open,
            next_open_label_ru=label,
            caution_ru="Пауза до 03:00 МСК. Не путать с выходными.",
        )

    return OilSessionStatus(
        is_weekend_gap=False,
        is_open=True,
        market_open_hint_ru="UKOUSD.s сейчас открыт (Bybit TradFi)",
        next_open_msk=None,
        next_open_label_ru="",
        caution_ru="",
    )


@dataclass(frozen=True)
class OilOpenBrief:
    """Прогноз на открытие после выходных по последним новостям + цене."""

    bias: str  # DOWN | UP | MIXED | WAIT
    confidence: int
    price_proxy: float
    headline_ru: str
    base_case_ru: str
    alt_case_ru: str
    levels_ru: str
    news_digest_ru: tuple[str, ...]
    session_ru: str
    disclaimer_ru: str


def build_weekend_open_brief(
    *,
    price: float,
    news_items: Sequence[Any],
    news_bias: Any | None,
    forecast: Any | None = None,
    session: OilSessionStatus | None = None,
    sat_high_hint: float | None = None,
    sun_low_hint: float | None = None,
) -> OilOpenBrief:
    """Коротко: что ждать на открытии пн 01:00 MSK (Bybit UKOUSD.s)."""
    session = session or oil_session_status()
    bias_n = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    w = float(getattr(news_bias, "weighted_score", 0) or 0) if news_bias else 0.0
    fc_bias = (getattr(forecast, "bias", "") or "").upper() if forecast else ""

    tops: list[str] = []
    for it in list(news_items or [])[:8]:
        t = (getattr(it, "title", "") or "").strip()
        if not t:
            continue
        imp = getattr(it, "impact", "neutral")
        mark = {"bullish": "↑", "bearish": "↓"}.get(imp, "·")
        tops.append(f"{mark} {t[:90]}")
        if len(tops) >= 3:
            break

    dealish = any(
        any(
            k in (getattr(it, "title", "") or "").lower()
            for k in (
                "taco", "cancel", "pause", "deal", "reopen", "tumble", "slump",
                "отмен", "струсил", "сделк", "holds off", "hold off",
            )
        )
        for it in (news_items or [])
    )
    hot_geo = any(
        any(
            k in (getattr(it, "title", "") or "").lower()
            for k in ("attack", "strike", "blockade", "close strait", "удар", "атак")
        )
        and not any(
            k in (getattr(it, "title", "") or "").lower()
            for k in ("cancel", "pause", "taco", "отмен", "holds off", "hold off")
        )
        for it in (news_items or [])
    )
    opec_hike = any(
        any(
            k in (getattr(it, "title", "") or "").lower()
            for k in ("quota", "hike", "increase", "квот", "повыс", "добыч")
        )
        for it in (news_items or [])
    )

    # Новости важнее прогноза графика: сделка/ОПЕК → вниз; удары → вверх
    if dealish and not hot_geo:
        bias = "DOWN"
        conf = 7 if abs(w) >= 2 or dealish else 5
        if opec_hike:
            conf = min(8, conf + 1)
        headline = (
            f"На открытии пн <b>01:00 МСК</b> скорее <b>дешевле</b> "
            f"(сейчас ориентир ≈${price:.2f})."
        )
        base = (
            "Страх войны ослаб (пауза/отмена ударов). "
            + (
                "Плюс ОПЕК может добавить добычу — тоже давит вниз. "
                if opec_hike
                else ""
            )
            + "На старте возможен резкий разрыв цены."
        )
        alt = "Если ночью снова про удары/пролив — может развернуться вверх."
    elif bias_n == "bearish" or fc_bias == "SHORT" or opec_hike:
        bias = "DOWN"
        conf = 6 if opec_hike or abs(w) >= 2 else 5
        headline = (
            f"На открытии пн <b>01:00 МСК</b> скорее <b>дешевле</b> "
            f"(сейчас ориентир ≈${price:.2f})."
        )
        base = (
            "Новости давят цену вниз. "
            "Подожди первый час после открытия."
        )
        alt = "Резкая новость про конфликт/пролив может подбросить вверх."
    elif hot_geo or bias_n == "bullish":
        bias = "UP"
        conf = 6
        headline = (
            f"На открытии пн <b>01:00 МСК</b> скорее <b>дороже</b> "
            f"(сейчас ориентир ≈${price:.2f})."
        )
        base = "В ленте риски по Ирану/проливу — рынок может открыться выше."
        alt = "Если до открытия скажут про сделку или паузу — может резко вниз."
    else:
        bias = "MIXED"
        conf = 4
        headline = (
            f"На открытии пн <b>01:00 МСК</b> пока <b>неясно</b> "
            f"(ориентир ≈${price:.2f}). Лучше подождать первый час."
        )
        base = "Новости тянут в разные стороны. Без ясного сигнала не входи вслепую."
        alt = "Сильная новость ночью может всё перевернуть."

    levels: list[str] = []
    if sun_low_hint and sun_low_hint > 0:
        levels.append(f"низ ≈${sun_low_hint:.2f}")
    if sat_high_hint and sat_high_hint > 0:
        levels.append(f"верх ≈${sat_high_hint:.2f}")
    levels_ru = " · ".join(levels) if levels else "смотри цену на Bybit после 01:00"

    return OilOpenBrief(
        bias=bias,
        confidence=conf,
        price_proxy=price,
        headline_ru=headline,
        base_case_ru=base,
        alt_case_ru=alt,
        levels_ru=levels_ru,
        news_digest_ru=tuple(tops),
        session_ru=session.next_open_label_ru or session.market_open_hint_ru,
        disclaimer_ru="Не финсовет. Смотри только Bybit UKOUSD.s.",
    )


def format_weekend_open_brief(
    brief: OilOpenBrief, *, session: OilSessionStatus | None = None
) -> str:
    """Короткий текст без простыни — только по делу."""
    session = session or oil_session_status()
    mark = {"DOWN": "🔴", "UP": "🟢", "MIXED": "⚪", "WAIT": "⚪"}.get(brief.bias, "⚪")
    dir_ru = {
        "DOWN": "скорее вниз",
        "UP": "скорее вверх",
        "MIXED": "неясно",
        "WAIT": "ждать",
    }.get(brief.bias, "")
    lines = [
        f"{mark} <b>Открытие UKOUSD.s</b> · пн 01:00 МСК",
        f"<i>{dir_ru} · уверенность {brief.confidence}/10</i>",
        "",
        brief.headline_ru,
        "",
        f"<b>Главное:</b> {brief.base_case_ru}",
        f"<b>Иначе:</b> {brief.alt_case_ru}",
        f"<b>Ориентиры:</b> {brief.levels_ru}",
    ]
    if brief.news_digest_ru:
        lines.append("")
        lines.append("<b>Новости</b>")
        for n in brief.news_digest_ru[:3]:
            lines.append(f"• {_esc(n)}")
    if session.caution_ru or session.is_weekend_gap:
        lines.append("")
        lines.append(
            "⚠️ Сейчас выходные: на Bybit UKOUSD.s торгов нет. "
            "Это план на открытие, не вход сейчас."
        )
    lines.append("")
    lines.append(f"<i>{brief.disclaimer_ru}</i>")
    return "\n".join(lines)


def minutes_until_oil_open(*, now: datetime | None = None) -> float | None:
    """Минут до следующего открытия UKOUSD.s (Bybit GMT+3)."""
    now = _as_msk(now)
    if is_ukousd_session_open(now=now):
        return None
    nxt = next_ukousd_open_msk(now=now)
    return (nxt - now).total_seconds() / 60.0


def should_send_preopen_alert(
    *,
    now: datetime | None = None,
    window_lo_min: float = 30.0,
    window_hi_min: float = 70.0,
) -> bool:
    """True за 30–70 мин до открытия (для пн 01:00 → вс ~23:50–пн 00:30 MSK)."""
    mins = minutes_until_oil_open(now=now)
    if mins is None:
        return False
    return window_lo_min <= mins <= window_hi_min


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
