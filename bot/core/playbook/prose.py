"""Короткий разбор Telegram: факты по монете, без блока INTEL."""
from __future__ import annotations

import re
from html import escape

from ..snapshot import MarketSnapshot
from ...ta_analysis import TAAnalysisResult, fmt_price


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _flow_hint_ru(ta: TAAnalysisResult | None) -> str:
    if ta is None:
        return ""
    blob = " ".join(str(x) for x in (getattr(ta, "market_participation_lines", None) or [])).lower()
    if "закрытии позиц" in blob or "short covering" in blob:
        return "Открытый интерес снижается — движение скорее на закрытии позиций, чем на новых деньгах."
    if "cvd buy" in blob and ("7" in blob or "8" in blob or "9" in blob):
        return "В ленте доминируют покупки — у сопротивления это часто вынос стопов, не готовый лонг."
    if "cvd sell" in blob or "агрессивные продаж" in blob:
        return "В ленте сильнее продажи — отскоки вверх могут сдаваться быстрее."
    if "без явного" in blob or "без совместного" in blob:
        return "Цена и OI не тянут вместе — импульс слабый, лучше ждать уровень."
    return ""


def compose_playbook_prose_html(
    snap: MarketSnapshot,
    *,
    situation: str,
    expect: str,
    ta: TAAnalysisResult | None = None,
) -> str:
    """2–4 предложения: что на графике и что ждём (без OI/CVD/Liq таблицы)."""
    if ta is not None:
        try:
            from ..chart_read import get_or_build_chart_read

            read = get_or_build_chart_read(ta, symbol=snap.symbol or getattr(ta, "symbol", "") or "")
            prose = read.telegram_prose_html()
            if prose and len(prose) > 24:
                brk = float(getattr(ta, "breakout_level", 0) or 0) or snap.break_up
                brdn = float(getattr(ta, "breakdown_level", 0) or 0) or snap.break_down
                if brk > 0 and brdn > 0 and fmt_price(brdn) not in prose and fmt_price(brk) not in prose:
                    prose += (
                        f" Коридор <b>{fmt_price(brdn)}</b>–<b>{fmt_price(brk)}</b>."
                    )
                if len(prose) > 520:
                    prose = prose[:517] + "…"
                return prose
        except Exception:
            pass

    chunks: list[str] = []

    recent = ""
    if ta is not None:
        recent = _strip_html(str(getattr(ta, "recent_price_action_ru", "") or ""))
    if recent:
        chunks.append(recent.rstrip(".") + ".")
    elif situation:
        chunks.append(situation.rstrip(".") + ".")

    brk = float(getattr(ta, "breakout_level", 0) or 0) if ta else snap.break_up
    brdn = float(getattr(ta, "breakdown_level", 0) or 0) if ta else snap.break_down
    if brk > 0 and brdn > 0 and brk > brdn:
        chunks.append(
            f"Ключевой коридор <b>{fmt_price(brdn)}</b>–<b>{fmt_price(brk)}</b> — без market в середине."
        )
    elif brk > 0:
        chunks.append(f"Сверху смотрим реакцию у <b>{fmt_price(brk)}</b>.")
    elif brdn > 0:
        chunks.append(f"Снизу смотрим пробой и retest у <b>{fmt_price(brdn)}</b>.")

    if expect:
        ex = expect.rstrip(".")
        if ex and not any(ex[:40] in c for c in chunks):
            chunks.append(ex + ".")

    flow = _flow_hint_ru(ta)
    if flow and flow not in " ".join(chunks):
        chunks.append(flow)

    seek = (snap.reading_seek_label or "").strip()
    if seek and len(chunks) < 3:
        chunks.append(seek.rstrip(".") + ".")

    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip() if ta else ""
    if stack and len(chunks) < 4:
        chunks.append(f"Контекст: {stack[:90]}.")

    # не раздуваем
    text = " ".join(chunks[:4])
    if len(text) > 520:
        text = text[:517].rsplit(" ", 1)[0] + "…"
    return escape(text)
