"""Экономический календарь нефти: FF JSON + EIA/API + desk-брифинг админу."""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    _MSK = ZoneInfo("Europe/Moscow")
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _MSK = timezone(timedelta(hours=3), name="MSK")
    _ET = timezone(timedelta(hours=-5), name="ET")

_FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Только то, что реально двигает нефть / USD-risk для Brent
_OIL_TITLE_KW = (
    "crude", "oil inventory", "oil inventories", "api weekly", "eia",
    "opec", "petroleum", "gasoline inventory", "distillate",
    "non-farm", "nonfarm", "nfp", "cpi", "ppi", "fomc", "fed chair",
    "interest rate", "powell", "bessent", "trump speaks", "president trump",
    "white house", "adp", "ism manufacturing", "ism services",
    "crude oil", "natural gas storage",
)

_SPEECH_RE = re.compile(
    r"("
    r"press\s*conference|presser|will\s+speak|to\s+speak|speech\s+at|"
    r"scheduled\s+to|holds?\s+a\s+press|white\s+house\s+briefing|"
    r"пресс[- ]?конференц|выступ(?:ит|ление)|через\s+\d+\s*(?:час|ч|мин)|"
    r"в\s+\d{1,2}[:.]\d{2}|at\s+\d{1,2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?|et|utc)?"
    r")",
    re.I,
)


@dataclass(frozen=True)
class OilCalendarEvent:
    key: str
    title_ru: str
    when_ts: float
    kind: str  # eia | api | opec | fed | cpi | ppi | nfp | speech | inventory | macro | other
    impact: str = "Medium"  # High | Medium | Low
    lock_before_min: float = 20.0
    lock_after_min: float = 15.0
    country: str = ""


@dataclass(frozen=True)
class OilCalendarLock:
    active: bool
    reason_ru: str = ""
    until_ts: float = 0.0
    event: OilCalendarEvent | None = None


def _as_msk(now: datetime | None = None) -> datetime:
    now = now or datetime.now(tz=_MSK)
    if now.tzinfo is None:
        return now.replace(tzinfo=_MSK)
    return now.astimezone(_MSK)


def _parse_ff_date(raw: str) -> float | None:
    try:
        dt = datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_ET)
        return dt.timestamp()
    except Exception:
        return None


def _kind_from_title(title: str) -> str:
    low = (title or "").lower()
    if "api" in low and ("oil" in low or "crude" in low or "statistical" in low):
        return "api"
    if "crude oil" in low or "oil inventor" in low or "eia" in low:
        return "eia" if "eia" in low or "crude oil inventor" in low else "inventory"
    if "opec" in low:
        return "opec"
    if "cpi" in low:
        return "cpi"
    if "ppi" in low:
        return "ppi"
    if "non-farm" in low or "nonfarm" in low or low.strip() == "nfp":
        return "nfp"
    if "fomc" in low or "fed chair" in low or "powell" in low:
        return "fed"
    if "speak" in low or "trump" in low or "bessent" in low:
        return "speech"
    return "macro"


_WD_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _title_ru(title: str, kind: str) -> str:
    """Любой EN-тайтл из FF → коротко по-русски."""
    low = (title or "").lower().strip()
    mapping_kind = {
        "api": "API запасы нефти",
        "eia": "EIA запасы нефти (Crude)",
        "inventory": "Запасы нефти США",
        "opec": "ОПЕК / JMMC",
        "cpi": "CPI США (инфляция)",
        "ppi": "PPI США",
        "nfp": "NFP (занятость США)",
        "fed": "ФРС / FOMC",
    }
    if kind in mapping_kind and kind != "speech":
        # уточнение спикера, если есть
        if kind == "fed":
            if "powell" in low:
                return "Пауэлл (ФРС) — выступление / пресс-конф."
            if "fomc" in low and "rate" in low:
                return "FOMC — решение по ставке"
            if "fomc" in low:
                return "FOMC / заседание ФРС"
        return mapping_kind[kind]

    # Speeches / named people
    if "powell" in low:
        return "Пауэлл (ФРС) — выступление"
    if "bessent" in low:
        return "Бессент — выступление"
    if "trump" in low and ("speak" in low or "press" in low or "remarks" in low):
        return "Трамп — выступление / брифинг"
    if "white house" in low and ("brief" in low or "press" in low):
        return "Белый дом — брифинг"
    if kind == "speech" or "speak" in low or "press conference" in low or "remarks" in low:
        who = "спикер"
        if "fed" in low or "chair" in low:
            who = "ФРС"
        elif "treasury" in low:
            who = "Минфин США"
        elif "trump" in low:
            who = "Трамп"
        return f"Выступление ({who})"

    if "adp" in low:
        return "ADP занятость США"
    if "ism manufacturing" in low:
        return "ISM производство США"
    if "ism services" in low or "ism non-manufacturing" in low:
        return "ISM услуги США"
    if "claim" in low and "jobless" in low:
        return "Заявки на пособия по безработице США"
    if "retail sales" in low:
        return "Розничные продажи США"
    if "gdp" in low:
        return "ВВП США"
    if "crude" in low or "oil inventor" in low:
        return "Запасы нефти США"
    if "gasoline" in low and "inventor" in low:
        return "Запасы бензина США"
    if "natural gas" in low:
        return "Запасы газа США"
    if "opec" in low:
        return "ОПЕК / встреча"

    # fallback — без сырого EN
    if kind == "macro":
        return "Макро США (риск для нефти)"
    t = re.sub(r"\s+", " ", (title or "").strip())
    # если уже кириллица — оставить
    if re.search(r"[А-Яа-яЁё]", t):
        return t[:80]
    return "Событие календаря (см. время)"


def _headline_ru_short(title: str) -> str:
    """Ночной фон: EN-заголовок → 1 фраза по-русски."""
    try:
        from .oil_why import _explain_headline

        what, means, _ = _explain_headline(title or "")
        # what уже по-русски; если generic — сжать means
        if what and "без ясного" not in what.lower():
            return what
        return means[:100] if means else "Сюжет по нефти"
    except Exception:
        t = (title or "").strip()
        if re.search(r"[А-Яа-яЁё]", t):
            return t[:100]
        return "Новость по нефти / геополитике"


def _lock_windows(kind: str, impact: str) -> tuple[float, float]:
    if kind in {"eia", "api", "inventory"}:
        return 25.0, 20.0
    if kind in {"nfp", "cpi"} or impact == "High":
        return 30.0, 20.0
    if kind in {"fed", "opec", "speech"}:
        return 20.0, 30.0
    if impact == "Medium":
        return 15.0, 15.0
    return 10.0, 10.0


def _is_oil_relevant_ff(row: dict[str, Any]) -> bool:
    title = str(row.get("title") or "")
    country = str(row.get("country") or "")
    impact = str(row.get("impact") or "")
    low = title.lower()
    if impact == "Holiday":
        return False
    if any(k in low for k in _OIL_TITLE_KW):
        return True
    # USD High macro всегда влияет на risk/нефть
    if country == "USD" and impact == "High":
        return True
    if country == "USD" and impact == "Medium" and any(
        k in low for k in ("speak", "fomc", "adp", "ism", "claim", "powell", "bessent", "trump")
    ):
        return True
    return False


def fetch_ff_oil_events(*, timeout: float = 12.0) -> list[OilCalendarEvent]:
    """ForexFactory this-week JSON → только oil/USD-risk события."""
    try:
        req = urllib.request.Request(
            _FF_URL,
            headers={"User-Agent": "BybitOilBot/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        logger.debug("FF calendar fetch failed", exc_info=True)
        return []
    if not isinstance(data, list):
        return []
    out: list[OilCalendarEvent] = []
    for row in data:
        if not isinstance(row, dict) or not _is_oil_relevant_ff(row):
            continue
        ts = _parse_ff_date(str(row.get("date") or ""))
        if ts is None:
            continue
        title = str(row.get("title") or "").strip()
        kind = _kind_from_title(title)
        impact = str(row.get("impact") or "Medium")
        before, after = _lock_windows(kind, impact)
        out.append(
            OilCalendarEvent(
                key=f"ff-{kind}-{int(ts)}-{title[:24]}",
                title_ru=_title_ru(title, kind),
                when_ts=ts,
                kind=kind,
                impact=impact,
                lock_before_min=before,
                lock_after_min=after,
                country=str(row.get("country") or ""),
            )
        )
    out.sort(key=lambda e: e.when_ts)
    return out


def _next_weekday_et(weekday: int, hour: int, minute: int, *, now: datetime) -> datetime:
    et_now = now.astimezone(_ET)
    target = et_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days = (weekday - et_now.weekday()) % 7
    if days == 0 and target <= et_now:
        days = 7
    return (target + timedelta(days=days)).astimezone(_MSK)


def _fallback_weekly_oil(now_m: datetime) -> list[OilCalendarEvent]:
    """Если FF недоступен — хотя бы API/EIA по расписанию."""
    events: list[OilCalendarEvent] = []
    api_dt = _next_weekday_et(1, 16, 30, now=now_m)
    eia_dt = _next_weekday_et(2, 10, 30, now=now_m)
    events.append(
        OilCalendarEvent(
            key=f"api-{api_dt.date().isoformat()}",
            title_ru="API запасы нефти",
            when_ts=api_dt.timestamp(),
            kind="api",
            impact="Medium",
            lock_before_min=25.0,
            lock_after_min=20.0,
            country="USD",
        )
    )
    events.append(
        OilCalendarEvent(
            key=f"eia-{eia_dt.date().isoformat()}",
            title_ru="EIA запасы нефти (Crude)",
            when_ts=eia_dt.timestamp(),
            kind="eia",
            impact="High",
            lock_before_min=25.0,
            lock_after_min=20.0,
            country="USD",
        )
    )
    return events


def upcoming_oil_events(
    *,
    now: datetime | None = None,
    horizon_hours: float = 72.0,
    ff_events: Sequence[OilCalendarEvent] | None = None,
) -> list[OilCalendarEvent]:
    """Ближайшие oil-релевантные события (FF + fallback)."""
    now_m = _as_msk(now)
    now_ts = now_m.timestamp()
    horizon = now_ts + horizon_hours * 3600.0
    raw = list(ff_events) if ff_events is not None else fetch_ff_oil_events()
    if not raw:
        raw = _fallback_weekly_oil(now_m)
    out = [e for e in raw if now_ts - 3600 <= e.when_ts <= horizon]
    # dedupe близких одинаковых kind
    kept: list[OilCalendarEvent] = []
    seen: set[str] = set()
    for e in sorted(out, key=lambda x: x.when_ts):
        bucket = f"{e.kind}-{int(e.when_ts // 1800)}"
        if bucket in seen:
            continue
        seen.add(bucket)
        kept.append(e)
    return kept


def calendar_entry_lock(
    *,
    now_ts: float | None = None,
    events: Sequence[OilCalendarEvent] | None = None,
) -> OilCalendarLock:
    t0 = now_ts if now_ts is not None else time.time()
    evs = list(events) if events is not None else upcoming_oil_events()
    for ev in evs:
        # Low без нефти/речи не блокируем
        if ev.impact == "Low" and ev.kind not in {"eia", "api", "inventory", "speech", "opec"}:
            continue
        start = ev.when_ts - ev.lock_before_min * 60.0
        end = ev.when_ts + ev.lock_after_min * 60.0
        if start <= t0 <= end:
            left = max(0, int((end - t0) / 60.0))
            return OilCalendarLock(
                active=True,
                reason_ru=f"🔒 {ev.title_ru} · входы OFF ещё ~{left}м",
                until_ts=end,
                event=ev,
            )
    return OilCalendarLock(active=False)


def detect_scheduled_speech_freeze(
    title: str,
    *,
    now_ts: float | None = None,
    freeze_minutes: float = 60.0,
) -> OilCalendarLock:
    t = (title or "").strip()
    if not t or not _SPEECH_RE.search(t):
        return OilCalendarLock(active=False)
    low = t.lower()
    future_marks = (
        "will ", "to speak", "scheduled", "press conference", "presser",
        "через ", "выступит", "пресс-конферен", "сегодня в", "tomorrow",
    )
    if not any(m in low for m in future_marks):
        return OilCalendarLock(active=False)
    t0 = now_ts if now_ts is not None else time.time()
    until = t0 + max(15.0, float(freeze_minutes)) * 60.0
    return OilCalendarLock(
        active=True,
        reason_ru=f"🔒 Ожидается заявление · входы OFF ~{int(freeze_minutes)}м",
        until_ts=until,
        event=OilCalendarEvent(
            key=f"speech-{int(t0)}",
            title_ru=_headline_ru_short(t)[:80],
            when_ts=t0,
            kind="speech",
            impact="High",
            lock_before_min=0.0,
            lock_after_min=float(freeze_minutes),
        ),
    )


def merge_locks(*locks: OilCalendarLock) -> OilCalendarLock:
    active = [lk for lk in locks if lk and lk.active]
    if not active:
        return OilCalendarLock(active=False)
    return max(active, key=lambda lk: lk.until_ts)


def _fmt_when(ts: float, now_ts: float) -> str:
    when = datetime.fromtimestamp(ts, tz=_MSK)
    delta_m = int((ts - now_ts) / 60.0)
    wd = _WD_RU[when.weekday()]
    day_bit = f"{wd} {when.strftime('%d.%m')} "
    if delta_m < -5:
        tag = "было"
    elif delta_m <= 0:
        tag = "сейчас"
    elif delta_m < 60:
        tag = f"через {delta_m}м"
    elif delta_m < 24 * 60:
        tag = f"через {delta_m // 60}ч {delta_m % 60:02d}м"
    else:
        tag = day_bit.strip()
        return f"{when.strftime('%H:%M')} МСК · {tag}"
    # если не сегодня — добавить день
    now_d = datetime.fromtimestamp(now_ts, tz=_MSK).date()
    if when.date() != now_d:
        return f"{day_bit}{when.strftime('%H:%M')} МСК · {tag}"
    return f"{when.strftime('%H:%M')} МСК · {tag}"


def important_events_today(
    events: Sequence[OilCalendarEvent],
    *,
    now: datetime | None = None,
) -> list[OilCalendarEvent]:
    """События «стоит знать в Новостнике»: нефть + High макро + речи."""
    now_m = _as_msk(now)
    day_end = datetime(
        now_m.year, now_m.month, now_m.day, 23, 59, 59, tzinfo=_MSK
    ).timestamp()
    now_ts = now_m.timestamp()
    important_kinds = {
        "eia", "api", "inventory", "opec", "nfp", "cpi", "ppi", "fed", "speech",
    }
    out: list[OilCalendarEvent] = []
    for e in events:
        if e.when_ts < now_ts - 1800 or e.when_ts > day_end + 3600:
            continue
        if e.kind in important_kinds:
            out.append(e)
            continue
        if e.impact == "High" and e.country in {"USD", "All", ""}:
            out.append(e)
    return out[:6]


def format_newsroom_desk_flash(
    events: Sequence[OilCalendarEvent],
    *,
    now: datetime | None = None,
) -> str:
    """Одна короткая карточка в Новостник — только если день важный."""
    now_m = _as_msk(now)
    now_ts = now_m.timestamp()
    important = important_events_today(events, now=now_m)
    if not important:
        return ""
    lines = [f"🗓 <b>Сегодня важно для нефти</b> · {now_m.strftime('%d.%m')}"]
    for e in important[:5]:
        mark = "🔴" if e.impact == "High" or e.kind in {"eia", "nfp", "speech"} else "🟡"
        lines.append(f"{mark} {e.title_ru} · {_fmt_when(e.when_ts, now_ts)}")
    lines.append("<i>У релизов — без новых входов</i>")
    return "\n".join(lines)


def format_morning_desk_brief(
    events: Sequence[OilCalendarEvent] | None = None,
    *,
    now: datetime | None = None,
    night_headlines: Sequence[str] | None = None,
) -> str:
    """Короткий DESK на сегодня → админу. Весь текст по-русски."""
    now_m = _as_msk(now)
    now_ts = now_m.timestamp()
    day_end = datetime(
        now_m.year, now_m.month, now_m.day, 23, 59, 59, tzinfo=_MSK
    ).timestamp()
    tomorrow_end = day_end + 24 * 3600.0
    # Смотрим дальше, чтобы «завтра» не пропало
    evs = list(events) if events is not None else upcoming_oil_events(
        now=now_m, horizon_hours=48.0
    )

    today = [e for e in evs if e.when_ts <= day_end + 1800]
    tomorrow = [e for e in evs if day_end + 1800 < e.when_ts <= tomorrow_end]

    oil_kinds = {"eia", "api", "inventory", "opec"}
    oil_today = [e for e in today if e.kind in oil_kinds]
    speech_today = [e for e in today if e.kind == "speech"]
    macro_today = [
        e for e in today
        if e.kind not in oil_kinds and e.kind != "speech"
        and (e.impact in {"High", "Medium"} or e.kind in {"nfp", "cpi", "ppi", "fed"})
    ]

    # Завтра: всё важное (нефть, макро High, речи, FOMC)
    def _is_important(e: OilCalendarEvent) -> bool:
        if e.kind in oil_kinds | {"speech", "fed", "nfp", "cpi", "ppi", "opec"}:
            return True
        return e.impact == "High"

    tomorrow_imp = [e for e in tomorrow if _is_important(e)][:6]

    wd = _WD_RU[now_m.weekday()]
    lines = [
        f"🗓 <b>DESK UKOUSD</b> · {wd} {now_m.strftime('%d.%m')} · 08:00 МСК",
    ]

    lines.append("")
    lines.append("<b>Нефть сегодня</b>")
    if oil_today:
        for e in oil_today[:4]:
            lines.append(f"• {e.title_ru} · {_fmt_when(e.when_ts, now_ts)}")
    else:
        lines.append("• EIA/API на сегодня в календаре нет")

    if speech_today:
        lines.append("")
        lines.append("<b>Кто говорит (риск)</b>")
        for e in speech_today[:4]:
            lines.append(f"• {e.title_ru} · {_fmt_when(e.when_ts, now_ts)}")

    if macro_today:
        lines.append("")
        lines.append("<b>Макро → доллар / нефть</b>")
        for e in macro_today[:5]:
            mark = "🔴" if e.impact == "High" else "🟡"
            lines.append(f"• {mark} {e.title_ru} · {_fmt_when(e.when_ts, now_ts)}")

    # Всегда показываем завтра, если есть что сказать
    lines.append("")
    lines.append("<b>Завтра</b>")
    if tomorrow_imp:
        for e in tomorrow_imp:
            mark = "🔴" if e.impact == "High" or e.kind in {"eia", "nfp", "fed", "speech"} else "🟡"
            lines.append(f"• {mark} {e.title_ru} · {_fmt_when(e.when_ts, now_ts)}")
    else:
        lines.append("• важных релизов / заседаний в календаре пока нет")

    if night_headlines:
        lines.append("")
        lines.append("<b>Ночь (фон)</b>")
        for h in list(night_headlines)[:2]:
            # уже могли перевести снаружи; на всякий случай ещё раз
            ru = h if re.search(r"[А-Яа-яЁё]", h or "") else _headline_ru_short(h)
            lines.append(f"• {ru[:110]}")

    lines.append("")
    lines.append(
        "<i>Режим: между релизами — график и уровни. "
        "За 15–30 мин до события и сразу после — ждать, без новых входов. "
        "После срочной новости — подождать подтверждение на 5‑мин свече.</i>"
    )
    return "\n".join(lines)


# backward-compatible alias
def format_morning_calendar_brief(
    events: Sequence[OilCalendarEvent] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    return format_morning_desk_brief(events, now=now)
