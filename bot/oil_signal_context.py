"""Контекст сигнала UKOUSD: Ормуз / запасы / поток — понятный разбор."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class HormuzContext:
    """Статус переговоров по проливу — не путать слух со сделкой."""

    status: str  # none | talks | progress | not_final | signed_claim
    fee_ru: str  # кто сколько хочет
    control_ru: str
    oil_bias: str  # bearish_soft | bullish_soft | mixed | neutral
    line_ru: str  # одна строка в карточку
    for_entry: bool  # можно ли торговать «как deal»


@dataclass(frozen=True)
class InventoryContext:
    tone: str  # bullish | bearish | mixed | none
    line_ru: str
    from_news: bool


@dataclass(frozen=True)
class SignalDrivers:
    hormuz: HormuzContext
    inventory: InventoryContext
    news_mode: str
    flow_ru: str
    why_ru: str
    caution_ru: str
    lines_ru: tuple[str, ...]


def _blob(items: Sequence[Any] | None) -> str:
    parts: list[str] = []
    for it in list(items or [])[:24]:
        parts.append(str(getattr(it, "title", "") or ""))
        parts.append(str(getattr(it, "theme", "") or ""))
        parts.append(str(getattr(it, "summary", "") or ""))
    return " ".join(parts).lower()


def analyze_hormuz_context(
    news_items: Sequence[Any] | None,
    *,
    now: float | None = None,
) -> HormuzContext:
    """Разбор Ормуз: 3% Оман / 5–7% Иран / 0% США / сделка не финал."""
    text = _blob(news_items)
    if not any(k in text for k in ("hormuz", "ормуз", "strait", "пролив")):
        # Iran alone without strait — weak
        if not any(k in text for k in ("iran", "иран")):
            return HormuzContext(
                status="none",
                fee_ru="",
                control_ru="",
                oil_bias="neutral",
                line_ru="",
                for_entry=False,
            )

    has_deal = any(
        k in text
        for k in (
            "deal",
            "сделк",
            "accord",
            "mou",
            "ceasefire",
            "перемир",
            "reopen",
            "открыт",
            "progress",
            "прогресс",
        )
    )
    not_final = any(
        k in text
        for k in (
            "not final",
            "no finality",
            "not yet",
            "still underway",
            "unresolved",
            "negotiat",
            "переговор",
            "не подпис",
            "не заключ",
            "без финал",
            "progress but",
            "no agreement",
            "no deal yet",
        )
    )
    signed_claim = any(
        k in text
        for k in (
            "signed",
            "reached a deal",
            "deal done",
            "agreement reached",
            "подписан",
            "заключил",
            "сделку приняли",
        )
    ) and not not_final

    # Fee positions
    iran_high = bool(
        re.search(r"5\s*[–\-to]+\s*7\s*%", text)
        or re.search(r"\b7\s*%", text)
        or ("5%" in text and "7%" in text)
        or "mandatory toll" in text
        or "обязательн" in text
    )
    oman_3 = bool(
        re.search(r"\b3\s*%", text)
        or "around 3%" in text
        or "approximately 3%" in text
        or "около 3" in text
        or ("oman" in text and "3%" in text)
        or ("оман" in text and "3%" in text)
    )
    us_zero = any(
        k in text
        for k in (
            "no fee",
            "zero fee",
            "no toll",
            "won't let them charge",
            "freedom of movement",
            "0%",
            "без сбор",
            "без пошлин",
            "не дам брать",
            "не позволю",
        )
    ) or ("bessent" in text and "freedom" in text)

    # Условие: без судов США / Израиля — сделка не «полный reopen»
    us_ships_ban = any(
        k in text
        for k in (
            "bans us",
            "ban us",
            "bans israel",
            "no us vessel",
            "no us ships",
            "no american",
            "us israel ships",
            "no us involvement",
            "without us involvement",
            "без судов сша",
            "без американск",
            "суда сша не",
        )
    ) or (
        ("us ship" in text or "us vessel" in text or "american ship" in text)
        and any(k in text for k in ("ban", "bans", "not", "no ", "without", "except"))
    )

    fee_bits: list[str] = []
    if iran_high:
        fee_bits.append("Иран хочет 5–7%")
    if oman_3:
        fee_bits.append("Оман предлагает ~3% (service fee)")
    if us_zero:
        fee_bits.append("США: 0% / свобода прохода")
    if us_ships_ban:
        fee_bits.append("условие: без судов США/Израиля")
    if not fee_bits and has_deal:
        fee_bits.append("сборы обсуждают, цифры в ленте неясны")

    control = ""
    if "control" in text or "контрол" in text or "inbound" in text:
        control = "спор о контроле входа в залив"
    if "oman" in text or "оман" in text:
        control = (control + " · " if control else "") + "переговоры Иран–Оман"

    if signed_claim and not not_final and not us_ships_ban:
        status = "signed_claim"
        oil_bias = "bearish_soft"
        for_entry = True
        line = "Ормуз: в ленте пишут «сделка есть» — сверяй, не слух ли"
    elif us_ships_ban:
        status = "not_final"
        oil_bias = "mixed"
        for_entry = False
        line = (
            "Ормуз: Иран готов подписать ЛИШЬ с условием "
            "(без судов США/Израиля) — НЕ чистый reopen, premium может остаться"
        )
    elif has_deal or not_final or oman_3 or iran_high:
        status = "not_final" if (not_final or oman_3 or iran_high or us_zero) else "progress"
        # Разные позиции по fee = нельзя SHORT как «premium снят навсегда»
        if (oman_3 or iran_high) and us_zero:
            oil_bias = "mixed"
            for_entry = False
            line = (
                "Ормуз: сделка НЕ финал · "
                + " · ".join(fee_bits[:3])
                + " — без chase SHORT"
            )
        elif us_zero and has_deal:
            oil_bias = "mixed"
            for_entry = False
            line = "Ормуз: прогресс, но США против toll — premium может остаться"
        elif oman_3 and not iran_high:
            oil_bias = "bearish_soft"
            for_entry = False
            line = "Ормуз: слух ~3% (Оман), не подписано — фон↓, не сигнал входа"
        else:
            oil_bias = "bearish_soft" if has_deal else "mixed"
            for_entry = False
            line = "Ормуз: переговоры / прогресс, финала нет — только фон"
    else:
        status = "talks"
        oil_bias = "neutral"
        for_entry = False
        line = "Ормуз/Иран в ленте без ясной сделки"

    if control and line:
        line = f"{line} · {control}"

    # Свежесть: если все заголовки старше 2ч — не for_entry
    now_ts = now if now is not None else time.time()
    ages = []
    for it in list(news_items or []):
        ts = float(getattr(it, "published_ts", 0) or 0)
        if ts > 0:
            ages.append((now_ts - ts) / 3600.0)
    if ages and min(ages) > 2.0:
        for_entry = False
        if line and "фон" not in line:
            line = line + " · новости не HOT"

    return HormuzContext(
        status=status,
        fee_ru=" · ".join(fee_bits),
        control_ru=control,
        oil_bias=oil_bias,
        line_ru=line[:220],
        for_entry=for_entry,
    )


def analyze_inventory_from_news(
    news_items: Sequence[Any] | None,
) -> InventoryContext:
    """Запасы из заголовков EIA/API (без обязательного HTTP)."""
    text = _blob(news_items)
    if not any(
        k in text
        for k in ("eia", "inventory", "inventories", "запас", "api crude", "stocks")
    ):
        return InventoryContext(tone="none", line_ru="", from_news=False)

    build = any(
        k in text
        for k in (
            "build",
            "built",
            "rose",
            "increase",
            "up ",
            "рост запас",
            "вырос",
            "увеличил",
            "+2",
            "2.5 million",
            "2.4 million",
            "surprise build",
        )
    )
    draw = any(
        k in text
        for k in (
            "draw",
            "drew",
            "fell",
            "decline",
            "drop",
            "сниж",
            "сокращ",
            "упал",
            "unexpected draw",
        )
    )
    # Crude vs products nuance
    products_tight = any(
        k in text for k in ("gasoline", "distillate", "бензин", "дизел")
    ) and draw

    if build and not draw:
        return InventoryContext(
            tone="bearish",
            line_ru="Запасы: crude build (EIA/лента) → краткосрочно давление↓",
            from_news=True,
        )
    if draw and not build:
        return InventoryContext(
            tone="bullish",
            line_ru="Запасы: crude draw (EIA/лента) → поддержка↑",
            from_news=True,
        )
    if build and products_tight:
        return InventoryContext(
            tone="mixed",
            line_ru="Запасы: crude↑, продукты↓ — mixed, не один SHORT/LONG",
            from_news=True,
        )
    if build and draw:
        return InventoryContext(
            tone="mixed",
            line_ru="Запасы: в ленте и build, и draw — сверяй EIA",
            from_news=True,
        )
    return InventoryContext(
        tone="mixed",
        line_ru="Запасы США в ленте — без ясного сюрприза",
        from_news=True,
    )


def build_signal_drivers(
    *,
    news_items: Sequence[Any] | None = None,
    news_mode: str = "none",
    flow: Any | None = None,
    inventory_status: Any | None = None,
    side: str = "WAIT",
    bars: Sequence[Any] | None = None,
) -> SignalDrivers:
    """Сводка драйверов для понятного сигнала."""
    hormuz = analyze_hormuz_context(news_items)
    inv = analyze_inventory_from_news(news_items)
    if inventory_status is not None and not inv.line_ru:
        try:
            from .oil_inventory import format_inventory_short

            raw = format_inventory_short(inventory_status)
            plain = re.sub(r"<[^>]+>", "", raw)
            if plain:
                inv = InventoryContext(
                    tone="mixed",
                    line_ru=plain[:160],
                    from_news=False,
                )
        except Exception:
            pass

    flow_ru = ""
    fl = (getattr(flow, "bias", "") or "").lower() if flow is not None else ""
    if fl == "buy":
        flow_ru = "поток BUY"
    elif fl == "sell":
        flow_ru = "поток SELL"
    elif fl == "neutral":
        flow_ru = "поток нейтрален"

    macd_ru = ""
    try:
        from .oil_macd import compute_oil_macd

        macd = compute_oil_macd(bars)
        if macd is not None:
            macd_ru = macd.line_ru
    except Exception:
        pass

    lines: list[str] = []
    if hormuz.line_ru:
        lines.append(hormuz.line_ru)
    if inv.line_ru:
        lines.append(inv.line_ru)
    if flow_ru:
        lines.append(flow_ru)
    if macd_ru:
        lines.append(macd_ru)
    if news_mode and news_mode != "none":
        lines.append(f"новости: {news_mode.upper()}")

    why_parts: list[str] = []
    if side == "SHORT":
        if hormuz.oil_bias == "bearish_soft" and hormuz.for_entry:
            why_parts.append("свежий deal-tape Ормуз")
        elif inv.tone == "bearish":
            why_parts.append("build запасов")
        if fl == "sell":
            why_parts.append("поток SELL")
        if "MACD↓" in macd_ru:
            why_parts.append("MACD↓")
    elif side == "LONG":
        if hormuz.oil_bias == "bullish_soft":
            why_parts.append("риск срыва / блок пролива")
        elif inv.tone == "bullish":
            why_parts.append("draw запасов")
        if fl == "buy":
            why_parts.append("поток BUY")
        if "MACD↑" in macd_ru:
            why_parts.append("MACD↑")
    why_ru = " · ".join(why_parts) if why_parts else "схождение факторов слабое"

    caution = ""
    if hormuz.status in {"not_final", "progress", "talks"} and side == "SHORT":
        caution = "Ормуз не финал — не догонять SHORT по слуху"
    elif hormuz.oil_bias == "mixed":
        caution = "позиции по сборам разные (3% / 5–7% / 0%) — WAIT предпочтительнее"
    elif inv.tone == "mixed" and side in {"LONG", "SHORT"}:
        caution = "запасы mixed — размер меньше"
    elif fl == "buy" and side == "SHORT":
        caution = "поток против SHORT"
    elif fl == "sell" and side == "LONG":
        caution = "поток против LONG"
    elif "MACD↑" in macd_ru and side == "SHORT":
        caution = "MACD бычий — SHORT против индикатора"
    elif "MACD↓" in macd_ru and side == "LONG":
        caution = "MACD медвежий — LONG против индикатора"

    return SignalDrivers(
        hormuz=hormuz,
        inventory=inv,
        news_mode=news_mode or "none",
        flow_ru=flow_ru,
        why_ru=why_ru,
        caution_ru=caution,
        lines_ru=tuple(lines[:5]),
    )


def format_clear_signal_card(
    *,
    side: str,
    quality: int,
    price: float,
    drivers: SignalDrivers,
    entry_lo: float | None = None,
    entry_hi: float | None = None,
    stop: float | None = None,
    tp1: float | None = None,
    tp2: float | None = None,
    trigger_ru: str = "",
    invalidation: float | None = None,
    horizon_ru: str = "",
    mode_tag: str = "",
    extra_ru: str = "",
) -> str:
    """Единая понятная карточка сигнала для чата."""
    if side == "WAIT":
        mark, title = "✋", "WAIT"
    elif side == "LONG":
        mark, title = "🟢", "LONG"
    else:
        mark, title = "🔴", "SHORT"

    lines = [
        f"{mark} <b>СИГНАЛ {title}</b> · {int(quality)}/10"
        + (f" · ${float(price):.2f}" if price and float(price) > 0.5 else "")
        + mode_tag,
    ]
    if horizon_ru:
        lines.append(f"<i>{_esc(horizon_ru)}</i>")

    if drivers.why_ru and side in {"LONG", "SHORT"}:
        lines.append(f"<b>Почему:</b> {_esc(drivers.why_ru)}")
    elif side == "WAIT":
        lines.append(
            f"<b>Почему WAIT:</b> {_esc(drivers.caution_ru or 'нет чистого края')}"
        )

    if drivers.lines_ru:
        lines.append("<b>Фон:</b>")
        for d in drivers.lines_ru[:4]:
            lines.append(f"· {_esc(d)}")

    if side in {"LONG", "SHORT"}:
        plan: list[str] = []
        if entry_lo is not None and entry_hi is not None:
            if abs(entry_lo - entry_hi) < 1e-6:
                plan.append(f"вход {entry_lo:.2f}")
            else:
                plan.append(f"вход {entry_lo:.2f}–{entry_hi:.2f}")
        if stop is not None:
            plan.append(f"SL {stop:.2f}")
        tps = []
        if tp1 is not None:
            tps.append(f"{tp1:.2f}")
        if tp2 is not None:
            tps.append(f"{tp2:.2f}")
        if tps:
            plan.append(f"TP {' / '.join(tps)}")
        if plan:
            lines.append(f"<b>План:</b> {' · '.join(plan)}")
        if trigger_ru:
            lines.append(f"<b>Триггер:</b> {_esc(trigger_ru)[:140]}")
        if invalidation is not None:
            if side == "LONG":
                lines.append(f"<b>Отмена:</b> close ниже ${float(invalidation):.2f}")
            else:
                lines.append(f"<b>Отмена:</b> close выше ${float(invalidation):.2f}")
    else:
        if trigger_ru:
            lines.append(_esc(trigger_ru)[:160])

    if drivers.caution_ru and side in {"LONG", "SHORT"}:
        lines.append(f"⚠ {_esc(drivers.caution_ru)}")
    if extra_ru:
        g = " ".join(extra_ru.split())
        if len(g) > 160:
            g = g[:157] + "…"
        lines.append(_esc(g))
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
