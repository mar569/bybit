"""Ормуз: живое скопление судов (AIS) + дневные транзиты (IMF PortWatch) + новости."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Sequence

import aiohttp

logger = logging.getLogger(__name__)

# «Как по телевизору» — карты AIS глазами
_MARINETRAFFIC = (
    "https://www.marinetraffic.com/en/ais/home/centerx:56.5/centery:26.5/zoom:8"
)
_VESSELFINDER = "https://www.vesselfinder.com/?center=26.567,56.250&zoom=8"
_STRAITS_LIVE = "https://straits.live/"
_STRAITS_VESSELS = "https://straits.live/vessels"

# Открытый JSON-снимок straits.live (без ключа)
_STRAITS_STATUS_URL = (
    "https://raw.githubusercontent.com/jasonhjohnson/strait-of-hormuz-data/"
    "main/data/status.json"
)
# IMF PortWatch — дневные транзиты через Ормуз (chokepoint6), лаг ~4–7 дней
_PORTWATCH_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
    "?where=portid%3D%27chokepoint6%27"
    "&outFields=date%2Cn_tanker%2Cn_cargo%2Cn_total"
    "&orderByFields=date%20DESC"
    "&resultRecordCount=5"
    "&f=json"
)


@dataclass(frozen=True)
class HormuzStatus:
    traffic: str  # open | restricted | disrupted | closed | unknown
    risk: str  # low | elevated | high | critical | unknown
    summary_ru: str
    evidence_ru: tuple[str, ...]
    sources_ru: tuple[str, ...]
    live_links_ru: str
    confidence: int  # 1–10
    # Живые цифры (если есть)
    vessels_in_zone: int | None = None
    tankers_dark: int | None = None
    stranded: int | None = None
    transits_day: int | None = None
    transits_baseline: int | None = None
    tankers_day: int | None = None
    as_of_ru: str = ""


_OPEN_KW = (
    "reopen", "reopened", "transit resume", "tankers pass", "shipping resumes",
    "traffic normal", "flows recover", "open strait", "open hormuz",
    "проходят", "открыт", "судоход", "возобнов",
)
_RESTRICT_KW = (
    "restricted", "slow", "convoy", "war risk", "insurance", "caution",
    "limited traffic", "частич", "огранич", "конвой", "страхов",
)
_CLOSED_KW = (
    "closed", "blockade", "blocked", "halted", "no transit", "strait closed",
    "shipping halted", "закрыт", "блок", "остановлен", "не пропуска",
)
_DISRUPT_KW = (
    "disrupted", "disruption", "attack on tanker", "hit tanker", "seizure",
    "mine", "сбой", "атака на танкер", "захват",
)


def _links_block() -> str:
    return (
        "<b>Смотреть «как по ТВ»</b>\n"
        f"• <a href=\"{_STRAITS_LIVE}\">straits.live — сводка Ормуза</a>\n"
        f"• <a href=\"{_STRAITS_VESSELS}\">карта судов в зоне</a>\n"
        f"• <a href=\"{_MARINETRAFFIC}\">MarineTraffic · Ормуз</a>\n"
        f"• <a href=\"{_VESSELFINDER}\">VesselFinder · Ормуз</a>"
    )


def infer_hormuz_from_news(items: Sequence[Any]) -> HormuzStatus:
    """Запасной вариант: только заголовки."""
    open_n = restrict_n = closed_n = disrupt_n = 0
    evidence: list[str] = []
    for it in items or []:
        title = (getattr(it, "title", "") or "").strip()
        if not title:
            continue
        low = title.lower()
        if not any(
            k in low for k in ("hormuz", "ормуз", "strait", "tanker", "танкер", "shipping")
        ):
            continue
        hit = None
        if any(k in low for k in _CLOSED_KW):
            closed_n += 1
            hit = "closed"
        elif any(k in low for k in _DISRUPT_KW):
            disrupt_n += 1
            hit = "disrupt"
        elif any(k in low for k in _RESTRICT_KW):
            restrict_n += 1
            hit = "restrict"
        elif any(k in low for k in _OPEN_KW):
            open_n += 1
            hit = "open"
        if hit:
            evidence.append(title[:120])
        if len(evidence) >= 5:
            break

    total = open_n + restrict_n + closed_n + disrupt_n
    if total == 0:
        return HormuzStatus(
            traffic="unknown",
            risk="unknown",
            summary_ru=(
                "По ленте бота нет ясного сигнала. Открой живую карту ниже — "
                "там видно скопление судов, как на ТВ."
            ),
            evidence_ru=(),
            sources_ru=("только новости бота",),
            live_links_ru=_links_block(),
            confidence=2,
        )

    if closed_n >= max(open_n, disrupt_n, restrict_n) and closed_n > 0:
        traffic, risk, conf = "closed", "critical", 6
        summary = (
            "По заголовкам — пролив/трафик <b>закрыт или заблокирован</b>. "
            "Скопление танкеров обычно растёт у входа / на якоре."
        )
    elif disrupt_n > open_n:
        traffic, risk, conf = "disrupted", "high", 6
        summary = "По заголовкам — сбои и риски для танкеров."
    elif restrict_n > open_n:
        traffic, risk, conf = "restricted", "elevated", 5
        summary = "По заголовкам — ходят с ограничениями (конвои, страховки)."
    else:
        traffic, risk, conf = "open", "elevated", 5
        summary = "По заголовкам — сигналы, что судоходство восстанавливается."

    return HormuzStatus(
        traffic=traffic,
        risk=risk,
        summary_ru=summary,
        evidence_ru=tuple(evidence[:4]),
        sources_ru=("эвристика по заголовкам",),
        live_links_ru=_links_block(),
        confidence=conf,
    )


async def fetch_straits_live_status() -> dict[str, Any] | None:
    """Живой снимок straits.live (AIS concurrent / stranded / verdict)."""
    try:
        timeout = aiohttp.ClientTimeout(total=18)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_STRAITS_STATUS_URL) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("straits.live status fetch failed", exc_info=True)
        return None


async def fetch_portwatch_hormuz() -> dict[str, Any] | None:
    """Последний день транзитов IMF PortWatch (танкеры отдельно)."""
    try:
        timeout = aiohttp.ClientTimeout(total=18)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_PORTWATCH_URL) as resp:
                if resp.status != 200:
                    return None
                body = await resp.json(content_type=None)
        feats = (body or {}).get("features") or []
        if not feats:
            return None
        attrs = (feats[0] or {}).get("attributes") or {}
        return {
            "date": attrs.get("date"),
            "n_tanker": attrs.get("n_tanker"),
            "n_cargo": attrs.get("n_cargo"),
            "n_total": attrs.get("n_total"),
        }
    except Exception:
        logger.debug("PortWatch fetch failed", exc_info=True)
        return None


def _status_from_straits(data: dict[str, Any], portwatch: dict[str, Any] | None) -> HormuzStatus:
    verdict = data.get("verdict") or {}
    v_status = str(verdict.get("status") or "").lower()
    if v_status == "closed":
        traffic, risk = "closed", "critical"
    elif v_status in {"restricted", "limited"}:
        traffic, risk = "restricted", "high"
    elif v_status in {"open", "normal"}:
        traffic, risk = "open", "elevated"
    else:
        traffic, risk = "disrupted", "high"

    vessels = data.get("aisConcurrentInZone")
    if vessels is None:
        vessels = data.get("shipsTransiting")
    try:
        vessels_i = int(vessels) if vessels is not None else None
    except (TypeError, ValueError):
        vessels_i = None

    stranded = data.get("strandedOffshore")
    if stranded is None:
        stranded = data.get("stranded")
    try:
        stranded_i = int(stranded) if stranded is not None else None
    except (TypeError, ValueError):
        stranded_i = None

    gaps = data.get("aisGaps") or {}
    try:
        dark_i = int(gaps.get("count")) if gaps.get("count") is not None else None
    except (TypeError, ValueError):
        dark_i = None

    tr = data.get("transits") or {}
    try:
        tr_count = int(tr.get("count")) if tr.get("count") is not None else None
        tr_base = int(tr.get("baseline")) if tr.get("baseline") is not None else None
    except (TypeError, ValueError):
        tr_count, tr_base = None, None
    tr_date = str(tr.get("asOfDate") or "")

    pw_tankers = pw_total = pw_date = None
    if portwatch:
        pw_date = str(portwatch.get("date") or "")
        try:
            pw_tankers = int(portwatch["n_tanker"]) if portwatch.get("n_tanker") is not None else None
            pw_total = int(portwatch["n_total"]) if portwatch.get("n_total") is not None else None
        except (TypeError, ValueError):
            pass
        if pw_total is not None:
            tr_count = pw_total
        if pw_date:
            tr_date = pw_date

    crisis = ((data.get("hormuzIndex") or {}).get("crisisPressure") or {})
    band = str(crisis.get("band") or "")
    score = crisis.get("score") or crisis.get("value")

    # Простыми словами
    if traffic == "closed":
        head = "Сейчас коммерческий проход через Ормуз <b>фактически закрыт / сильно сжат</b>."
    elif traffic == "restricted":
        head = "Судоходство <b>ограничено</b> (конвои / страховки / выборочный проход)."
    elif traffic == "open":
        head = "По сводке — пролив скорее <b>открыт</b> для коммерции."
    else:
        head = "Картина <b>нарушена</b>, смотри цифры скопления ниже."

    bits = [head]
    if vessels_i is not None:
        bits.append(
            f"В зоне Ормуза/Персидского залива сейчас на AIS видно примерно "
            f"<b>{vessels_i}</b> судов (скольжение «в кадре», не все — проход за день)."
        )
    if stranded_i is not None:
        bits.append(
            f"Стоят / ждут (не у причала): около <b>{stranded_i}</b> — это и есть "
            f"«скопление», которое показывают по ТВ."
        )
    if dark_i is not None:
        bits.append(
            f"Танкеры «погасили» AIS (dark): около <b>{dark_i}</b> — реальный трафик "
            f"может быть выше видимого."
        )
    if tr_count is not None:
        base = f" (норма до кризиса ≈{tr_base}/день)" if tr_base else ""
        day = f" на {tr_date}" if tr_date else ""
        tank = f", из них танкеров ≈{pw_tankers}" if pw_tankers is not None else ""
        bits.append(
            f"Официальные дневные проходы (IMF PortWatch){day}: "
            f"<b>{tr_count}</b>{tank}{base}."
        )
    if band:
        bits.append(f"Индекс давления кризиса: <b>{band}</b>" + (f" ({score})" if score else "") + ".")

    as_of = str(data.get("asOf") or "")[:19].replace("T", " ")
    evidence = []
    if vessels_i is not None:
        evidence.append(f"Суда в зоне (AIS): {vessels_i}")
    if stranded_i is not None:
        evidence.append(f"Ждут / стоят: {stranded_i}")
    if dark_i is not None:
        evidence.append(f"Dark-танкеры: {dark_i}")
    if tr_count is not None:
        evidence.append(f"Проходы/день: {tr_count}" + (f" (танкеры {pw_tankers})" if pw_tankers is not None else ""))
    if verdict.get("long"):
        evidence.append(str(verdict.get("long"))[:160])

    return HormuzStatus(
        traffic=traffic,
        risk=risk,
        summary_ru=" ".join(bits),
        evidence_ru=tuple(evidence[:6]),
        sources_ru=(
            "straits.live (AIS snapshot)",
            "IMF PortWatch (дневные транзиты, лаг несколько дней)",
        ),
        live_links_ru=_links_block(),
        confidence=8,
        vessels_in_zone=vessels_i,
        tankers_dark=dark_i,
        stranded=stranded_i,
        transits_day=tr_count,
        transits_baseline=tr_base,
        tankers_day=pw_tankers,
        as_of_ru=as_of,
    )


async def fetch_hormuz_api_status(api_key: str | None = None) -> HormuzStatus | None:
    """Опционально: HORMUZ_API_KEY → api.hormuzmonitor.com."""
    key = (api_key or os.getenv("HORMUZ_API_KEY") or "").strip()
    if not key:
        return None
    base = "https://api.hormuzmonitor.com/v2"
    headers = {"X-API-Key": key, "Accept": "application/json"}
    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            traffic_data: dict = {}
            crisis_data: dict = {}
            async with session.get(f"{base}/traffic", headers=headers) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    traffic_data = body.get("data") or body
            async with session.get(f"{base}/crisis", headers=headers) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    crisis_data = body.get("data") or body
        if not traffic_data and not crisis_data:
            return None
        inbound = str(traffic_data.get("inbound_lane_status") or "").lower()
        outbound = str(traffic_data.get("outbound_lane_status") or "").lower()
        tstat = str(traffic_data.get("traffic_status") or "").lower()
        today = traffic_data.get("transits_today")
        if "closed" in (inbound, outbound, tstat):
            traffic = "closed"
        elif "severely" in tstat or "disrupted" in tstat:
            traffic = "disrupted"
        elif "restrict" in tstat or "restrict" in inbound:
            traffic = "restricted"
        elif "open" in (inbound, outbound) or "normal" in tstat:
            traffic = "open"
        else:
            traffic = "unknown"
        sev = str(crisis_data.get("severity") or "").lower()
        risk = sev if sev in {"low", "elevated", "high", "critical"} else "unknown"
        summary = crisis_data.get("summary") or f"Трафик API: {tstat or traffic}"
        evidence = []
        if today is not None:
            evidence.append(f"Транзиты сегодня: {today}")
        return HormuzStatus(
            traffic=traffic,
            risk=risk,
            summary_ru=str(summary)[:500],
            evidence_ru=tuple(evidence),
            sources_ru=("Hormuz Monitor API",),
            live_links_ru=_links_block(),
            confidence=8,
            transits_day=int(today) if today is not None else None,
        )
    except Exception:
        logger.debug("Hormuz API fetch failed", exc_info=True)
        return None


async def build_hormuz_status(
    news_items: Sequence[Any],
    *,
    api_key: str | None = None,
) -> HormuzStatus:
    """Приоритет: straits.live AIS → optional API → новости."""
    # 1) Живой снимок «как по ТВ»
    straits = await fetch_straits_live_status()
    portwatch = await fetch_portwatch_hormuz()
    if straits:
        return _status_from_straits(straits, portwatch)

    # 2) Платный/регистрационный API если есть ключ
    api = await fetch_hormuz_api_status(api_key)
    if api is not None:
        return api

    # 3) Только PortWatch без AIS
    if portwatch and portwatch.get("n_total") is not None:
        total = int(portwatch["n_total"])
        tankers = portwatch.get("n_tanker")
        base = 88
        pct = round(100.0 * total / base) if base else 0
        if total < base * 0.25:
            traffic, risk = "closed", "critical"
        elif total < base * 0.6:
            traffic, risk = "restricted", "high"
        else:
            traffic, risk = "open", "elevated"
        return HormuzStatus(
            traffic=traffic,
            risk=risk,
            summary_ru=(
                f"По IMF PortWatch за {portwatch.get('date')}: прошло "
                f"<b>{total}</b> судов"
                + (f" (танкеров {tankers})" if tankers is not None else "")
                + f" — это ≈{pct}% от докризисных ~{base}/день. "
                "Это не секундный AIS, а дневной официальный счётчик (лаг несколько дней)."
            ),
            evidence_ru=(
                f"Дата: {portwatch.get('date')}",
                f"Всего: {total}",
                f"Танкеры: {tankers}",
            ),
            sources_ru=("IMF PortWatch",),
            live_links_ru=_links_block(),
            confidence=6,
            transits_day=total,
            transits_baseline=base,
            tankers_day=int(tankers) if tankers is not None else None,
            as_of_ru=str(portwatch.get("date") or ""),
        )

    return infer_hormuz_from_news(news_items)


@dataclass(frozen=True)
class HormuzAlert:
    """Важное изменение — кидать в Новостник."""

    reasons_ru: tuple[str, ...]
    trade_critical: bool  # смена open↔closed / critical — ещё и в ручной TA
    bias_hint: str  # bullish | bearish | neutral
    message_html: str


def detect_hormuz_alert(
    prev: HormuzStatus | None,
    curr: HormuzStatus,
    *,
    first_shot_if_tense: bool = True,
) -> HormuzAlert | None:
    """
    Алерт только если это важно для понимания цены нефти:
    смена статуса пролива, скачок скопления, критический риск.
    Первый снимок — тихий baseline, кроме уже закрыто/critical.
    """
    if curr.traffic == "unknown" and curr.vessels_in_zone is None and curr.stranded is None:
        return None

    reasons: list[str] = []
    trade_critical = False
    bias = "neutral"

    if prev is None:
        if not first_shot_if_tense:
            return None
        # Первый раз — только если уже «горит»
        if curr.traffic in {"closed", "disrupted"} or curr.risk == "critical":
            if curr.traffic == "closed":
                reasons.append("Ормуз сейчас фактически закрыт / сильно сжат")
                bias = "bullish"
                trade_critical = True
            elif curr.traffic == "disrupted":
                reasons.append("Судоходство через Ормуз нарушено")
                bias = "bullish"
                trade_critical = True
            else:
                reasons.append("Риск по Ормузу критический")
                bias = "bullish"
            if curr.stranded is not None and curr.stranded >= 150:
                reasons.append(f"Скопление стоящих судов ≈{curr.stranded}")
            if not reasons:
                return None
            return HormuzAlert(
                reasons_ru=tuple(reasons),
                trade_critical=trade_critical,
                bias_hint=bias,
                message_html=format_hormuz_news_alert(curr, reasons, bias_hint=bias),
            )
        return None

    # Смена статуса трафика
    if prev.traffic != curr.traffic and curr.traffic != "unknown":
        label = {
            "open": "открыт / ходят",
            "restricted": "ограничен",
            "disrupted": "сбои",
            "closed": "закрыт / сжат",
        }.get(curr.traffic, curr.traffic)
        reasons.append(f"Статус пролива: {prev.traffic} → <b>{label}</b>")
        if curr.traffic in {"closed", "disrupted"}:
            bias = "bullish"
            trade_critical = True
        elif curr.traffic == "open" and prev.traffic in {"closed", "disrupted", "restricted"}:
            bias = "bearish"
            trade_critical = True
        elif curr.traffic == "restricted":
            bias = "bullish"

    if prev.risk != curr.risk and curr.risk in {"high", "critical"}:
        reasons.append(f"Риск: {prev.risk} → <b>{curr.risk}</b>")
        if curr.risk == "critical":
            bias = "bullish" if bias == "neutral" else bias
            trade_critical = True

    # Скачок «скопления» (то, что показывают по ТВ)
    if prev.stranded is not None and curr.stranded is not None:
        delta = curr.stranded - prev.stranded
        if abs(delta) >= 40 or (
            prev.stranded > 0 and abs(delta) / max(prev.stranded, 1) >= 0.25 and abs(delta) >= 25
        ):
            arrow = "↑" if delta > 0 else "↓"
            reasons.append(
                f"Скопление судов {arrow}: {prev.stranded} → <b>{curr.stranded}</b> ({delta:+d})"
            )
            if delta > 0 and bias == "neutral":
                bias = "bullish"
            elif delta < 0 and bias == "neutral":
                bias = "bearish"
            if abs(delta) >= 80:
                trade_critical = True

    if prev.vessels_in_zone is not None and curr.vessels_in_zone is not None:
        delta_v = curr.vessels_in_zone - prev.vessels_in_zone
        if abs(delta_v) >= 50:
            arrow = "↑" if delta_v > 0 else "↓"
            reasons.append(
                f"В кадре AIS {arrow}: {prev.vessels_in_zone} → <b>{curr.vessels_in_zone}</b>"
            )

    if prev.tankers_dark is not None and curr.tankers_dark is not None:
        delta_d = curr.tankers_dark - prev.tankers_dark
        if abs(delta_d) >= 15:
            reasons.append(
                f"Dark AIS: {prev.tankers_dark} → <b>{curr.tankers_dark}</b> ({delta_d:+d})"
            )

    # Резкий обвал дневных проходов (PortWatch)
    if (
        prev.transits_day is not None
        and curr.transits_day is not None
        and prev.transits_day > 0
        and curr.transits_day <= prev.transits_day * 0.5
        and (prev.transits_day - curr.transits_day) >= 15
    ):
        reasons.append(
            f"Дневные проходы упали: {prev.transits_day} → <b>{curr.transits_day}</b>"
        )
        bias = "bullish"
        trade_critical = True
    elif (
        prev.transits_day is not None
        and curr.transits_day is not None
        and prev.transits_day > 0
        and curr.transits_day >= prev.transits_day * 1.8
        and (curr.transits_day - prev.transits_day) >= 20
    ):
        reasons.append(
            f"Дневные проходы выросли: {prev.transits_day} → <b>{curr.transits_day}</b>"
        )
        bias = "bearish"
        trade_critical = True

    if not reasons:
        return None

    return HormuzAlert(
        reasons_ru=tuple(reasons),
        trade_critical=trade_critical,
        bias_hint=bias,
        message_html=format_hormuz_news_alert(curr, reasons, bias_hint=bias),
    )


def format_hormuz_news_alert(
    st: HormuzStatus,
    reasons: Sequence[str],
    *,
    bias_hint: str = "neutral",
) -> str:
    """Короткий пуш в Новостник — не полная сводка, а «что изменилось»."""
    bias_ru = {
        "bullish": "📈 давление на цену вверх (риск поставок)",
        "bearish": "📉 давление вниз (трафик восстанавливается)",
        "neutral": "↔️ смотри контекст",
    }.get(bias_hint, "↔️")

    lines = [
        "🚢 <b>Ормуз · важно для нефти</b>",
        bias_ru,
        "",
        "<b>Что изменилось</b>",
    ]
    for r in reasons:
        lines.append(f"• {r}")

    nums: list[str] = []
    if st.stranded is not None:
        nums.append(f"скопление <b>{st.stranded}</b>")
    if st.vessels_in_zone is not None:
        nums.append(f"в AIS <b>{st.vessels_in_zone}</b>")
    if st.tankers_dark is not None:
        nums.append(f"dark <b>{st.tankers_dark}</b>")
    if st.transits_day is not None:
        nums.append(f"проходов/день <b>{st.transits_day}</b>")
    if nums:
        lines.append("")
        lines.append("Сейчас: " + " · ".join(nums))

    lines.append("")
    lines.append(
        f"<a href=\"{_STRAITS_LIVE}\">straits.live</a> · "
        f"<a href=\"{_MARINETRAFFIC}\">MarineTraffic</a> · /hormuz"
    )
    if st.as_of_ru:
        lines.append(f"<i>снимок {st.as_of_ru} UTC</i>")
    return "\n".join(lines)


def format_hormuz_status(st: HormuzStatus) -> str:
    traffic_ru = {
        "open": "🟢 ходят / открыто",
        "restricted": "🟡 ограничено",
        "disrupted": "🟠 сбои",
        "closed": "🔴 закрыто / сжато",
        "unknown": "⚪ неясно",
    }.get(st.traffic, st.traffic)
    risk_ru = {
        "low": "низкий",
        "elevated": "повышенный",
        "high": "высокий",
        "critical": "очень высокий",
        "unknown": "неизвестно",
    }.get(st.risk, st.risk)

    lines = [
        "🚢 <b>Ормуз · скопление танкеров (почти как по ТВ)</b>",
        f"Статус: <b>{traffic_ru}</b> · риск <b>{risk_ru}</b> · уверенность {st.confidence}/10",
    ]
    if st.as_of_ru:
        lines.append(f"<i>Данные на: {st.as_of_ru} UTC</i>")
    lines.append("")
    lines.append(st.summary_ru)

    # Крупные цифры сверху — то, что ищут с телевизора
    nums = []
    if st.vessels_in_zone is not None:
        nums.append(f"• В кадре AIS сейчас: <b>{st.vessels_in_zone}</b> судов")
    if st.stranded is not None:
        nums.append(f"• Скопление (стоят/ждут): <b>{st.stranded}</b>")
    if st.tankers_dark is not None:
        nums.append(f"• Танкеры без AIS (dark): <b>{st.tankers_dark}</b>")
    if st.transits_day is not None:
        base = f" из ~{st.transits_baseline}" if st.transits_baseline else ""
        tank = f" · танкеры {st.tankers_day}" if st.tankers_day is not None else ""
        nums.append(f"• Проходов за день (PortWatch): <b>{st.transits_day}</b>{base}{tank}")
    if nums:
        lines.append("")
        lines.append("<b>Цифры</b>")
        lines.extend(nums)

    if st.evidence_ru:
        lines.append("")
        lines.append("<b>Детали</b>")
        for e in st.evidence_ru:
            lines.append(f"• {_esc(e)}")

    lines.append("")
    lines.append("<b>Откуда</b>")
    for s in st.sources_ru:
        lines.append(f"• {_esc(s)}")
    lines.append("")
    lines.append(st.live_links_ru)
    lines.append("")
    lines.append(
        "<i>Важно: AIS — только суда, которые «светятся». Часть танкеров гасит маяк. "
        "PortWatch считает дневные проходы с лагом. Для глаз — карты выше.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
