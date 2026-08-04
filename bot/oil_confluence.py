"""Confluence-setup UKOUSD: все факторы → редкий LONG/SHORT в ручной TA."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from .ta_analysis import TAAnalysisResult, fmt_price


@dataclass(frozen=True)
class OilConfluenceSetup:
    """Сильный торговый setup для чата ручного анализа."""

    side: str  # LONG | SHORT | WAIT
    quality: int  # 1–10
    entry_lo: float | None
    entry_hi: float | None
    stop: float | None
    tp1: float | None
    tp2: float | None
    invalidation: float | None
    horizon_ru: str
    factors_ru: tuple[str, ...]
    catalyst: str
    trigger_ru: str
    near_level: bool
    gemini_ru: str = ""
    price: float = 0.0


def _dist_pct(px: float, level: float | None) -> float | None:
    if not level or px <= 0:
        return None
    return abs(px - float(level)) / px * 100.0


def _pro_analyst_boost(news_items: Sequence[Any] | None) -> tuple[int, str]:
    """+баллы и имя, если в окне есть Blas/Kemp/Croft/Sen."""
    try:
        from .oil_monitor import match_pro_oil_analyst
    except Exception:
        return 0, ""
    best_name = ""
    best_boost = 0
    for it in list(news_items or []):
        hit = match_pro_oil_analyst(
            getattr(it, "title", "") or "",
            getattr(it, "source", "") or "",
        )
        if hit is None:
            continue
        name, boost = hit
        if boost > best_boost:
            best_boost = boost
            best_name = name
    if best_boost <= 0:
        return 0, ""
    # Нормируем boost 3–5 → +1…+2 к голосу стороны
    pts = 2 if best_boost >= 5 else 1
    return pts, best_name


def build_oil_confluence_setup(
    snap: Any,
    ta: TAAnalysisResult | None,
    *,
    forecast: Any | None = None,
    news_bias: Any | None = None,
    scalp_call: Any | None = None,
    bounce_plan: Any | None = None,
    news_items: Sequence[Any] | None = None,
    market_mood: str = "",
    interval_minutes: int = 15,
    ta_verdict_raw: str | None = None,
    ta_confidence_raw: int | None = None,
    min_quality: int = 7,
    near_pct: float = 0.35,
    bars: Sequence[Any] | None = None,
    session_block_minutes: float = 20.0,
    apply_session_filter: bool = True,
    apply_chase_filter: bool = True,
    require_close_break: bool = True,
) -> OilConfluenceSetup | None:
    """Голосование факторов. None / WAIT-side если нет сильного края."""
    from .oil_entry_filters import (
        is_session_open_fragile,
        last_bar_closes_beyond,
        measure_recent_move,
    )

    px = float(getattr(snap, "price", 0) or 0)
    if px <= 0:
        return None

    s = getattr(snap, "support", None)
    r = getattr(snap, "resistance", None)
    bd = getattr(snap, "breakdown", None)
    bo = getattr(snap, "breakout", None)
    hi7 = getattr(snap, "high_7d", None)
    lo7 = getattr(snap, "low_7d", None)

    ta_v = (ta_verdict_raw or getattr(snap, "verdict", None) or "WAIT").upper()
    ta_c = int(
        ta_confidence_raw
        if ta_confidence_raw is not None
        else (getattr(snap, "confidence", 0) or 0)
    )
    if ta is not None and ta_confidence_raw is None:
        ta_c = int(getattr(ta, "verdict_confidence", ta_c) or ta_c)

    news = getattr(news_bias, "bias", "neutral") if news_bias else "neutral"
    news_w = float(getattr(news_bias, "weighted_score", 0) or 0) if news_bias else 0.0
    fc_bias = (getattr(forecast, "bias", None) or "WAIT").upper() if forecast else "WAIT"
    fc_conf = int(getattr(forecast, "confidence", 0) or 0) if forecast else 0
    fc_scen = getattr(forecast, "scenario", "") if forecast else ""
    scalp_action = getattr(scalp_call, "action", "wait") if scalp_call else "wait"
    scalp_score = int(getattr(scalp_call, "score", 0) or 0) if scalp_call else 0

    near_s = (_dist_pct(px, s) or 99) <= near_pct
    near_r = (_dist_pct(px, r) or 99) <= near_pct
    near_bd = (_dist_pct(px, bd) or 99) <= max(0.25, near_pct * 0.8)
    near_bo = (_dist_pct(px, bo) or 99) <= max(0.25, near_pct * 0.8)
    near_level = near_s or near_r or near_bd or near_bo

    long_pts = 0
    short_pts = 0
    factors: list[str] = []

    # Forecast
    if fc_bias == "LONG":
        long_pts += 3 if fc_conf >= 6 else 2
        factors.append(f"прогноз LONG {fc_conf}/10" + (f" · {fc_scen}" if fc_scen else ""))
    elif fc_bias == "SHORT":
        short_pts += 3 if fc_conf >= 6 else 2
        factors.append(f"прогноз SHORT {fc_conf}/10" + (f" · {fc_scen}" if fc_scen else ""))
    elif forecast is not None:
        factors.append(f"прогноз WAIT · {fc_scen or 'range'}")

    # News bias
    if news == "bullish":
        add = 3 if abs(news_w) >= 3 else 2 if abs(news_w) >= 1.5 else 1
        long_pts += add
        factors.append(f"новости↑ {news_w:+.1f}/10")
    elif news == "bearish":
        add = 3 if abs(news_w) >= 3 else 2 if abs(news_w) >= 1.5 else 1
        short_pts += add
        factors.append(f"новости↓ {news_w:+.1f}/10")
    elif news == "mixed":
        factors.append("новости mixed")
    elif news_bias is not None:
        factors.append("новости нейтральны")

    # TA
    if ta_v == "LONG":
        long_pts += 3 if ta_c >= 6 else 2 if ta_c >= 4 else 1
        factors.append(f"TA LONG {ta_c}/10")
    elif ta_v == "SHORT":
        short_pts += 3 if ta_c >= 6 else 2 if ta_c >= 4 else 1
        factors.append(f"TA SHORT {ta_c}/10")
    else:
        factors.append(f"TA WAIT {ta_c}/10")

    # Scalp
    if scalp_action == "open_long":
        long_pts += 2 if scalp_score >= 7 else 1
        factors.append(f"скальп LONG score {scalp_score}")
    elif scalp_action == "open_short":
        short_pts += 2 if scalp_score >= 7 else 1
        factors.append(f"скальп SHORT score {scalp_score}")

    # Bounce
    if bounce_plan is not None:
        bside = getattr(bounce_plan, "side", "")
        strong = bool(getattr(bounce_plan, "strong", False))
        if bside == "long":
            long_pts += 3 if strong else 2
            factors.append(f"отскок LONG у {fmt_price(bounce_plan.bounce_level)}")
        elif bside == "short":
            short_pts += 3 if strong else 2
            factors.append(f"отскок SHORT у {fmt_price(bounce_plan.bounce_level)}")

    # Level proximity (только усиливает сторону, не создаёт одну)
    if near_s or near_bo:
        long_pts += 1
        factors.append(
            "цена у support" if near_s else "цена у breakout↑"
        )
    if near_r or near_bd:
        short_pts += 1
        factors.append(
            "цена у resistance" if near_r else "цена у breakdown↓"
        )

    # Pro analysts
    pro_pts, pro_name = _pro_analyst_boost(news_items)
    catalyst = ""
    if news_bias and getattr(news_bias, "top_catalyst", ""):
        catalyst = str(news_bias.top_catalyst)[:140]
    if pro_name:
        # Толкаем в сторону уже лидирующего bias / news
        if long_pts > short_pts:
            long_pts += pro_pts
        elif short_pts > long_pts:
            short_pts += pro_pts
        elif news == "bullish":
            long_pts += pro_pts
        elif news == "bearish":
            short_pts += pro_pts
        factors.append(f"⭐ {pro_name}")
        if not catalyst:
            catalyst = pro_name

    if market_mood:
        factors.append(f"режим: {market_mood.split('—')[0].strip()[:40]}")

    # Conflict penalty
    if long_pts > 0 and short_pts > 0 and abs(long_pts - short_pts) <= 1:
        return OilConfluenceSetup(
            side="WAIT",
            quality=min(6, max(3, max(long_pts, short_pts))),
            entry_lo=None,
            entry_hi=None,
            stop=None,
            tp1=None,
            tp2=None,
            invalidation=None,
            horizon_ru=f"ждать clarifier · TF {interval_minutes}m",
            factors_ru=tuple(factors[:8]),
            catalyst=catalyst,
            trigger_ru="Конфликт факторов — не входить",
            near_level=near_level,
            price=px,
        )

    if long_pts > short_pts:
        side = "LONG"
        edge = long_pts - short_pts
    elif short_pts > long_pts:
        side = "SHORT"
        edge = short_pts - long_pts
    else:
        return None

    quality = min(10, max(1, 4 + edge + (1 if near_level else 0) + (1 if ta_c >= 6 else 0)))
    # Сильный конфликт news vs TA режет quality
    if (news == "bullish" and ta_v == "SHORT") or (news == "bearish" and ta_v == "LONG"):
        quality = min(quality, 6)
        factors.append("конфликт news↔TA")

    # Levels for side
    entry_lo = entry_hi = stop = tp1 = tp2 = inv = None
    trigger = ""
    if bounce_plan is not None and getattr(bounce_plan, "side", "") == side.lower():
        entry_lo = float(bounce_plan.entry_lo)
        entry_hi = float(bounce_plan.entry_hi)
        stop = float(bounce_plan.stop)
        tps = list(bounce_plan.targets or ())
        tp1 = float(tps[0]) if tps else None
        tp2 = float(tps[1]) if len(tps) > 1 else None
        trigger = f"отскок от {fmt_price(bounce_plan.bounce_level)}"
    elif side == "LONG":
        if near_s and s:
            entry_lo = float(s) * 0.998
            entry_hi = min(px, float(s) * 1.003)
            stop = float(bd or s * 0.992)
            tp1 = float(r or bo or px * 1.006) if (r or bo) else px * 1.006
            tp2 = float(bo or hi7 or tp1 * 1.004) if (bo or hi7) else None
            inv = stop
            trigger = f"касание S {fmt_price(s)}"
        elif near_bo and bo:
            entry_lo = float(bo)
            entry_hi = float(bo) * 1.002
            stop = float(s or bd or bo * 0.992)
            tp1 = float(hi7 or bo * 1.008) if hi7 else bo * 1.008
            tp2 = float(hi7) if hi7 else None
            inv = stop
            trigger = f"close/hold выше BO {fmt_price(bo)}"
        else:
            # Нет близости — setup только как watch (ниже gate)
            entry_lo = float(s) * 0.998 if s else px * 0.997
            entry_hi = float(s) * 1.002 if s else px
            stop = float(bd or (s * 0.99 if s else px * 0.992))
            tp1 = float(r or bo or px * 1.008)
            tp2 = float(bo or hi7) if (bo or hi7) else None
            inv = stop
            trigger = "ждать цену у S или close выше breakout↑"
    else:  # SHORT
        if near_r and r:
            entry_lo = max(px, float(r) * 0.997)
            entry_hi = float(r) * 1.002
            stop = float(bo or r * 1.008)
            tp1 = float(s or bd or px * 0.994) if (s or bd) else px * 0.994
            tp2 = float(bd or lo7 or tp1 * 0.996) if (bd or lo7) else None
            inv = stop
            trigger = f"касание R {fmt_price(r)}"
        elif near_bd and bd:
            entry_lo = float(bd) * 0.998
            entry_hi = float(bd)
            stop = float(r or bo or bd * 1.008)
            tp1 = float(lo7 or bd * 0.992) if lo7 else bd * 0.992
            tp2 = float(lo7) if lo7 else None
            inv = stop
            trigger = f"close/hold ниже BD {fmt_price(bd)}"
        else:
            entry_lo = float(r) * 0.998 if r else px
            entry_hi = float(r) * 1.002 if r else px * 1.003
            stop = float(bo or (r * 1.01 if r else px * 1.008))
            tp1 = float(s or bd or px * 0.992)
            tp2 = float(bd or lo7) if (bd or lo7) else None
            inv = stop
            trigger = "ждать цену у R или close ниже breakdown↓"

    horizon = "intraday 4–12ч"
    if fc_scen in {"deal_tape", "disruption", "mixed_geo"}:
        horizon = "свинг 1–3д · intraday по уровню"
    elif scalp_action in {"open_long", "open_short"}:
        lo = getattr(scalp_call, "hold_min", 20)
        hi = getattr(scalp_call, "hold_max", 75)
        horizon = f"скальп {lo}–{hi} мин · либо свинг если hold уровня"

    setup = OilConfluenceSetup(
        side=side,
        quality=quality,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        invalidation=inv,
        horizon_ru=horizon,
        factors_ru=tuple(factors[:8]),
        catalyst=catalyst,
        trigger_ru=trigger,
        near_level=near_level,
        price=px,
    )

    # Сессия / chase / close за уровнем
    break_level = None
    need_close = False
    if side == "LONG" and near_bo and bo:
        break_level = float(bo)
        need_close = bool(require_close_break)
    elif side == "SHORT" and near_bd and bd:
        break_level = float(bd)
        need_close = bool(require_close_break)

    if apply_session_filter and is_session_open_fragile(
        block_minutes=session_block_minutes,
    ):
        return replace(
            setup,
            side="WAIT",
            trigger_ru=(
                f"Bias {side}, но открытие сессии (<{session_block_minutes:.0f}м) — "
                f"ждать: {trigger}"
            ),
            quality=min(quality, int(min_quality) - 1),
            factors_ru=tuple(list(factors[:7]) + ["фильтр: открытие сессии"]),
        )

    move = measure_recent_move(bars, interval_minutes=interval_minutes)
    if apply_chase_filter and move and move.priced_in and not near_level:
        # Уже уехали в сторону сигнала — только WAIT у уровня
        chased = (
            (side == "LONG" and (move.move_30m_pct >= 0.55 or move.move_60m_pct >= 0.9))
            or (side == "SHORT" and (move.move_30m_pct <= -0.55 or move.move_60m_pct <= -0.9))
        )
        if chased:
            return replace(
                setup,
                side="WAIT",
                trigger_ru=(
                    f"Bias {side}, но {move.note_ru or 'ход уже сделан'} — "
                    f"ждать уровень: {trigger}"
                ),
                quality=min(quality, int(min_quality) - 1),
                factors_ru=tuple(list(factors[:7]) + ["фильтр: не chase"]),
            )

    if need_close and break_level is not None:
        if not last_bar_closes_beyond(bars, side=side.lower(), level=break_level):
            return replace(
                setup,
                side="WAIT",
                trigger_ru=(
                    f"Bias {side}: ждать close за {fmt_price(break_level)} "
                    f"(сейчас только касание)"
                ),
                quality=min(quality, int(min_quality) - 1),
                factors_ru=tuple(list(factors[:7]) + ["фильтр: нет close-триггера"]),
            )

    # High-conviction gate: quality + (near level OR bounce aligned OR scalp open)
    ready = (
        quality >= int(min_quality)
        and side in {"LONG", "SHORT"}
        and (
            near_level
            or (
                bounce_plan is not None
                and getattr(bounce_plan, "side", "") == side.lower()
            )
            or scalp_action == ("open_long" if side == "LONG" else "open_short")
        )
    )
    if not ready:
        # Возвращаем WAIT-обёртку только если качество почти прошло — иначе None
        if quality >= max(5, int(min_quality) - 2) and side in {"LONG", "SHORT"}:
            return replace(
                setup,
                side="WAIT",
                trigger_ru=f"Bias {side}, но нет касания уровня — ждать: {trigger}",
                quality=min(quality, int(min_quality) - 1),
            )
        return None
    return setup


def format_oil_confluence_setup(setup: OilConfluenceSetup) -> str:
    """HTML для Telegram (ручной TA) — чеклист + детали без простыни."""
    from .oil_journal import risk_checklist_lines

    if setup.side == "WAIT":
        mark = "⚪"
        title = "Bybit UKOUSD.s · ждать (нет сильного края)"
    elif setup.side == "LONG":
        mark = "🟢"
        title = "Вход LONG · Bybit UKOUSD.s"
    else:
        mark = "🔴"
        title = "Вход SHORT · Bybit UKOUSD.s"

    lines = [
        f"{mark} <b>{title}</b> · качество {setup.quality}/10",
        f"<i>Сейчас ≈${setup.price:.2f} · {setup.horizon_ru} · только Bybit TradFi</i>",
        "",
    ]
    if setup.side in {"LONG", "SHORT"}:
        lines.extend(
            risk_checklist_lines(
                side=setup.side,
                price=setup.price,
                entry_lo=setup.entry_lo,
                entry_hi=setup.entry_hi,
                stop=setup.stop,
                tp1=setup.tp1,
                tp2=setup.tp2,
                invalidation=setup.invalidation,
                catalyst=setup.catalyst,
            )
        )
        lines.append("")
        lines.append(f"• Триггер: {_esc(setup.trigger_ru)}")
    else:
        lines.append(f"• {_esc(setup.trigger_ru)}")

    if setup.factors_ru:
        lines.append("")
        lines.append("<b>Почему сейчас</b>")
        for f in setup.factors_ru[:5]:
            lines.append(f"• {_esc(f)}")
    if setup.gemini_ru:
        lines.append("")
        lines.append(f"🤖 <b>Главное от ИИ</b>\n{_esc(setup.gemini_ru)}")
    lines.append("")
    lines.append(
        "<i>Инструмент: только Bybit TradFi <b>UKOUSD.s</b>. "
        "Новости США/Иран/Ормуз важнее «красивого» чужого графика. "
        "Нет касания уровня — не входить. Не финсовет.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def setup_passes_gate(
    setup: OilConfluenceSetup | None,
    *,
    min_quality: int = 7,
) -> bool:
    """True только для отправки в ручной TA."""
    if setup is None:
        return False
    if setup.side not in {"LONG", "SHORT"}:
        return False
    return int(setup.quality) >= int(min_quality)


async def enrich_setup_with_gemini(
    setup: OilConfluenceSetup,
    snap: Any,
    *,
    news_items: Sequence[Any] | None = None,
    api_key: str | None = None,
    model: str = "gemini-3.6-flash",
    memory_ru: str = "",
) -> OilConfluenceSetup:
    """Короткий AI-абзац; ошибка не ломает setup."""
    if not api_key or setup.side not in {"LONG", "SHORT"}:
        return setup
    try:
        from .ai_analyst import ask_gemini, sanitize_ai_reply_for_telegram

        titles = []
        for it in list(news_items or [])[:6]:
            t = getattr(it, "title", "") or ""
            if t:
                titles.append(f"- {t[:120]}")
        news_block = "\n".join(titles) if titles else "(нет)"
        memory = (memory_ru or "").strip()
        memory_block = f"\nОпыт прошлых сигналов бота:\n{memory}\n" if memory else ""
        ctx = (
            "Ты трейдер нефти. Пиши по-русски простыми словами, 5–8 строк: "
            "выжми важное, немного деталей, без простыни и без англ. жаргона.\n"
            f"SETUP: {setup.side} quality={setup.quality}\n"
            f"Цена ${setup.price:.2f} entry={setup.entry_lo}-{setup.entry_hi} "
            f"stop={setup.stop} tp={setup.tp1}/{setup.tp2}\n"
            f"Триггер: {setup.trigger_ru}\n"
            f"Факторы: {'; '.join(setup.factors_ru)}\n"
            f"Новости:\n{news_block}"
            f"{memory_block}"
        )
        result = await ask_gemini(
            api_key=api_key,
            model=model,
            context_text=ctx,
            user_text=(
                "Структура: главное ПОЧЕМУ вход валиден; куда цена; "
                "один риск; когда отменять. Не меняй сторону. Не финсовет. "
                "Свежие критичные новости (Ормуз/Иран) важнее старой статистики."
            ),
        )
        text = sanitize_ai_reply_for_telegram(result.text or "").strip()
        if not text or result.error:
            return setup
        if len(text) > 600:
            text = text[:597] + "…"
        return replace(setup, gemini_ru=text)
    except Exception:
        return setup
