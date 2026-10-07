"""Русские подписи в алертах — без ENTRY/WATCH/setup A/B и прочего жаргона."""
from __future__ import annotations

import re

_TIER_HTML = {
    "entry": "🎯 <b>Можно входить</b>",
    "watch": "👀 <b>Наблюдение</b>",
    "skip": "🚫 <b>Не шлём</b>",
}


def quality_tier_html(tier: str | None) -> str:
    return _TIER_HTML.get((tier or "").lower(), "")


def quality_tier_prefix(tier: str | None) -> str:
    """Префикс шапки сигнала с точкой."""
    label = quality_tier_html(tier)
    return f"{label} · " if label else ""


def side_ru(side: str | None, *, cap: bool = False) -> str:
    s = (side or "").lower()
    if s == "long":
        return "Лонг" if cap else "лонг"
    if s == "short":
        return "Шорт" if cap else "шорт"
    return side or ""


def verdict_ru(verdict: str | None) -> str:
    v = (verdict or "").upper()
    if v == "LONG":
        return "Лонг"
    if v == "SHORT":
        return "Шорт"
    if v == "WAIT":
        return "Ждать"
    return verdict or ""


def cvd_taker_line(ratio: float | None) -> str:
    if ratio is None:
        return ""
    buy_pct = ratio * 100.0
    sell_pct = 100.0 - buy_pct
    return f"Поток сделок: покупки {buy_pct:.0f}% / продажи {sell_pct:.0f}%"


def flow_matrix_label_ru(label: str) -> str:
    if not label:
        return ""
    low = label.lower()
    if "aligned" in low:
        return "OI и цена в одну сторону"
    if "diverg" in low or "conflict" in low:
        return "OI и цена расходятся"
    return label


_JARGON_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\bENTRY\b", "вход"),
    (r"\bWATCH\b", "наблюдение"),
    (r"\bTRIGGER\b", "триггер"),
    (r"\bsetup\s+([ABC])\b", r"план \1"),
    (r"\bsetup\b", "план"),
    (r"\bHot\b", "разбор"),
    (r"\bHTF\b", "старшие ТФ"),
    (r"\bLTF\b", "младший ТФ"),
    (r"\bLIQ-CASCADE\b", "каскад ликвидаций"),
    (r"\bmarket-?entry\b", "вход по рынку"),
    (r"\bmarket\b", "по рынку"),
    (r"\bconfluence\b", "схождение уровней"),
    (r"\baligned\b", "в одну сторону"),
    (r"\bLONG\b", "Лонг"),
    (r"\bSHORT\b", "Шорт"),
    (r"\bSL\b", "стоп"),
    (r"\bTP\b", "цель"),
    (r"\bOB\b", "блок ордеров"),
    (r"\bFib\b", "Фибо"),
    (r"\bCVD\b", "поток сделок"),
    (r"\bsweep\b", "смыв ликвидности"),
    (r"\breversal\b", "разворот"),
    (r"\bcont\b", "продолжение"),
    (r"\bcorr\b", "коррекция"),
)


def polish_user_copy(text: str) -> str:
    """Последняя полировка HTML/текста для Telegram."""
    if not text:
        return text
    out = text
    for pattern, repl in _JARGON_REPLACEMENTS:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    out = out.replace("режим A (", "лимит в зоне (")
    out = out.replace("режим B (", "после свечи (")
    out = out.replace("режим C (", "наблюдение (")
    out = re.sub(r"· режим <b>[ABC]</b>", "", out)
    out = re.sub(r"режим <b>[ABC]</b> \(", "способ входа (", out)
    return out
