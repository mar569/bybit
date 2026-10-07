"""Разбор под ситуацию: проза от фактов TA, не один шаблон на все монеты.

Сканер собирает данные по каждой паре; `classify_situation` выбирает ветку (импульс,
консолидация, supply-шорт, середина range, тренд, разворот, …) — только если факты
совпадают. Пример AVAAI (H4 supply + L/S + M15) — одна из веток, не обязательная.
"""
from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult

from .human_trade_brief import preferred_trade_side
from .ta_analysis import fmt_price


def _short_bias(ta: "TAAnalysisResult") -> bool:
    if preferred_trade_side(ta) == "short":
        return True
    if (ta.verdict or "").upper() == "SHORT":
        return True
    return (getattr(ta, "action_priority", "") or "").lower() == "short"


def _long_short_ratio(ta: "TAAnalysisResult") -> float | None:
    metrics = getattr(ta, "market_metrics", None) or {}
    if isinstance(metrics, dict):
        ar = metrics.get("account_ratio")
        if isinstance(ar, dict) and ar.get("long_short_ratio") is not None:
            try:
                v = float(ar["long_short_ratio"])
                return v if v > 0 else None
            except (TypeError, ValueError):
                pass
    for line in getattr(ta, "market_participation_lines", None) or []:
        m = re.search(r"L/S(?:\s+\w+)?\s+([\d.]+)", str(line))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _consolidation_htf_label(cons) -> str:
    lbl = str(getattr(cons, "label", "") or "")
    for tag in ("H4", "H1", "M15"):
        if tag in lbl.upper():
            return tag
    return "HTF"


def _at_htf_resistance(ta: "TAAnalysisResult") -> bool:
    cur = float(getattr(ta, "current_price", 0) or 0)
    if cur <= 0:
        return False
    rp = float(getattr(ta, "range_position", 0) or 0)
    nr = getattr(ta, "nearest_resistance", None)
    if nr and float(nr) > cur * 0.998 and abs(float(nr) - cur) / cur <= 0.035:
        return True
    if rp >= 0.78:
        return True
    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot > 0 and cur >= bot and cur >= top * 0.92:
            lbl = str(getattr(cons, "label", "") or "").upper()
            if any(x in lbl for x in ("H4", "H1", "M15")):
                return True
            if "БОКОВ" in lbl and (top - bot) / bot >= 0.03:
                return True
    if ta.entry_zone and len(ta.entry_zone) == 2:
        lo, hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
        if hi > lo and lo <= cur <= hi * 1.01:
            return _short_bias(ta)
    return False


def _htf_resistance_short_context(ta: "TAAnalysisResult") -> bool:
    if not _at_htf_resistance(ta):
        return False
    phase = str(getattr(ta, "phase", "") or "")
    live = str(getattr(ta, "reading_live_scenario", "") or "")
    post = bool(getattr(ta, "post_pump", False))
    rp = float(getattr(ta, "range_position", 0) or 0)
    hot = (
        phase in {"impulse_up", "blowoff", "squeeze"}
        or post
        or live in {"exhaustion", "reversal"}
        or rp >= 0.72
    )
    if not hot:
        return False
    return _short_bias(ta) and (rp >= 0.68 or live == "exhaustion" or post or phase == "impulse_up")


def _supply_band(ta: "TAAnalysisResult") -> tuple[float, float, str] | None:
    cons = getattr(ta, "consolidation", None)
    cur = float(getattr(ta, "current_price", 0) or 0)
    if cons is not None:
        top, bot = float(cons.top), float(cons.bottom)
        if top > bot > 0 and cur >= bot * 0.98:
            return bot, top, _consolidation_htf_label(cons)
    if ta.entry_zone and len(ta.entry_zone) == 2:
        lo, hi = float(ta.entry_zone[0]), float(ta.entry_zone[1])
        if hi > lo:
            return lo, hi, "M15"
    nr = getattr(ta, "nearest_resistance", None)
    if nr and cur > 0:
        p = float(nr)
        pad = max(p * 0.012, cur * 0.008)
        return p - pad, p + pad * 0.35, "HTF"
    return None


def _ltf_entry_hint(ta: "TAAnalysisResult") -> str:
    for pat in reversed(list(getattr(ta, "patterns", None) or [])[-10:]):
        pid = str(getattr(pat, "name", "") or "")
        label = str(getattr(pat, "label_ru", "") or "")
        low = f"{pid} {label}".lower()
        if "bear_engulf" in pid or ("медв" in low and "поглощ" in low):
            mid = int(getattr(ta, "mid_interval_minutes", 15) or 15)
            return f"медв. поглощение на M{mid} (точнее, чем на M5)"
    trigger = str(getattr(ta, "setup_trigger", "") or "").strip()
    if trigger:
        return trigger[:100]
    ltf = int(getattr(ta, "analysis_interval_minutes", 5) or 5)
    mid = int(getattr(ta, "mid_interval_minutes", 15) or 15)
    return (
        f"сначала картина на M{ltf}, вход уточнять на M{mid}: "
        "отказ / поглощающая красная у верха зоны"
    )


def _ls_crowd_line(ta: "TAAnalysisResult") -> str:
    ratio = _long_short_ratio(ta)
    if ratio is None:
        for line in getattr(ta, "market_participation_lines", None) or []:
            if "перекос в лонг" in str(line).lower() or "L/S" in str(line):
                return str(line).split("·")[0].strip()[:120]
        return ""
    if ratio >= 1.25:
        approx = f"≈{ratio:.1f}:1" if ratio >= 1.5 else f"{ratio:.2f}"
        return (
            f"L/S {approx} — перекос в лонг, при отказе от сопротивления логично сбривать лонги"
        )
    if ratio <= 0.8:
        return f"L/S {ratio:.2f} — перекос в шорт, осторожно с агрессивным шортом у сопротивления"
    return f"L/S {ratio:.2f} — без экстремального перекоса"


def classify_situation(ta: "TAAnalysisResult") -> str:
    phase = str(getattr(ta, "phase", "") or "")
    live = str(getattr(ta, "reading_live_scenario", "") or "")
    cur = float(getattr(ta, "current_price", 0) or 0)
    cons = getattr(ta, "consolidation", None)
    rp = float(getattr(ta, "range_position", 0) or 0)

    if _htf_resistance_short_context(ta):
        return "htf_resistance_short"

    if live == "exhaustion" or phase in {"impulse_up", "impulse_down"}:
        return "impulse_wait"
    if cons is not None and cons.top > cons.bottom > 0:
        if cur > 0 and cons.bottom <= cur <= cons.top * 1.02:
            return "inside_consolidation"
        if cur > cons.top * 1.005:
            return "above_consolidation"
        if cur < cons.bottom * 0.995:
            return "below_consolidation"
    if 0.35 <= rp <= 0.65:
        return "range_middle"
    if live == "continuation":
        return "trend_continuation"
    if live == "reversal":
        return "reversal_setup"
    return "mixed"


def _consolidation_band(ta: "TAAnalysisResult") -> tuple[float, float] | None:
    cons = getattr(ta, "consolidation", None)
    if cons is None:
        return None
    top, bot = float(cons.top), float(cons.bottom)
    if top > bot > 0:
        return bot, top
    return None


def _level_line(ta: "TAAnalysisResult", side: str) -> str:
    cur = float(getattr(ta, "current_price", 0) or 0)
    brk = getattr(ta, "breakout_level", None)
    brdn = getattr(ta, "breakdown_level", None)
    stop = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
    tps = list(getattr(ta, "target_prices", None) or [])[:2]

    bits: list[str] = []
    if side == "short" and brdn and cur > 0 and float(brdn) < cur:
        bits.append(f"триггер шорта — закреп ниже {fmt_price(float(brdn))}")
    elif side == "long" and brk and cur > 0 and float(brk) > cur * 0.998:
        bits.append(f"триггер лонга — закреп выше {fmt_price(float(brk))}")
    if stop:
        bits.append(f"стоп/отмена — {fmt_price(float(stop))}")
    if tps:
        bits.append(f"ориентир — {fmt_price(float(tps[0]))}")
    return "; ".join(bits)


def build_situational_brief_plain(ta: "TAAnalysisResult", *, symbol: str = "") -> str:
    sym = (symbol or "").strip().upper() or "Монета"
    cur = float(getattr(ta, "current_price", 0) or 0)
    px = fmt_price(cur) if cur > 0 else "—"
    kind = classify_situation(ta)
    side = preferred_trade_side(ta)
    band = _consolidation_band(ta)
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()

    lines: list[str] = []

    if kind == "htf_resistance_short":
        band = _supply_band(ta)
        stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
        if band:
            lo, hi, tf = band
            lines.append(
                f"{sym} (~{px}): пришли в зону сопротивления {tf} "
                f"{fmt_price(lo)}–{fmt_price(hi)} — здесь план набирать шорт лесенкой, "
                "диапазон бери с запасом (верх/низ зоны), не одним market."
            )
        else:
            lines.append(
                f"{sym} (~{px}): у сопротивления на старшем ТФ — шорт лесенкой от зоны, "
                "не догонять вертикальный импульс одной позицией."
            )
        if stack:
            lines.append(f"Глобально ({stack}): работаем от зоны сопротивления, вход — младшие ТФ.")
        ls = _ls_crowd_line(ta)
        if ls:
            lines.append(ls + ".")
        lines.append(f"Триггер: {_ltf_entry_hint(ta)}.")
        inv = getattr(ta, "invalidation_price", None) or getattr(ta, "setup_stop", None)
        tps = list(getattr(ta, "target_prices", None) or [])[:2]
        risk_bits: list[str] = []
        if inv:
            risk_bits.append(f"стоп/отмена — {fmt_price(float(inv))}")
        if tps:
            risk_bits.append(f"цели — {', '.join(fmt_price(float(t)) for t in tps)}")
        if risk_bits:
            lines.append("; ".join(risk_bits) + ".")
    elif kind == "impulse_wait":
        seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
        lines.append(
            f"{sym} (~{px}): сильный импульс — не гоняться в market. "
            f"{seek or 'Ждём откат, базу или новый триггер на младшем ТФ.'}"
        )
    elif kind == "inside_consolidation" and band:
        lo, hi = band
        cons = getattr(ta, "consolidation", None)
        tf_hint = ""
        if cons is not None:
            lbl = str(getattr(cons, "label", "") or "")
            if lbl.startswith(("H1:", "M15:", "H4:", "M15", "H1", "H4")):
                tf_hint = f" ({lbl.split(':')[0] if ':' in lbl else lbl.split()[0]})"
        lines.append(
            f"{sym} (~{px}): цена в консолидации{tf_hint} "
            f"{fmt_price(lo)}–{fmt_price(hi)}. Пока внутри — только наблюдение, без market."
        )
        if side == "short":
            lines.append(
                f"Если после консолидации закрепятся ниже {fmt_price(lo)}, "
                f"можно смотреть аккуратный шорт — {_level_line(ta, 'short') or 'стоп за верх диапазона'}."
            )
        elif side == "long":
            lines.append(
                f"Если закрепятся выше {fmt_price(hi)}, "
                f"логичнее лонг по пробою — {_level_line(ta, 'long') or 'стоп под диапазон'}."
            )
        else:
            brdn = getattr(ta, "breakdown_level", None)
            brk = getattr(ta, "breakout_level", None)
            if brdn:
                lines.append(
                    f"Снизу интересен пробой {fmt_price(float(brdn))} (шорт после подтверждения)."
                )
            if brk:
                lines.append(
                    f"Сверху — пробой {fmt_price(float(brk))} (лонг после подтверждения)."
                )
    elif kind == "below_consolidation" and band:
        lo, _hi = band
        lines.append(
            f"{sym} (~{px}): под диапазоном (граница ~{fmt_price(lo)}). "
            "Шорт имеет смысл только если нет быстрого возврата внутрь — ждём close, не лимит в пустоту."
        )
        lines.append(_level_line(ta, "short") or "Уточни стоп по локальному хаю отката.")
    elif kind == "above_consolidation" and band:
        _lo, hi = band
        lines.append(
            f"{sym} (~{px}): над консолидацией (пробой {fmt_price(hi)}). "
            "Не покупать верх без отката — либо ретest зоны, либо новый higher low."
        )
        lines.append(_level_line(ta, "long") or "Стоп — под пробитой границей.")
    elif kind == "range_middle":
        lines.append(
            f"{sym} (~{px}): середина диапазона — худшее место для входа. "
            "Ждём край диапазона или явный пробой с объёмом."
        )
    elif kind == "trend_continuation":
        tail = str(getattr(ta, "reading_seek_label", "") or "продолжение по старшему ТФ")
        lines.append(f"{sym} (~{px}): {tail}. Вход только по триггеру, не market.")
        lines.append(_level_line(ta, side or "long") or "")
    elif kind == "reversal_setup":
        lines.append(
            f"{sym} (~{px}): возможен разворот — {getattr(ta, 'reading_seek_label', '') or 'нужен свип и слом'}."
        )
        lines.append(_level_line(ta, side or "short") or "")
    else:
        if stack:
            lines.append(f"{sym} (~{px}): {stack}.")
        else:
            lines.append(f"{sym} (~{px}): смешанная картина.")
        seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
        if seek:
            lines.append(seek)

    v = (ta.verdict or "WAIT").upper()
    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    if v == "WAIT":
        lines.append(f"Сейчас: не входим ({conf}/10) — только лимит/триггер по плану.")
    else:
        lines.append(f"Сейчас: уклон {v} ({conf}/10), но без подтверждения не market.")

    return " ".join(x for x in lines if x).strip()


def build_situational_brief_html(ta: "TAAnalysisResult", *, symbol: str = "") -> str:
    text = build_situational_brief_plain(ta, symbol=symbol)
    if not text:
        return ""
    key = classify_situation(ta)
    title = {
        "htf_resistance_short": "Сопротивление · шорт",
        "impulse_wait": "Импульс",
        "inside_consolidation": "Консолидация",
        "below_consolidation": "Под диапазоном",
        "above_consolidation": "Над диапазоном",
        "range_middle": "Середина range",
        "trend_continuation": "По тренду",
        "reversal_setup": "Разворот",
        "mixed": "Разбор",
    }.get(key, "Разбор")
    return f"💬 <b>{escape(title)}</b>\n{escape(text)}"


def ai_situation_hint(ta: "TAAnalysisResult") -> str:
    """Короткая подсказка для промпта ИИ — какой стиль ответа нужен."""
    key = classify_situation(ta)
    hints = {
        "htf_resistance_short": (
            "Как у трейдера: H4/H1 зона сопротивления, шорт лесенкой в диапазоне supply, "
            "L/S перекос в лонг → сбрив лонгов, вход уточнять M5→M15 (поглощающая у верха). "
            "Уровни только из JSON."
        ),
        "impulse_wait": "Опиши импульс и почему не гонять. Жди откат/базу.",
        "inside_consolidation": (
            "Опиши консолидацию и два сценария: пробой вверх / закреп вниз для шорта. "
            "Как у трейдера: «стоит в боковике, после закрепа под X — аккуратный шорт»."
        ),
        "below_consolidation": "Цена под диапазоном — шорт только после подтверждения, не нож.",
        "above_consolidation": "Пробой вверх — не FOMO, жди retest или HL.",
        "range_middle": "Середина — WAIT, без входа.",
        "trend_continuation": "Тренд по HTF — вход только по триггеру из пакета.",
        "reversal_setup": "Разворот — условия из пакета, без выдуманных уровней.",
        "mixed": "Кратко по фактам из JSON, один сценарий.",
    }
    return hints.get(key, hints["mixed"])
