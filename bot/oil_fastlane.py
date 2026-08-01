"""Fast-lane нефтяных новостей: WSJ / Reuters / Bloomberg / Blas / FT / NYT / official."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Sequence

from .bybit_klines import KlineBar

logger = logging.getLogger(__name__)

# Узкий набор — только топ wire / эксклюзивы / official
FAST_LANE_QUERIES_EN: tuple[str, ...] = (
    "site:wsj.com Iran OR Hormuz OR oil OR Trump when:12h",
    "site:reuters.com Iran OR Hormuz OR oil OR Trump when:12h",
    "site:bloomberg.com Iran OR Hormuz OR oil OR energy OR Trump when:12h",
    "Javier Blas oil OR Hormuz OR Iran OR Brent when:12h",
    "site:ft.com oil OR Iran OR Hormuz OR Brent when:1d",
    "site:nytimes.com Iran OR Hormuz OR oil OR Trump when:1d",
    "White House Iran oil OR Pentagon Iran OR DoD Iran Hormuz when:12h",
    "EIA crude oil inventory OR STEO when:1d",
    "Trump orders attack Iran OR strike Iran oil when:12h",
    "Strait of Hormuz tanker OR blockade OR reopen when:12h",
)

FAST_LANE_QUERIES_RU: tuple[str, ...] = (
    "WSJ Трамп Иран удар OR атака нефть when:12h",
    "Reuters Ормуз нефть Иран when:12h",
    "Bloomberg Ормуз OR Иран нефть when:12h",
)

# (needle in source/title/url, display, tier 1=highest)
_TIER1_SOURCES: tuple[tuple[str, str, int], ...] = (
    ("wall street journal", "WSJ", 1),
    ("wsj.com", "WSJ", 1),
    ("wsj", "WSJ", 1),
    ("reuters", "Reuters", 1),
    ("bloomberg", "Bloomberg", 1),
    ("javier blas", "Javier Blas", 1),
    ("financial times", "FT", 2),
    ("ft.com", "FT", 2),
    ("new york times", "NYT", 2),
    ("nytimes", "NYT", 2),
    ("white house", "White House", 1),
    ("pentagon", "DoD / Pentagon", 1),
    ("department of defense", "DoD", 1),
    ("eia.gov", "EIA", 1),
    ("u.s. energy information", "EIA", 1),
    ("energy information administration", "EIA", 1),
)

# Баллы только по ЗАГОЛОВКУ (не по source — иначе любой WSJ даёт flash)
_FLASH_TERMS: dict[str, int] = {
    "attack": 5,
    "strike": 5,
    "удар": 5,
    "атак": 5,
    "orders attack": 6,
    "ordered a": 4,
    "hormuz": 5,
    "ормуз": 5,
    "blockade": 4,
    "close strait": 5,
    "reopen": 3,
    "ceasefire": 3,
    "sanction": 3,
    "trump": 3,
    "трамп": 3,
    "iran": 3,
    "иран": 3,
    "oil": 3,
    "crude": 3,
    "brent": 3,
    "нефт": 3,
    "opec": 2,
    "eia": 2,
    "tanker": 3,
    "танкер": 3,
    "javier blas": 4,
    "pentagon": 3,
    "white house": 3,
}

# Без этого в title — не шлём, даже если outlet = WSJ
# Короткие токены (spr, eia, wti) — только целыми словами (иначе spr⊂Spreading).
_TOPIC_OIL: tuple[str, ...] = (
    "oil", "crude", "brent", "petroleum", "gasoline", "diesel",
    "нефт", "баррель", "barrel", "opec", "опек",
    "energy", "энерг", "commodity", "commodit", "fuel oil",
)
_TOPIC_OIL_WORDS: tuple[str, ...] = (
    "wti", "eia", "spr", "fuel",
)
_TOPIC_GEO: tuple[str, ...] = (
    "iran", "иран", "tehran", "тегеран", "hormuz", "ормуз", "strait",
    "houthi", "хусит", "persian gulf", "middle east", "ближн",
    "israel", "израил", "hezbollah", "red sea",
)


def _has_whole_word(text: str, word: str) -> bool:
    import re

    return re.search(rf"(?<![a-zа-я0-9]){re.escape(word)}(?![a-zа-я0-9])", text) is not None


@dataclass(frozen=True)
class FastLaneMeta:
    outlet: str
    tier: int  # 1 or 2
    flash_score: int
    is_flash: bool


def fastlane_title_on_topic(title: str) -> bool:
    """Заголовок про нефть / Ормуз / Иран / энерго-гео — иначе шум (мода, спорт…)."""
    low = (title or "").lower()
    if not low.strip():
        return False
    if any(k in low for k in _TOPIC_OIL):
        return True
    if any(_has_whole_word(low, w) for w in _TOPIC_OIL_WORDS):
        return True
    if any(k in low for k in _TOPIC_GEO):
        return True
    # Санкции/атака США — только вместе с нефтью или Ираном (не любой Trump-заголовок)
    if any(k in low for k in ("sanction", "санкц", "attack", "strike", "удар", "атак")) and any(
        k in low for k in ("oil", "crude", "нефт", "iran", "иран", "hormuz", "ормуз", "energy")
    ):
        return True
    return False


def detect_fastlane_outlet(title: str, source: str = "", url: str = "") -> FastLaneMeta | None:
    blob = f"{title} {source} {url}".lower()
    title_l = (title or "").lower()
    best: tuple[str, int] | None = None
    for needle, display, tier in _TIER1_SOURCES:
        if needle in blob:
            if best is None or tier < best[1] or (tier == best[1] and len(display) > len(best[0])):
                # prefer lower tier number (more important)
                if best is None or tier < best[1]:
                    best = (display, tier)
                elif tier == best[1] and display != best[0]:
                    # keep first match of same tier unless WSJ/Blas
                    if display in {"WSJ", "Javier Blas", "Bloomberg", "Reuters"}:
                        best = (display, tier)
    if best is None:
        return None
    score = 0
    for term, w in _FLASH_TERMS.items():
        if term in title_l:
            score += w
    # Tier1 outlet always gets base boost
    score += 4 if best[1] == 1 else 2
    is_flash = score >= 8
    return FastLaneMeta(outlet=best[0], tier=best[1], flash_score=score, is_flash=is_flash)


def is_fastlane_item(item: Any, *, min_flash_score: int = 7) -> bool:
    title = getattr(item, "title", "") or ""
    if not fastlane_title_on_topic(title):
        return False
    meta = detect_fastlane_outlet(
        title,
        getattr(item, "source", "") or "",
        getattr(item, "url", "") or "",
    )
    if meta is None:
        return False
    # Tier-1 geo/oil always; tier-2 needs higher score
    if meta.tier == 1 and meta.flash_score >= min_flash_score:
        return True
    if meta.tier == 2 and meta.flash_score >= min_flash_score + 2:
        return True
    return meta.is_flash


def ai_says_off_topic(ai_text: str) -> bool:
    """Gemini пометил новость как не про нефть → не слать в чат."""
    raw = (ai_text or "").strip()
    if not raw:
        return False
    head = raw[:220].upper().replace(" ", "")
    if "OIL_RELEVANT:NO" in head or "OIL_RELEVANT：NO" in head:
        return True
    if "OIL_RELEVANT:YES" in head or "OIL_RELEVANT：YES" in head:
        return False
    low = raw.lower()
    strong_no = (
        "никак не относится к нефт",
        "абсолютно никак не относится",
        "не относится к нефт",
        "влияние на котировки ukousd нулев",
        "влияние на нефть: нет",
        "no_trade",
        "информационным шумом",
        "полностью игнорировать эту новость",
    )
    return any(p in low for p in strong_no)


def _price_move_note(bars: Sequence[KlineBar] | None, *, interval_minutes: int = 5) -> str:
    """Если цена уже скакнула — рынок мог опередить заголовок."""
    if not bars or len(bars) < 6:
        return ""
    b30 = max(1, int(round(30 / max(5, interval_minutes))))
    b60 = max(b30 + 1, int(round(60 / max(5, interval_minutes))))
    px = float(bars[-1].close)
    p30 = float(bars[-1 - min(b30, len(bars) - 1)].close)
    p60 = float(bars[-1 - min(b60, len(bars) - 1)].close)
    if p30 <= 0 or p60 <= 0:
        return ""
    m30 = (px - p30) / p30 * 100.0
    m60 = (px - p60) / p60 * 100.0
    if abs(m30) < 0.35 and abs(m60) < 0.5:
        return ""
    if abs(m30) >= 0.8 or abs(m60) >= 1.2:
        return (
            f"⚠️ Движение опережает новость: прокси-цена уже "
            f"{m30:+.2f}% / 30м и {m60:+.2f}% / 1ч (≈${px:.2f}). "
            "Часто так при слухах до полного текста WSJ/Reuters."
        )
    return (
        f"Цена уже сдвинулась: {m30:+.2f}% / 30м, {m60:+.2f}% / 1ч (≈${px:.2f})."
    )


def _bounce_hint(impact: str, move_note: str) -> str:
    """После сильного импульса часто бывает отскок."""
    strong = "опережает" in move_note or "уже" in move_note
    if impact == "bullish":
        if strong:
            return (
                "Сценарий: импульс вверх на geo → часто короткий <b>отскок вниз</b> "
                "для фиксации. Лонг только от поддержки / не chase хай."
            )
        return (
            "Сценарий: давление вверх (страх поставок). Шорт против премии — осторожно; "
            "лонг от уровня, не в середине импульса."
        )
    if impact == "bearish":
        if strong:
            return (
                "Сценарий: снятие premium / deal-tape → часто <b>отскок вверх</b> "
                "после слива. Шорт от сопротивления, не ловить нож."
            )
        return (
            "Сценарий: давление вниз (танкеры/сделка). Лонг только от сильной поддержки."
        )
    return "Сценарий смешанный — ждать уровень и подтверждение следующего заголовка."


async def enrich_fastlane_with_gemini(
    title: str,
    *,
    source: str,
    outlet: str,
    impact: str,
    move_note: str,
    api_key: str | None,
    model: str = "gemini-3.6-flash",
) -> str:
    """Короткий ИИ-разбор для UKOUSD; пустая строка при ошибке.

    Первая строка ответа: OIL_RELEVANT: YES|NO — для отсева моды/спорта и т.п.
    """
    if not api_key:
        return ""
    try:
        from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram

        ctx = (
            "Ты профессиональный трейдер Brent/UKOUSD. Пиши по-русски, просто и жёстко. "
            "Без markdown.\n"
            f"Источник: {outlet} ({source})\n"
            f"Заголовок: {title}\n"
            f"Тон заголовка (бот): {impact}\n"
            f"Цена: {move_note or 'без сильного сдвига до новости'}\n"
        )
        user = (
            "Первая строка СТРОГО одна из двух:\n"
            "OIL_RELEVANT: YES\n"
            "или\n"
            "OIL_RELEVANT: NO\n"
            "YES — только если влияет на нефть/Brent/Ормуз/санкции/запасы/ОПЕК/танкеры.\n"
            "NO — мода, спорт, культура, быт, чистая политика без энергии.\n\n"
            "Если YES, дальше 5 коротких пунктов:\n"
            "1) Суть\n2) Почему влияет на нефть\n3) Bias вверх/вниз/mixed\n"
            "4) Отскок после импульса?\n5) Что делать (не финсовет)\n"
            "Если NO — одной фразой почему это шум. Не выдумывай факты."
        )
        result = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=user,
        )
        text = sanitize_ai_reply_for_telegram(result.text or "").strip()
        if result.error or not text:
            return ""
        if len(text) > 1100:
            text = text[:1097] + "…"
        return text
    except Exception:
        logger.exception("Fast-lane Gemini failed")
        return ""


def format_fastlane_flash(
    item: Any,
    *,
    meta: FastLaneMeta,
    ai_ru: str = "",
    move_note: str = "",
    age_label: str = "",
) -> str:
    """Сообщение ‼️ КРИТИЧНО в Новостник."""
    title = (getattr(item, "title", "") or "").replace("<", "&lt;").replace(">", "&gt;")
    url = getattr(item, "url", "") or ""
    impact = getattr(item, "impact", "neutral") or "neutral"
    impact_ru = {
        "bullish": "🟢 давление на нефть ВВЕРХ",
        "bearish": "🔴 давление на нефть ВНИЗ",
        "neutral": "⚪ влияние смешанное / уточнять",
    }.get(impact, "⚪ контекст")
    bang = "‼️‼️" if meta.tier == 1 and meta.flash_score >= 12 else "‼️"
    lines = [
        f"{bang} <b>КРИТИЧНО · НЕФТЬ</b> · {meta.outlet}",
        f"<i>fast-lane · score {meta.flash_score} · tier {meta.tier}</i>",
        "",
    ]
    if url:
        lines.append(f"<a href=\"{url}\"><b>{title}</b></a>")
    else:
        lines.append(f"<b>{title}</b>")
    lines.append("")
    lines.append(impact_ru)
    lines.append(_bounce_hint(impact, move_note))
    if move_note:
        lines.append("")
        lines.append(f"<b>{_esc(move_note)}</b>")
    if ai_ru:
        lines.append("")
        lines.append("🤖 <b>Разбор ИИ</b>")
        lines.append(_esc(ai_ru))
    lines.append("")
    src = getattr(item, "source", "") or meta.outlet
    age = age_label or ""
    lines.append(f"<i>🇬🇧 {src}" + (f" · {age}" if age else "") + "</i>")
    if url:
        lines.append(f"🔗 <a href=\"{url}\">Открыть источник</a>")
    lines.append(
        "<i>Топ-wire (WSJ/Reuters/Bloomberg/Blas/FT/NYT/official). Не финсовет.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def filter_fastlane_items(
    items: Sequence[Any],
    *,
    min_flash_score: int = 7,
    max_age_hours: float = 6.0,
) -> list[Any]:
    now = time.time()
    out: list[Any] = []
    for it in items:
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts and (now - ts) > max_age_hours * 3600:
            continue
        if is_fastlane_item(it, min_flash_score=min_flash_score):
            out.append(it)
    out.sort(
        key=lambda x: (
            float(getattr(x, "published_ts", 0) or 0),
            (
                detect_fastlane_outlet(
                    getattr(x, "title", "") or "",
                    getattr(x, "source", "") or "",
                    getattr(x, "url", "") or "",
                )
                or FastLaneMeta(outlet="", tier=9, flash_score=0, is_flash=False)
            ).flash_score,
        ),
        reverse=True,
    )
    return out
