"""Честный ответ «почему UKOUSD растёт/падает» — из новостей + цены + потока, без фантазий."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .bybit_klines import KlineBar
from .oil_flow import OilFlowProxy, compute_oil_flow_proxy
from .ta_analysis import fmt_price


@dataclass(frozen=True)
class OilWhyReport:
    direction: str  # up | down | flat
    move_1h_pct: float
    move_4h_pct: float
    price: float
    confidence: int  # 1–10 насколько объяснение опирается на факты
    headline_ru: str
    drivers_ru: tuple[str, ...]
    against_ru: tuple[str, ...]
    unknown_ru: tuple[str, ...]
    how_to_use_ru: str
    sources_note_ru: str


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _move_from_bars(bars: Sequence[KlineBar], *, bars_back: int) -> float:
    if not bars or len(bars) < 2:
        return 0.0
    i = max(0, len(bars) - 1 - bars_back)
    return _pct(float(bars[-1].close), float(bars[i].close))


def _theme_counts(news_items: Sequence[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in news_items:
        theme = getattr(it, "theme", "") or ""
        if not theme:
            continue
        counts[theme] = counts.get(theme, 0) + 1
    return counts


def _top_headlines(news_items: Sequence[Any], *, want_impact: str, limit: int = 3) -> list[str]:
    scored: list[tuple[int, str]] = []
    for it in news_items:
        if want_impact and getattr(it, "impact", "") != want_impact:
            continue
        title = (getattr(it, "title", "") or "").strip()
        if not title:
            continue
        from .oil_monitor import news_critical_score

        scored.append((news_critical_score(title, source=getattr(it, "source", "")), title))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _, t in scored:
        key = t.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(t[:140])
        if len(out) >= limit:
            break
    return out


def build_oil_why_report(
    bars: Sequence[KlineBar],
    *,
    news_items: Sequence[Any] | None = None,
    news_bias: Any | None = None,
    forecast: Any | None = None,
    flow: OilFlowProxy | None = None,
    interval_minutes: int = 15,
) -> OilWhyReport | None:
    if not bars or len(bars) < 8:
        return None
    px = float(bars[-1].close)
    # Окна в барах
    b1h = max(1, int(round(60 / max(5, interval_minutes))))
    b4h = max(b1h + 1, int(round(240 / max(5, interval_minutes))))
    m1 = _move_from_bars(bars, bars_back=b1h)
    m4 = _move_from_bars(bars, bars_back=min(b4h, len(bars) - 1))

    # Направление: приоритет 1ч, иначе 4ч
    primary_move = m1 if abs(m1) >= 0.08 else m4
    if primary_move >= 0.12:
        direction = "up"
    elif primary_move <= -0.12:
        direction = "down"
    else:
        direction = "flat"

    items = list(news_items or [])
    if flow is None:
        flow = compute_oil_flow_proxy(bars, lookback=12 if interval_minutes <= 15 else 8)

    news = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    news_w = float(getattr(news_bias, "weighted_score", 0) or 0) if news_bias else 0.0
    catalyst = (getattr(news_bias, "top_catalyst", "") or "") if news_bias else ""
    fc_bias = (getattr(forecast, "bias", "") or "") if forecast else ""
    fc_scen = (getattr(forecast, "scenario", "") or "") if forecast else ""

    drivers: list[str] = []
    against: list[str] = []
    unknown: list[str] = [
        "Нет доступа к живому DOM ICE/NYMEX — только свечи + публичные заголовки.",
        "CFD UKOUSD может на минуты расходиться с Brent futures (basis/спред брокера).",
    ]

    # Цена
    if direction == "up":
        drivers.append(
            f"Цена UKOUSD≈${px:.2f}: за ~1ч {m1:+.2f}%, за ~4ч {m4:+.2f}%."
        )
    elif direction == "down":
        drivers.append(
            f"Цена UKOUSD≈${px:.2f}: за ~1ч {m1:+.2f}%, за ~4ч {m4:+.2f}%."
        )
    else:
        drivers.append(
            f"Цена UKOUSD≈${px:.2f}: движение слабое (~1ч {m1:+.2f}%, ~4ч {m4:+.2f}%) — "
            "скорее шум/база, не новый тренд."
        )

    # Новости
    themes = _theme_counts(items)
    if themes.get("iran_geo"):
        if news == "bullish" or (direction == "up" and news != "bearish"):
            drivers.append(
                "Геополитика Иран/Ормуз в свежих заголовках — рынок часто добавляет "
                "war-premium при эскалации / блоке танкеров."
            )
        elif news == "bearish" or direction == "down":
            drivers.append(
                "Иран/Ормуз в ленте, но тон скорее «сделка / больше танкерных потоков» — "
                "это обычно снимает premium и давит цену вниз."
            )
        else:
            drivers.append("Иран/Ормуз в ленте — главный источник волатильности, тон смешанный.")
    if themes.get("inventory"):
        drivers.append("Есть заголовки по запасам EIA/SPR — краткосрочный драйвер US crude.")
    if themes.get("opec"):
        drivers.append("Есть сюжеты OPEC/добыча — среднесрочный supply-фактор.")
    if themes.get("analyst"):
        drivers.append("В ленте прогнозы банков/аналитиков — влияют на bias, не на тик.")

    want = "bullish" if direction == "up" else "bearish" if direction == "down" else ""
    tops = _top_headlines(items, want_impact=want, limit=3) if want else []
    if not tops and items:
        tops = _top_headlines(items, want_impact="", limit=2)
    for t in tops:
        drivers.append(f"Заголовок: «{t}»")

    if catalyst and catalyst not in " ".join(drivers):
        drivers.append(f"Катализатор ленты: «{catalyst[:120]}»")

    if news_bias is not None:
        drivers.append(f"News-bias бота: {news} ({news_w:+.1f}/10).")

    # Поток
    if flow is not None:
        if flow.bias == "buy" and direction == "up":
            drivers.append(
                f"Поток-прокси BUY (share {flow.buy_share_pct:g}%, vol x{flow.volume_ratio:g}) "
                "— свечной delta согласуется с ростом."
            )
        elif flow.bias == "sell" and direction == "down":
            drivers.append(
                f"Поток-прокси SELL (share {flow.buy_share_pct:g}%) — согласуется с падением."
            )
        elif flow.bias == "buy" and direction == "down":
            against.append(
                "Поток-прокси BUY при падении цены — возможен short-cover / слабый даун-мув."
            )
        elif flow.bias == "sell" and direction == "up":
            against.append(
                "Поток-прокси SELL при росте — риск ложного отскока / thin bounce."
            )
        else:
            against.append(f"Поток-прокси {flow.bias.upper()} — без явного подтверждения движения.")

    # Прогноз / сценарий
    if fc_scen:
        scen_map = {
            "deal_tape": "сценарий deal-tape (снятие premium) обычно давит цену вниз",
            "disruption": "сценарий disruption (блок/атаки) обычно поддерживает цену вверх",
            "inventory": "сценарий inventory — смотри сюрприз EIA",
            "mixed_geo": "mixed geo — рынок дёргается в обе стороны",
            "range": "range — без сильного фундаментального края",
        }
        drivers.append(f"Сценарий бота: {fc_scen} — {scen_map.get(fc_scen, fc_scen)}.")
    if fc_bias and fc_bias != "WAIT":
        if (fc_bias == "LONG" and direction == "down") or (fc_bias == "SHORT" and direction == "up"):
            against.append(f"Прогноз бота {fc_bias}, а цена сейчас идёт иначе — локальный шум возможен.")

    # Честность: конфликт новости vs движение
    if direction == "up" and news == "bearish":
        against.append(
            "Свежие новости в сумме медвежьи, а цена растёт — чаще отскок/шорт-сквиз, "
            "а не новый бычий фундамент."
        )
        conf = 4
    elif direction == "down" and news == "bullish":
        against.append(
            "Новости в сумме бычьи, цена падает — возможна фиксация / снятие geo-spike."
        )
        conf = 4
    elif direction == "flat":
        conf = 5
    elif items and (news in {"bullish", "bearish"} or themes):
        conf = 7
    elif items:
        conf = 5
    else:
        conf = 3
        unknown.append("Мало свежих приоритетных новостей в окне — объяснение слабее.")

    if direction == "up":
        headline = f"UKOUSD растёт (~1ч {m1:+.2f}%) — что видно по фактам"
        howto = (
            "Если рост совпадает с эскалацией/OI-блоком — не ловить short против premium. "
            "Если новости медвежьи, а цена зелёная — осторожный fade только от сопротивления."
        )
    elif direction == "down":
        headline = f"UKOUSD падает (~1ч {m1:+.2f}%) — что видно по фактам"
        howto = (
            "Если падение на «танкеры Ормуз / deal» — тренд снятие premium. "
            "Лонги только от сильной поддержки или после смены ленты."
        )
    else:
        headline = "UKOUSD без ясного края — шум/база"
        howto = "Не выдумывать причину. Ждать уровень или новый заголовок Иран/EIA/OPEC."

    if not against:
        against = ("Явных противоречий в данных бота сейчас нет.",)

    return OilWhyReport(
        direction=direction,
        move_1h_pct=round(m1, 2),
        move_4h_pct=round(m4, 2),
        price=px,
        confidence=conf,
        headline_ru=headline,
        drivers_ru=tuple(drivers[:8]),
        against_ru=tuple(against[:4]),
        unknown_ru=tuple(unknown[:4]),
        how_to_use_ru=howto,
        sources_note_ru=(
            "База: свечи BZ≈UKOUSD + приоритетные заголовки (Иран/Ормуз/EIA/OPEC/аналитики). "
            "Это реконструкция драйверов, не инсайд."
        ),
    )


def format_oil_why_report(rep: OilWhyReport) -> str:
    mark = {"up": "📈", "down": "📉", "flat": "➡️"}.get(rep.direction, "➡️")
    lines = [
        f"{mark} <b>Почему цена · UKOUSD</b> · уверенность {rep.confidence}/10",
        f"<i>{_esc(rep.headline_ru)}</i>",
        f"<i>${rep.price:.2f} · 1ч {rep.move_1h_pct:+.2f}% · 4ч {rep.move_4h_pct:+.2f}%</i>",
        "",
        "<b>Что поддерживает движение</b>",
    ]
    for d in rep.drivers_ru:
        lines.append(f"• {_esc(d)}")
    lines.append("")
    lines.append("<b>Что против / сомнения</b>")
    for a in rep.against_ru:
        lines.append(f"• {_esc(a)}")
    lines.append("")
    lines.append("<b>Чего мы не знаем</b>")
    for u in rep.unknown_ru:
        lines.append(f"• {_esc(u)}")
    lines.append("")
    lines.append(f"<b>Как использовать:</b> {_esc(rep.how_to_use_ru)}")
    lines.append("")
    lines.append(f"<i>{_esc(rep.sources_note_ru)}</i>")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
