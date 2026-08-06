"""Confluence-setup UKOUSD: все факторы → редкий LONG/SHORT в ручной TA."""
from __future__ import annotations

import time
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



def score_oil_tech_pack(
    ta: TAAnalysisResult | None,
    *,
    px: float,
    full_weight: bool = True,
) -> tuple[int, int, list[str], dict[str, float | None]]:
    """Полный техпакет: волны / Эллиотт / треугольники / фигуры / фаза.

    full_weight=True — нет сильных новостей, техника ведёт.
    full_weight=False — новость HOT, техника только подтверждает.
    """
    long_pts = 0
    short_pts = 0
    factors: list[str] = []
    levels: dict[str, float | None] = {
        "entry": None,
        "stop": None,
        "tp1": None,
        "tp2": None,
    }
    if ta is None or px <= 0:
        return 0, 0, factors, levels

    w = 1.0 if full_weight else 0.55

    ew_ready = bool(getattr(ta, "elliott_entry_ready", False))
    ew_conf = int(getattr(ta, "elliott_confidence", 0) or 0)
    ew_label = (getattr(ta, "elliott_label", "") or "")[:80]
    ew_path = (getattr(ta, "elliott_path_bias", "") or "").lower()
    wave_bias = (getattr(ta, "wave_bias", "") or "neutral").lower()
    wave_conf = int(getattr(ta, "wave_confidence", 0) or 0)

    if ew_ready and ew_conf >= 5:
        pts = int(round((3 if ew_conf >= 7 else 2) * w))
        mode = (getattr(ta, "elliott_entry_mode", "") or "").lower()
        if "short" in mode or ew_path == "short" or wave_bias == "short":
            short_pts += pts
            factors.append(
                f"EW вход SHORT · {ew_conf}/10"
                + (f" · {ew_label}" if ew_label else "")
            )
        elif "long" in mode or ew_path == "long" or wave_bias == "long":
            long_pts += pts
            factors.append(
                f"EW вход LONG · {ew_conf}/10"
                + (f" · {ew_label}" if ew_label else "")
            )
        if getattr(ta, "elliott_entry_price", None):
            levels["entry"] = float(ta.elliott_entry_price)
        if getattr(ta, "elliott_stop_price", None):
            levels["stop"] = float(ta.elliott_stop_price)
        tps = list(getattr(ta, "elliott_tp_prices", None) or [])
        if tps:
            levels["tp1"] = float(tps[0])
        if len(tps) > 1:
            levels["tp2"] = float(tps[1])
    elif wave_bias in {"long", "short"} and wave_conf >= 5:
        pts = int(round((2 if wave_conf >= 7 else 1) * w))
        if wave_bias == "long":
            long_pts += pts
        else:
            short_pts += pts
        factors.append(f"Волна {wave_bias.upper()} · {wave_conf}/10")

    tri_kind = (getattr(ta, "elliott_triangle_kind", "") or "").strip()
    tri_bias = (getattr(ta, "elliott_triangle_bias", "") or "").lower()
    if tri_kind and tri_bias in {"long", "short", "bullish", "bearish"}:
        pts = int(round(2 * w))
        side = "long" if tri_bias in {"long", "bullish"} else "short"
        if side == "long":
            long_pts += pts
        else:
            short_pts += pts
        factors.append(f"Треугольник {tri_kind} → {side.upper()}")

    try:
        from .chart_patterns import format_chart_pattern_compact, pick_primary_pattern

        patterns = list(getattr(ta, "chart_patterns", None) or [])
        primary = getattr(ta, "primary_chart_pattern", None) or pick_primary_pattern(
            patterns
        )
        if primary is not None:
            conf = float(getattr(primary, "confidence", 0) or 0)
            direction = (getattr(primary, "direction", "") or "neutral").lower()
            status = getattr(primary, "status", "") or ""
            label = format_chart_pattern_compact(primary) or getattr(
                primary, "label_ru", ""
            )
            if conf >= 0.55 and direction in {"long", "short", "bullish", "bearish"}:
                pts = int(
                    round((3 if conf >= 0.7 and status == "confirmed" else 2) * w)
                )
                if direction in {"long", "bullish"}:
                    long_pts += pts
                else:
                    short_pts += pts
                factors.append(f"Фигура: {label}" if label else f"Фигура {direction}")
                if full_weight:
                    if getattr(primary, "stop_price", None) and levels["stop"] is None:
                        levels["stop"] = float(primary.stop_price)
                    if getattr(primary, "target_price", None) and levels["tp1"] is None:
                        levels["tp1"] = float(primary.target_price)
            elif label:
                factors.append(f"Фигура (слабо): {label}")
    except Exception:
        pass

    phase = (getattr(ta, "phase_label", "") or "").lower()
    plabel = str(getattr(ta, "phase_label", "") or "")
    if "импульс вверх" in phase:
        long_pts += int(round(1 * w))
        factors.append(f"Фаза: {plabel[:40]}")
    elif "импульс вниз" in phase:
        short_pts += int(round(1 * w))
        factors.append(f"Фаза: {plabel[:40]}")
    elif any(k in phase for k in ("пробой", "сжатие", "breakout")):
        factors.append(f"Фаза: {plabel[:40]} — ждать close")
    elif plabel:
        factors.append(f"Фаза: {plabel[:40]}")

    structure = (getattr(ta, "structure_label", "") or "").lower()
    if full_weight:
        if "быч" in structure:
            long_pts += 1
        if "медвеж" in structure:
            short_pts += 1

    channel = getattr(ta, "channel", None)
    if channel is not None and full_weight:
        try:
            lo = float(
                getattr(channel, "lower", 0) or getattr(channel, "low", 0) or 0
            )
            hi = float(
                getattr(channel, "upper", 0) or getattr(channel, "high", 0) or 0
            )
            if lo > 0 and hi > lo:
                if abs(px - lo) / px <= 0.004:
                    long_pts += 1
                    factors.append("У низа канала")
                elif abs(px - hi) / px <= 0.004:
                    short_pts += 1
                    factors.append("У верха канала")
        except Exception:
            pass

    return long_pts, short_pts, factors, levels


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
    news_entry_max_age_hours: float = 1.0,
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

    # News: HOT ≤30м = очки входа; WARM/опоздание = только фон; priced-in = не догонять
    from .oil_news_discipline import assess_news_for_trade

    news_assess = assess_news_for_trade(
        news_items,
        news_bias=news_bias,
        bars=bars,
        hot_hours=float(news_entry_max_age_hours),
        warm_hours=2.0,
        priced_in_pct=0.35,
    )
    factors.append(f"📰 {news_assess.rule_ru}")
    age_note = news_assess.age_note

    if news == "bullish":
        if news_assess.for_entry:
            add = 3 if abs(news_w) >= 3 else 2 if abs(news_w) >= 1.5 else 1
            long_pts += add
            factors.append(f"новости↑ HOT {news_w:+.1f}/10 · {age_note}")
        elif news_assess.mode in {"warm", "hot"}:
            factors.append(f"новости↑ фон ({age_note}) — без очков входа")
        else:
            factors.append(f"новости↑ устарели ({age_note})")
    elif news == "bearish":
        if news_assess.for_entry:
            add = 3 if abs(news_w) >= 3 else 2 if abs(news_w) >= 1.5 else 1
            short_pts += add
            factors.append(f"новости↓ HOT {news_w:+.1f}/10 · {age_note}")
        elif news_assess.mode in {"warm", "hot"}:
            factors.append(f"новости↓ фон ({age_note}) — без очков входа")
        else:
            factors.append(f"новости↓ устарели ({age_note})")
    elif news == "mixed":
        factors.append("новости mixed — без входа по новостям")
    elif news_bias is not None:
        factors.append("новости нейтральны")

    # Ормуз / запасы — явный разбор в factors (не слепой deal SHORT)
    try:
        from .oil_signal_context import analyze_hormuz_context, analyze_inventory_from_news

        hz = analyze_hormuz_context(news_items)
        if hz.line_ru:
            factors.insert(1 if factors and factors[0].startswith("📐") else 0, hz.line_ru[:160])
        if hz.oil_bias == "mixed" or (
            hz.status in {"not_final", "progress"} and not hz.for_entry
        ):
            # Не раздувать SHORT по слуху о сделке
            short_pts = max(0, short_pts - 2)
        inv = analyze_inventory_from_news(news_items)
        if inv.line_ru:
            factors.append(inv.line_ru[:120])
            if inv.tone == "bearish" and news_assess.for_entry:
                short_pts += 1
            elif inv.tone == "bullish" and news_assess.for_entry:
                long_pts += 1
            elif inv.tone == "mixed":
                long_pts = max(0, long_pts - 1)
                short_pts = max(0, short_pts - 1)
    except Exception:
        pass

    # Техпакет: волны / EW / треугольники / фигуры — ведёт, если нет HOT-новостей
    tech_full = news_assess.mode in {"none", "cold"} or (
        news_assess.mode == "warm" and not news_assess.for_entry
    )
    if tech_full:
        factors.insert(0, "📐 режим: ТЕХНИКА (нет сильной свежей новости)")
    else:
        factors.append("режим: НОВОСТЬ + техника")
    t_long, t_short, t_factors, tech_levels = score_oil_tech_pack(
        ta, px=px, full_weight=tech_full
    )
    # Не толкать технику против блока новостей
    if news_assess.block_long:
        t_long = 0
    if news_assess.block_short:
        t_short = 0
    long_pts += t_long
    short_pts += t_short
    factors.extend(t_factors[:6])

    # TA
    if ta_v == "LONG":
        long_pts += 3 if ta_c >= 6 else 2 if ta_c >= 4 else 1
        factors.append(f"TA LONG {ta_c}/10")
    elif ta_v == "SHORT":
        short_pts += 3 if ta_c >= 6 else 2 if ta_c >= 4 else 1
        factors.append(f"TA SHORT {ta_c}/10")
    else:
        factors.append(f"TA WAIT {ta_c}/10")

    # Scalp — не усиливать против news discipline
    if scalp_action == "open_long" and not news_assess.block_long:
        long_pts += 2 if scalp_score >= 7 else 1
        factors.append(f"скальп LONG score {scalp_score}")
    elif scalp_action == "open_short" and not news_assess.block_short:
        short_pts += 2 if scalp_score >= 7 else 1
        factors.append(f"скальп SHORT score {scalp_score}")
    elif scalp_action in {"open_long", "open_short"}:
        factors.append("скальп отключён: конфликт с новостным фоном")

    # Bounce
    if bounce_plan is not None:
        bside = getattr(bounce_plan, "side", "")
        strong = bool(getattr(bounce_plan, "strong", False))
        if bside == "long" and not news_assess.block_long:
            long_pts += 3 if strong else 2
            factors.append(f"отскок LONG у {fmt_price(bounce_plan.bounce_level)}")
        elif bside == "short" and not news_assess.block_short:
            short_pts += 3 if strong else 2
            factors.append(f"отскок SHORT у {fmt_price(bounce_plan.bounce_level)}")
        elif bside in {"long", "short"}:
            factors.append("отскок против новостного фона — без очков")

    # Level proximity (только усиливает сторону, не создаёт одну)
    if (near_s or near_bo) and not news_assess.block_long:
        long_pts += 1
        factors.append(
            "цена у support" if near_s else "цена у breakout↑"
        )
    if (near_r or near_bd) and not news_assess.block_short:
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

    for note in read_oil_chart_structure(
        bars,
        price=px,
        support=float(s) if s else None,
        resistance=float(r) if r else None,
    ):
        factors.append(note)
        if "бычий каркас" in note:
            long_pts += 1
        elif "медвежий каркас" in note:
            short_pts += 1
        elif "не догонять" in note:
            # Импульс уже ушёл — не форсируем chase
            long_pts = max(0, long_pts - 1)
            short_pts = max(0, short_pts - 1)
        elif "середина" in note and not near_level:
            long_pts = max(0, long_pts - 1)
            short_pts = max(0, short_pts - 1)

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
    # Дисциплина новостей: блок стороны / опоздание / уже в цене
    if side == "LONG" and news_assess.block_long:
        quality = min(quality, 5)
        factors.append("блок LONG: новости↓ или уже отыграно")
    if side == "SHORT" and news_assess.block_short:
        quality = min(quality, 5)
        factors.append("блок SHORT: новости↑ или уже отыграно")
    if (news == "bullish" and ta_v == "SHORT") or (news == "bearish" and ta_v == "LONG"):
        quality = min(quality, 5)
        factors.append("конфликт news↔TA")
    if side == "LONG" and near_bo and news_assess.mode in {"warm", "hot"} and news == "bearish":
        quality = min(quality, 5)
        factors.append("пробой↑ при фоне сделки/Ормуз")
    if side == "SHORT" and near_bd and news_assess.mode in {"warm", "hot"} and news == "bullish":
        quality = min(quality, 5)
        factors.append("пробой↓ при бычьем новостном фоне")
    # Без HOT-новости не раздувать «новостной» пробой; техника (tech_full) — ок
    if (not tech_full) and news_assess.mode != "hot" and near_bo and side == "LONG" and news != "bullish":
        quality = min(quality, 6)
    if (not tech_full) and news_assess.mode != "hot" and near_bd and side == "SHORT" and news != "bearish":
        quality = min(quality, 6)
    if tech_full and (t_long >= 3 or t_short >= 3):
        quality = min(10, quality + 1)
        factors.append("техпакет согласован")

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

    # Техпакет: уровни EW/фигуры приоритетнее «голого» S/R, если техника ведёт
    if tech_full and tech_levels:
        te = tech_levels.get("entry")
        ts_ = tech_levels.get("stop")
        tt1 = tech_levels.get("tp1")
        tt2 = tech_levels.get("tp2")
        if te and te > 0:
            entry_lo = float(te) * 0.999
            entry_hi = float(te) * 1.001
            trigger = f"техвход EW/фигура ≈{fmt_price(te)}"
        if ts_ and ts_ > 0:
            stop = float(ts_)
            inv = stop
        if tt1 and tt1 > 0:
            tp1 = float(tt1)
        if tt2 and tt2 > 0:
            tp2 = float(tt2)
        if not catalyst and t_factors:
            catalyst = t_factors[0][:120]

    horizon = "intraday 4–12ч"
    if tech_full:
        horizon = "техника · intraday / свинг по фигуре-волне"
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
        factors_ru=tuple(factors[:10]),
        catalyst=catalyst,
        trigger_ru=trigger,
        near_level=near_level or bool(tech_full and tech_levels.get("entry")),
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

    # High-conviction gate: уровень ИЛИ bounce ИЛИ scalp ИЛИ техвход EW/фигура
    tech_ready = bool(
        tech_full
        and tech_levels.get("entry")
        and ((side == "LONG" and t_long >= 2) or (side == "SHORT" and t_short >= 2))
    )
    ready = (
        quality >= int(min_quality)
        and side in {"LONG", "SHORT"}
        and (
            near_level
            or tech_ready
            or setup.near_level
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


def read_oil_chart_structure(
    bars: Sequence[Any] | None,
    *,
    price: float,
    support: float | None = None,
    resistance: float | None = None,
) -> tuple[str, ...]:
    """Короткое чтение графика как у про: структура / позиция / уровни."""
    notes: list[str] = []
    if not bars or len(bars) < 12 or price <= 0:
        return tuple(notes)
    closes = [float(getattr(b, "close", 0) or 0) for b in bars[-48:]]
    highs = [float(getattr(b, "high", 0) or 0) for b in bars[-48:]]
    lows = [float(getattr(b, "low", 0) or 0) for b in bars[-48:]]
    if not closes or min(closes) <= 0:
        return tuple(notes)

    # Локальный swing: последние 20 vs предыдущие 20
    a, b = closes[-20:], closes[-40:-20] if len(closes) >= 40 else closes[: len(closes) // 2]
    if a and b:
        if a[-1] > max(b) * 0.999 and min(a) > min(b):
            notes.append("Структура: локальные HH/HL — бычий каркас")
        elif a[-1] < min(b) * 1.001 and max(a) < max(b):
            notes.append("Структура: локальные LH/LL — медвежий каркас")
        else:
            notes.append("Структура: смешанная / переходная")

    hi = max(highs[-24:]) if highs else price
    lo = min(lows[-24:]) if lows else price
    if hi > lo:
        pos = (price - lo) / (hi - lo)
        if pos >= 0.75:
            notes.append(f"Позиция в range: верх ({pos * 100:.0f}%) — осторожно с лонгом")
        elif pos <= 0.25:
            notes.append(f"Позиция в range: низ ({pos * 100:.0f}%) — осторожно с шортом")
        else:
            notes.append(f"Позиция в range: середина ({pos * 100:.0f}%) — ждать край")

    if support and resistance and support < resistance:
        mid = (float(support) + float(resistance)) / 2.0
        if abs(price - float(support)) / price < 0.004:
            notes.append(f"У уровня support ≈${float(support):.2f}")
        elif abs(price - float(resistance)) / price < 0.004:
            notes.append(f"У уровня resistance ≈${float(resistance):.2f}")
        elif price > mid:
            notes.append("Ближе к сопротивлению, чем к поддержке")
        else:
            notes.append("Ближе к поддержке, чем к сопротивлению")

    # Импульс последних 3 баров
    if len(closes) >= 4:
        ch = (closes[-1] - closes[-4]) / closes[-4] * 100.0
        if abs(ch) >= 0.35:
            notes.append(
                f"Импульс 15–20м: {ch:+.2f}% — "
                + ("не догонять" if abs(ch) >= 0.55 else "есть движение")
            )
    return tuple(notes[:5])


def format_oil_confluence_setup(
    setup: OilConfluenceSetup,
    *,
    news_items: Sequence[Any] | None = None,
    flow: Any | None = None,
    news_mode: str = "",
) -> str:
    """Понятная ПРО-карточка: почему / фон / план / отмена."""
    from .oil_signal_context import build_signal_drivers, format_clear_signal_card

    mode = news_mode
    if not mode:
        news_line = next((f for f in setup.factors_ru if f.startswith("📰")), "")
        if "HOT" in news_line:
            mode = "hot"
        elif "фон" in news_line or "WARM" in news_line:
            mode = "warm"
        elif "нет" in news_line.lower() or "none" in news_line.lower():
            mode = "none"
        else:
            mode = "cold"

    drivers = build_signal_drivers(
        news_items=news_items,
        news_mode=mode,
        flow=flow,
        side=setup.side,
    )
    # Если в factors уже есть Ормуз/техника — дополним why из factors
    tech_mode = any("режим: ТЕХНИКА" in f for f in setup.factors_ru)
    mode_tag = " · 📐 ТЕХНИКА" if tech_mode else ""
    if setup.catalyst and setup.side in {"LONG", "SHORT"} and drivers.why_ru == "схождение факторов слабое":
        drivers = type(drivers)(
            hormuz=drivers.hormuz,
            inventory=drivers.inventory,
            news_mode=drivers.news_mode,
            flow_ru=drivers.flow_ru,
            why_ru=str(setup.catalyst)[:100],
            caution_ru=drivers.caution_ru,
            lines_ru=drivers.lines_ru,
        )

    return format_clear_signal_card(
        side=setup.side,
        quality=setup.quality,
        price=setup.price,
        drivers=drivers,
        entry_lo=setup.entry_lo,
        entry_hi=setup.entry_hi,
        stop=setup.stop,
        tp1=setup.tp1,
        tp2=setup.tp2,
        trigger_ru=setup.trigger_ru,
        invalidation=setup.invalidation,
        horizon_ru=setup.horizon_ru,
        mode_tag=mode_tag,
        extra_ru=setup.gemini_ru,
    )


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
            "Ты профессиональный трейдер Brent (UKOUSD). Читаешь график: "
            "структура, уровни, импульс, новости Трамп/Бессент/Ормуз. "
            "Пиши по-русски, коротко, как на desk: 4–6 строк.\n"
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
                "Формат:\n"
                "1) Что вижу на графике (1 фраза)\n"
                "2) Почему вход валиден / или слабость\n"
                "3) Главный риск\n"
                "4) Когда отменять\n"
                "Не меняй сторону. Без цифр «идеального» входа сверх плана. Не финсовет."
            ),
        )
        text = sanitize_ai_reply_for_telegram(result.text or "").strip()
        if not text or result.error:
            return setup
        if len(text) > 700:
            text = text[:697] + "…"
        return replace(setup, gemini_ru=text)
    except Exception:
        return setup
