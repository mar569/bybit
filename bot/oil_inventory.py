"""Запасы США: SPR + коммерческая нефть (EIA weekly) — цифры и «мало/много»."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

import aiohttp

logger = logging.getLogger(__name__)

_EIA_HIST = (
    "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={series}&f=W"
)
_EIA_WPSR = "https://www.eia.gov/petroleum/supply/weekly/"
_SPR_PAGE = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=WCSSTUS1&f=W"
_COMM_PAGE = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=WCESTUS1&f=W"
_CUSHING_PAGE = (
    "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=W_EPC0_SAX_YCUOK_MBBL&f=W"
)

# Ориентиры (млн барр.) — для «мало/много», не жёсткие истины
_SPR_PEAK_MB = 726.0
_SPR_CAPACITY_MB = 714.0
_SPR_COMFORT_MB = 500.0  # «нормальный» запас до больших релизов
_SPR_LOW_MB = 350.0
_COMM_TYPICAL_MB = 430.0  # грубо mid-range excl SPR
_CUSHING_LOW_MB = 22.0
_CUSHING_HIGH_MB = 40.0


@dataclass(frozen=True)
class InventoryPoint:
    date_label: str  # MM/DD or YYYY-MM-DD
    mbbl: float  # million barrels


@dataclass(frozen=True)
class SeriesSnapshot:
    name: str
    series_id: str
    latest: InventoryPoint
    prev: InventoryPoint | None
    wow_mb: float | None
    points: tuple[InventoryPoint, ...]
    release_ru: str = ""


@dataclass(frozen=True)
class UsOilInventoryStatus:
    spr: SeriesSnapshot | None
    commercial: SeriesSnapshot | None
    cushing: SeriesSnapshot | None
    summary_ru: str
    verdict_ru: str
    watch_ru: str
    surprise_ru: str
    confidence: int


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def parse_eia_hist_html(html: str, *, max_points: int = 8) -> list[InventoryPoint]:
    """Парсит таблицу LeafHandler → последние weekly точки (новые в конце)."""
    if not html:
        return []
    # Строка месяца: 2026-Jul + пары 07/24 · 307,650
    row_re = re.compile(
        r"class='B6'>&nbsp;&nbsp;(\d{4})-([A-Za-z]{3})</td>(.*?)</tr>",
        re.IGNORECASE | re.DOTALL,
    )
    cell_re = re.compile(
        r"class='B5'>(\d{2}/\d{2})&nbsp;</td>\s*"
        r"<td class='B3'>([\d,]+)&nbsp;",
        re.IGNORECASE,
    )
    month_num = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    points: list[InventoryPoint] = []
    for m in row_re.finditer(html):
        year = int(m.group(1))
        mon = month_num.get(m.group(2).lower()[:3])
        if not mon:
            continue
        chunk = m.group(3)
        for cm in cell_re.finditer(chunk):
            md = cm.group(1)
            raw = cm.group(2).replace(",", "")
            try:
                kbbl = float(raw)
            except ValueError:
                continue
            mm, dd = md.split("/")
            label = f"{year}-{int(mm):02d}-{int(dd):02d}"
            points.append(InventoryPoint(date_label=label, mbbl=kbbl / 1000.0))
    return points[-max_points:]


def _release_note(html: str) -> str:
    rel = re.search(r"Release Date:\s*([^<\n]+)", html or "", re.I)
    nxt = re.search(r"Next Release Date:\s*([^<\n]+)", html or "", re.I)
    bits = []
    if rel:
        bits.append(f"опубликовано {rel.group(1).strip()}")
    if nxt:
        bits.append(f"следующий отчёт {nxt.group(1).strip()}")
    return " · ".join(bits)


def _snapshot(
    name: str,
    series_id: str,
    points: Sequence[InventoryPoint],
    *,
    release_ru: str = "",
) -> SeriesSnapshot | None:
    if not points:
        return None
    latest = points[-1]
    prev = points[-2] if len(points) >= 2 else None
    wow = (latest.mbbl - prev.mbbl) if prev else None
    return SeriesSnapshot(
        name=name,
        series_id=series_id,
        latest=latest,
        prev=prev,
        wow_mb=wow,
        points=tuple(points),
        release_ru=release_ru,
    )


def interpret_spr(mb: float, wow: float | None) -> tuple[str, str]:
    """(уровень_ru, смысл_для_цены) — простыми словами."""
    pct_cap = 100.0 * mb / _SPR_CAPACITY_MB
    if mb < _SPR_LOW_MB:
        level = f"очень мало (заполнено примерно на {pct_cap:.0f}%)"
        sense = (
            "госрезерв тонкий: если случится геошок (Иран/пролив), "
            "у США меньше «подушки», чтобы быстро выкинуть нефть на рынок"
        )
    elif mb < _SPR_COMFORT_MB:
        level = f"ниже привычного уровня (заполнено примерно на {pct_cap:.0f}%)"
        sense = "запас ещё не вернулся к «норме» спокойных лет"
    else:
        level = f"относительно нормально (заполнено примерно на {pct_cap:.0f}%)"
        sense = (
            "при шоке у США есть чем «залить» рынок — это может сдерживать рост цены"
        )
    wow_ru = ""
    if wow is not None:
        if wow <= -2.0:
            wow_ru = (
                f" За неделю из резерва ушло {abs(wow):.1f} млн баррелей — "
                f"обычно это скорее поддержка цене (нефти на складе стало меньше)."
            )
        elif wow >= 2.0:
            wow_ru = (
                f" За неделю в резерв залили {wow:.1f} млн баррелей — "
                f"чаще это давление вниз на цену."
            )
        else:
            wow_ru = f" За неделю почти без изменений ({wow:+.1f} млн баррелей)."
    return level, sense + "." + wow_ru


def interpret_commercial(mb: float, wow: float | None) -> tuple[str, str]:
    vs = mb - _COMM_TYPICAL_MB
    if vs <= -25:
        level = "на складах компаний меньше обычного"
        sense = (
            "рынок «тугой»: если в отчёте в среду запасов убавится сильнее ожидания — "
            "цене чаще легче расти"
        )
    elif vs >= 25:
        level = "на складах компаний больше обычного"
        sense = (
            "нефти много: если запасы ещё вырастут сильнее ожидания — "
            "легче давление вниз на цену"
        )
    else:
        level = "на складах компаний примерно как обычно"
        sense = "по складу фон спокойный, сам по себе мало двигает цену"
    if wow is not None:
        if wow <= -3.0:
            sense += (
                f". За неделю запасов убавилось на {abs(wow):.1f} млн баррелей — "
                f"для цены на часы/дни это обычно плюс (поддержка)."
            )
        elif wow >= 3.0:
            sense += (
                f". За неделю запасов прибавилось на {wow:.1f} млн баррелей — "
                f"для цены на часы/дни это обычно минус (давление вниз)."
            )
        else:
            sense += (
                f". За неделю изменение небольшое ({wow:+.1f} млн) — без шока."
            )
    return level, sense


def interpret_cushing(mb: float, wow: float | None) -> str:
    """Хаб Cushing (Оклахома) — главный склад для американской нефти WTI."""
    if mb <= _CUSHING_LOW_MB:
        base = (
            "на главном складе США (Кашинг) мало нефти — "
            "американская нефть (WTI) сильнее реагирует, если запасов ещё убавится"
        )
    elif mb >= _CUSHING_HIGH_MB:
        base = (
            "на главном складе США (Кашинг) много нефти — "
            "при росте запасов WTI проще давить вниз"
        )
    else:
        base = "на главном складе США (Кашинг) запасы в середине диапазона"
    if wow is not None:
        if wow <= -0.5:
            return f"{base}. За неделю −{abs(wow):.1f} млн баррелей."
        if wow >= 0.5:
            return f"{base}. За неделю +{wow:.1f} млн баррелей."
        return f"{base}. За неделю почти без изменений ({wow:+.1f} млн)."
    return base


def build_inventory_status(
    *,
    spr: SeriesSnapshot | None,
    commercial: SeriesSnapshot | None,
    cushing: SeriesSnapshot | None,
) -> UsOilInventoryStatus:
    bits: list[str] = []
    verdict_bits: list[str] = []
    watch: list[str] = []
    surprise: list[str] = []

    if spr:
        lvl, sense = interpret_spr(spr.latest.mbbl, spr.wow_mb)
        bits.append(
            f"<b>Госрезерв США</b> (стратегический запас на ЧП): "
            f"<b>{spr.latest.mbbl:.1f}</b> млн баррелей на {spr.latest.date_label} — {lvl}."
        )
        bits.append(sense)
        verdict_bits.append(lvl)
        if spr.wow_mb is not None and abs(spr.wow_mb) >= 2:
            surprise.append(
                "резкий ход госрезерва (выброс нефти на рынок или закупка обратно)"
            )
        watch.append(
            "сообщения минэнерго США: выкинут ли нефть из резерва или начнут покупать"
        )

    if commercial:
        lvl, sense = interpret_commercial(commercial.latest.mbbl, commercial.wow_mb)
        bits.append(
            f"<b>Склады компаний</b> (обычные коммерческие запасы, без госрезерва): "
            f"<b>{commercial.latest.mbbl:.1f}</b> млн баррелей "
            f"на {commercial.latest.date_label} — {lvl}."
        )
        bits.append(sense)
        verdict_bits.append(lvl)
        if commercial.wow_mb is not None and abs(commercial.wow_mb) >= 3:
            surprise.append(
                "в среду отчёт по запасам сильнее/слабее, чем ждал рынок"
            )
        watch.append(
            "еженедельный отчёт по запасам США (обычно среда ~17:30 по Москве)"
        )

    if cushing:
        bits.append(
            f"<b>Главный склад США (Кашинг)</b>: <b>{cushing.latest.mbbl:.1f}</b> млн баррелей — "
            f"{interpret_cushing(cushing.latest.mbbl, cushing.wow_mb)}."
        )

    if not bits:
        return UsOilInventoryStatus(
            spr=None,
            commercial=None,
            cushing=None,
            summary_ru=(
                "Не удалось загрузить цифры с сайта энергостатистики США. "
                "Открой отчёт по ссылке ниже."
            ),
            verdict_ru="нет данных",
            watch_ru="Открыть еженедельный отчёт по запасам США",
            surprise_ru="Без цифр — ориентируйся только на заголовки новостей",
            confidence=2,
        )

    # Итог для цены
    bull = bear = 0
    if spr and spr.latest.mbbl < _SPR_LOW_MB:
        bull += 1
    if spr and spr.wow_mb is not None and spr.wow_mb <= -2:
        bull += 1
    if spr and spr.wow_mb is not None and spr.wow_mb >= 2:
        bear += 1
    if commercial and commercial.wow_mb is not None:
        if commercial.wow_mb <= -3:
            bull += 1
        elif commercial.wow_mb >= 3:
            bear += 1

    if bull > bear:
        price_hint = (
            "по складам фон скорее за рост цены "
            "(нефти на складах мало / госрезерв тонкий)"
        )
    elif bear > bull:
        price_hint = (
            "по складам фон скорее за снижение цены "
            "(запасов много / госрезерв пополняют)"
        )
    else:
        price_hint = (
            "по складам картина смешанная — сильнее решают новости "
            "(пролив/Трамп) и сюрприз в среду vs то, что ждал рынок"
        )

    conf = 7 if spr and commercial else 5 if spr or commercial else 3
    return UsOilInventoryStatus(
        spr=spr,
        commercial=commercial,
        cushing=cushing,
        summary_ru=" ".join(bits),
        verdict_ru=price_hint,
        watch_ru="; ".join(watch) if watch else "еженедельный отчёт по запасам США",
        surprise_ru=(
            "; ".join(surprise)
            if surprise
            else "обычный отчёт без сильного сюрприза по складу"
        ),
        confidence=conf,
    )


async def _fetch_hist(series: str) -> tuple[str, list[InventoryPoint]]:
    url = _EIA_HIST.format(series=series)
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={"User-Agent": "BybitBotOil/1.0"},
            ) as resp:
                if resp.status != 200:
                    return "", []
                html = await resp.text()
        return html, parse_eia_hist_html(html)
    except Exception:
        logger.debug("EIA hist fetch failed %s", series, exc_info=True)
        return "", []


async def fetch_us_oil_inventory() -> UsOilInventoryStatus:
    """Живые weekly цифры с eia.gov (без API key)."""
    spr_html, spr_pts = await _fetch_hist("WCSSTUS1")
    comm_html, comm_pts = await _fetch_hist("WCESTUS1")
    cush_html, cush_pts = await _fetch_hist("W_EPC0_SAX_YCUOK_MBBL")

    spr = _snapshot(
        "SPR", "WCSSTUS1", spr_pts, release_ru=_release_note(spr_html)
    )
    commercial = _snapshot(
        "Commercial crude", "WCESTUS1", comm_pts, release_ru=_release_note(comm_html)
    )
    cushing = _snapshot(
        "Cushing", "W_EPC0_SAX_YCUOK_MBBL", cush_pts, release_ru=_release_note(cush_html)
    )
    return build_inventory_status(spr=spr, commercial=commercial, cushing=cushing)


def format_inventory_status(st: UsOilInventoryStatus) -> str:
    lines = [
        "📦 <b>Запасы нефти в США</b>",
        f"Насколько цифрам можно верить: {st.confidence}/10",
        "",
        "<i>Коротко словами:</i>",
        "• <b>Госрезерв</b> — стратегический запас США на ЧП (его ещё называют SPR).",
        "• <b>Склады компаний</b> — обычная нефть у рынка, не у государства.",
        "• <b>Кашинг</b> — главный складский хаб США; от него сильнее зависит "
        "американская нефть (WTI).",
        "• Если запасов <b>убавилось</b> — чаще поддержка цене; "
        "если <b>прибавилось</b> — чаще давление вниз.",
        "",
        st.summary_ru,
        "",
        f"<b>Итог для нефти:</b> {st.verdict_ru}",
        f"<b>Что ждать:</b> {_esc(st.watch_ru)}",
        f"<b>Где может быть сюрприз:</b> {_esc(st.surprise_ru)}",
        "",
        "<b>Ссылки на цифры</b>",
        f"• <a href=\"{_SPR_PAGE}\">Госрезерв США</a>",
        f"• <a href=\"{_COMM_PAGE}\">Склады компаний</a>",
        f"• <a href=\"{_CUSHING_PAGE}\">Главный склад (Кашинг)</a>",
        f"• <a href=\"{_EIA_WPSR}\">Полный еженедельный отчёт</a>",
        "",
        "<i>Цифры за неделю, с задержкой в несколько дней. Для сделки важнее, "
        "насколько среда удивит рынок (сильнее/слабее ждали), плюс геоновости "
        "(пролив Ормуз / Трамп). Один только склад редко даёт ход ±5%.</i>",
    ]
    if st.spr and st.spr.release_ru:
        lines.insert(2, f"<i>{_esc(st.spr.release_ru)}</i>")
    return "\n".join(lines)


def format_inventory_short(st: UsOilInventoryStatus) -> str:
    """Короткий блок для вставки в Ормуз / дайджест."""
    parts: list[str] = []
    if st.spr:
        wow = f" ({st.spr.wow_mb:+.1f})" if st.spr.wow_mb is not None else ""
        parts.append(f"госрезерв {st.spr.latest.mbbl:.0f} млн{wow}")
    if st.commercial:
        wow = (
            f" ({st.commercial.wow_mb:+.1f})"
            if st.commercial.wow_mb is not None
            else ""
        )
        parts.append(f"склады {st.commercial.latest.mbbl:.0f} млн{wow}")
    if not parts:
        return ""
    return "📦 " + " · ".join(parts) + f" — {st.verdict_ru}"
