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
        bits.append(f"релиз {rel.group(1).strip()}")
    if nxt:
        bits.append(f"следующий {nxt.group(1).strip()}")
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
    """(уровень_ru, смысл_для_цены)."""
    pct_cap = 100.0 * mb / _SPR_CAPACITY_MB
    if mb < _SPR_LOW_MB:
        level = f"очень мало (~{pct_cap:.0f}% ёмкости)"
        sense = "буфер SPR тонкий — при геошоке меньше «подушки» на выброс в рынок"
    elif mb < _SPR_COMFORT_MB:
        level = f"ниже комфорта (~{pct_cap:.0f}% ёмкости)"
        sense = "запас ещё не восстановлен до «нормы» докризисных лет"
    else:
        level = f"относительно комфортно (~{pct_cap:.0f}% ёмкости)"
        sense = "есть пространство для релиза при шоке (медвежий фактор при угрозе)"
    wow_ru = ""
    if wow is not None:
        if wow <= -2.0:
            wow_ru = f" сильный отток SPR за неделю ({wow:+.1f} млн) — обычно поддержка цене"
        elif wow >= 2.0:
            wow_ru = f" идёт дозаправка SPR ({wow:+.1f} млн) — чаще давление вниз"
        else:
            wow_ru = f" неделя почти без изменений ({wow:+.1f} млн)"
    return level, sense + "." + wow_ru


def interpret_commercial(mb: float, wow: float | None) -> tuple[str, str]:
    vs = mb - _COMM_TYPICAL_MB
    if vs <= -25:
        level = "коммерческие запасы ниже обычного диапазона"
        sense = "рынок туже → при сюрпризе draw чаще вверх"
    elif vs >= 25:
        level = "коммерческие запасы выше обычного"
        sense = "избыток → при build легче давление вниз"
    else:
        level = "коммерческие запасы около середины диапазона"
        sense = "нейтральный фон по складу"
    if wow is not None:
        if wow <= -3.0:
            sense += f". Сильный draw ({wow:+.1f} млн) — бычий для цены на часы/дни"
        elif wow >= 3.0:
            sense += f". Сильный build ({wow:+.1f} млн) — медвежий на часы/дни"
        else:
            sense += f". WoW {wow:+.1f} млн — без шока"
    return level, sense


def interpret_cushing(mb: float, wow: float | None) -> str:
    if mb <= _CUSHING_LOW_MB:
        base = "Cushing низкий — WTI чувствительнее к любому draw"
    elif mb >= _CUSHING_HIGH_MB:
        base = "Cushing высокий — проще давить WTI вниз при build"
    else:
        base = "Cushing в середине"
    if wow is not None:
        return f"{base} (WoW {wow:+.1f} млн)"
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
            f"SPR: <b>{spr.latest.mbbl:.1f}</b> млн барр. на {spr.latest.date_label} "
            f"— {lvl}."
        )
        bits.append(sense)
        verdict_bits.append(lvl)
        if spr.wow_mb is not None and abs(spr.wow_mb) >= 2:
            surprise.append("резкий ход SPR (релиз/закупка)")
        watch.append("DOE: анонсы release/refill SPR")

    if commercial:
        lvl, sense = interpret_commercial(commercial.latest.mbbl, commercial.wow_mb)
        bits.append(
            f"Коммерческая нефть (без SPR): <b>{commercial.latest.mbbl:.1f}</b> млн "
            f"на {commercial.latest.date_label} — {lvl}."
        )
        bits.append(sense)
        verdict_bits.append(lvl)
        if commercial.wow_mb is not None and abs(commercial.wow_mb) >= 3:
            surprise.append("сюрприз EIA draw/build vs ожидания")
        watch.append("EIA Weekly (обычно ср ~17:30 МСК)")

    if cushing:
        bits.append(
            f"Cushing: <b>{cushing.latest.mbbl:.1f}</b> млн — "
            f"{interpret_cushing(cushing.latest.mbbl, cushing.wow_mb)}."
        )

    if not bits:
        return UsOilInventoryStatus(
            spr=None,
            commercial=None,
            cushing=None,
            summary_ru="Не удалось загрузить цифры EIA. Смотри weekly petroleum status report.",
            verdict_ru="нет данных",
            watch_ru="Открыть eia.gov/petroleum/supply/weekly",
            surprise_ru="Без цифр — только заголовки по запасам",
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
        price_hint = "фон запасов скорее поддерживает цену (тугой склад / тонкий SPR)"
    elif bear > bull:
        price_hint = "фон запасов скорее давит цену (build / дозаправка SPR)"
    else:
        price_hint = "фон запасов смешанный — решают геоновости и сюрприз vs consensus"

    conf = 7 if spr and commercial else 5 if spr or commercial else 3
    return UsOilInventoryStatus(
        spr=spr,
        commercial=commercial,
        cushing=cushing,
        summary_ru=" ".join(bits),
        verdict_ru=price_hint,
        watch_ru="; ".join(watch) if watch else "EIA weekly",
        surprise_ru="; ".join(surprise) if surprise else "обычный EIA без шока по складу",
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
        "📦 <b>Запасы США · EIA / SPR</b>",
        f"Уверенность цифр {st.confidence}/10",
        "",
        st.summary_ru,
        "",
        f"<b>Итог для нефти:</b> {st.verdict_ru}",
        f"<b>Что ждать:</b> {_esc(st.watch_ru)}",
        f"<b>Риск сюрприза:</b> {_esc(st.surprise_ru)}",
        "",
        "<b>Ссылки</b>",
        f"• <a href=\"{_SPR_PAGE}\">SPR (WCSSTUS1)</a>",
        f"• <a href=\"{_COMM_PAGE}\">Commercial excl SPR</a>",
        f"• <a href=\"{_CUSHING_PAGE}\">Cushing</a>",
        f"• <a href=\"{_EIA_WPSR}\">Weekly Petroleum Status</a>",
        "",
        "<i>Цифры weekly (лаг несколько дней). Для сделки важнее сюрприз vs ожидания "
        "в среду, плюс гео (Ормуз/Трамп) — склад сам по себе редко даёт ±5%.</i>",
    ]
    if st.spr and st.spr.release_ru:
        lines.insert(2, f"<i>{_esc(st.spr.release_ru)}</i>")
    return "\n".join(lines)


def format_inventory_short(st: UsOilInventoryStatus) -> str:
    """Короткий блок для вставки в Ормуз / дайджест."""
    parts: list[str] = []
    if st.spr:
        wow = f" ({st.spr.wow_mb:+.1f})" if st.spr.wow_mb is not None else ""
        parts.append(f"SPR {st.spr.latest.mbbl:.0f} млн{wow}")
    if st.commercial:
        wow = f" ({st.commercial.wow_mb:+.1f})" if st.commercial.wow_mb is not None else ""
        parts.append(f"comm {st.commercial.latest.mbbl:.0f} млн{wow}")
    if not parts:
        return ""
    return "📦 " + " · ".join(parts) + f" — {st.verdict_ru}"
