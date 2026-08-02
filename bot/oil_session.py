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
            f"Bybit UKOUSD.s откроется <b>{next_open.strftime('%d.%m в %H:%M')} по Москве (GMT+3)</b> "
            f"— понедельник с 01:00"
        )
        caution = (
            "Сейчас <b>выходные</b>: на Bybit TradFi (UKOUSD.s) торговля недоступна "
            "(сб–вс закрыто). Бот читает новости и готовит план на открытие в "
            "<b>пн 01:00 MSK</b>. Это не сигнал входить сейчас."
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
            f"Ночной перерыв Bybit. Открытие <b>{next_open.strftime('%d.%m в %H:%M')} MSK</b> "
            f"(вт–пт с 03:00)"
        )
        return OilSessionStatus(
            is_weekend_gap=False,
            is_open=False,
            market_open_hint_ru="Ночной перерыв UKOUSD.s (примерно до 03:00 MSK)",
            next_open_msk=next_open,
            next_open_label_ru=label,
            caution_ru=(
                "Котировки/торговля на паузе до 03:00. Не путать с выходным gap."
            ),
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
    """Сводка: что ждать на открытии пн 01:00 MSK (Bybit UKOUSD.s)."""
    session = session or oil_session_status()
    bias_n = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    w = float(getattr(news_bias, "weighted_score", 0) or 0) if news_bias else 0.0
    fc_bias = (getattr(forecast, "bias", "") or "").upper() if forecast else ""
    fc_scen = getattr(forecast, "scenario", "") or "" if forecast else ""

    tops: list[str] = []
    for it in list(news_items or [])[:8]:
        t = (getattr(it, "title", "") or "").strip()
        if not t:
            continue
        imp = getattr(it, "impact", "neutral")
        mark = {"bullish": "↑", "bearish": "↓"}.get(imp, "·")
        tops.append(f"{mark} {t[:110]}")
        if len(tops) >= 5:
            break

    dealish = any(
        any(
            k in (getattr(it, "title", "") or "").lower()
            for k in (
                "taco", "cancel", "pause", "deal", "reopen", "tumble", "slump",
                "отмен", "струсил", "сделк",
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
            for k in ("cancel", "pause", "taco", "отмен")
        )
        for it in (news_items or [])
    )

    if bias_n == "bearish" or fc_bias == "SHORT" or (dealish and not hot_geo):
        bias = "DOWN"
        conf = 7 if abs(w) >= 2 or dealish else 5
        headline = (
            f"Простыми словами: в <b>пн в 01:00 по Москве</b> Bybit UKOUSD.s "
            f"скорее откроется <b>дешевле</b>. Ориентир сейчас ≈<b>${price:.2f}</b> "
            f"(на сторонних площадках цена уже могла уйти сильно вниз с ~92)."
        )
        base = (
            "За выходные: удары по Ирану отложили/отменили, страх войны ослаб. "
            "На Bybit в сб–вс торгов нет — в понедельник в 01:00 часто бывает "
            "резкий старт (gap). Реалистичная зона дальше — <b>около 80–82</b>, "
            "если не выйдет новая пугающая новость до открытия. "
            "Спред на старте может быть шире обычного."
        )
        alt = (
            "Другой вариант: отскок вверх к 85–88, если до 01:00 Трамп снова "
            "угрожает ударами. Тогда падение могут частично выкупить."
        )
    elif bias_n == "bullish" or fc_bias == "LONG" or hot_geo:
        bias = "UP"
        conf = 6
        headline = (
            f"Простыми словами: на открытии пн 01:00 MSK есть риск, что "
            f"UKOUSD.s <b>откроется дороже</b>. Ориентир ≈${price:.2f}."
        )
        base = (
            "В новостях снова про удары или закрытие пролива. "
            "Рынок может открыться выше — снова боятся перебоев с поставками."
        )
        alt = (
            "Другой вариант: если перед 01:00 скажут про сделку/паузу — "
            "цена может резко развернуться вниз."
        )
    else:
        bias = "MIXED"
        conf = 4
        headline = (
            f"Простыми словами: на пн 01:00 MSK картина смешанная. "
            f"Ориентир ≈${price:.2f}. Лучше подождать первый час сессии."
        )
        base = (
            "Новости тянут и вверх, и вниз. Без ясного сигнала не стоит "
            "угадывать gap заранее."
        )
        alt = "Сильная новость по Ормузу поздним вечером воскресенья может всё перевернуть."

    levels = []
    if sun_low_hint:
        levels.append(f"ближайшее дно прокси ≈${sun_low_hint:.2f}")
    levels.append("важная круглая отметка <b>$80</b>")
    if sat_high_hint:
        levels.append(
            f"если вернётся выше ≈${sat_high_hint:.2f} — сценарий падения слабеет"
        )
    levels_ru = "; ".join(levels)

    scen_plain = {
        "deal_tape": "фон «сделка / меньше страха»",
        "disruption": "фон «сбои поставок / война»",
        "mixed_geo": "фон смешанный по Ормузу",
        "inventory": "фон запасов США",
        "opec_supply": "фон ОПЕК",
        "range": "нет сильного драйвера",
    }.get(fc_scen, "")
    if scen_plain:
        base = f"{base} Сейчас бот видит: {scen_plain}."

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
        disclaimer_ru=(
            "Это не финансовый совет. Bybit UKOUSD.s на выходных закрыт; "
            "цена на Hyperliquid/Yahoo может сильно отличаться до пн 01:00 MSK. "
            "Отметка $80 — ориентир, не гарантия."
        ),
    )


def format_weekend_open_brief(
    brief: OilOpenBrief, *, session: OilSessionStatus | None = None
) -> str:
    session = session or oil_session_status()
    mark = {"DOWN": "🔴", "UP": "🟢", "MIXED": "⚪", "WAIT": "⚪"}.get(brief.bias, "⚪")
    dir_ru = {
        "DOWN": "скорее вниз",
        "UP": "скорее вверх",
        "MIXED": "неясно",
        "WAIT": "ждать",
    }.get(brief.bias, "")
    lines = [
        f"{mark} <b>Что будет с нефтью на открытии Bybit</b>",
        f"<i>Пн 01:00 MSK (GMT+3) · уверенность {brief.confidence} из 10 · {dir_ru}</i>",
        brief.session_ru,
        "",
        brief.headline_ru,
        "",
        "<b>Главный сценарий</b>",
        brief.base_case_ru,
        "",
        "<b>Что может пойти иначе</b>",
        brief.alt_case_ru,
        "",
        f"<b>На какие цены смотреть:</b> {brief.levels_ru}",
    ]
    if brief.news_digest_ru:
        lines.append("")
        lines.append("<b>Из последних новостей</b>")
        for n in brief.news_digest_ru:
            lines.append(f"• {_esc(n)}")
    if session.caution_ru:
        lines.append("")
        lines.append(f"⚠️ {session.caution_ru}")
    lines.append("")
    lines.append(
        "<i>Расписание UKOUSD.s: пн 01:00–24:00 · вт–пт 03:00–24:00 · сб–вс закрыто.</i>"
    )
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
