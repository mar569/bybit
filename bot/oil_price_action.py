"""Система прайс-экшен для нефти (пробой / отскок / ретест / EMA / зоны).

Сводит правила из обучающих схем:
- трейдер паттернов vs зон;
- типы разворота (HH/HL → LH/LL, пробой+ретест);
- прямоугольник / флаг (через primary chart pattern);
- EMA 9 / 21 / 50;
- бычий/медвежий ретест спроса, уровня, ордерблока;
- свечи (молот / поглощение) как confluence.

Не заменяет UT/Vataga △ — добавляет голоса и отрисовку.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class OilOrderBlock:
    """База импульса = зона спроса/предложения."""

    top: float
    bottom: float
    side: str  # demand | supply
    start_idx: int
    end_idx: int
    label_ru: str


@dataclass(frozen=True)
class OilPriceActionPlan:
    """Итог PA-системы для нефти."""

    bias: str  # long | short | neutral
    mode: str  # breakout | bounce | retest | wait
    structure: str  # bullish | bearish | range | unknown
    ema_stack: str  # bull | bear | mixed | none
    ema9: float
    ema21: float
    ema50: float
    allow_long: bool
    allow_short: bool
    stop: float | None
    tp1: float | None
    demand: OilOrderBlock | None
    supply: OilOrderBlock | None
    line_ru: str
    why_ru: str
    factors_ru: tuple[str, ...]
    # ряды для графика (длина = len bars)
    ema9_series: tuple[float, ...] = ()
    ema21_series: tuple[float, ...] = ()
    ema50_series: tuple[float, ...] = ()


def _closes(bars: Sequence[Any]) -> list[float]:
    out: list[float] = []
    for b in bars:
        try:
            c = float(getattr(b, "close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out.append(c)
    return out


def compute_ema_series(values: list[float], period: int) -> list[float]:
    """Классическая EMA; до прогрева — SMA seed."""
    n = len(values)
    if n == 0 or period < 1:
        return []
    p = int(period)
    out = [0.0] * n
    if n < p:
        s = 0.0
        for i, v in enumerate(values):
            s += v
            out[i] = s / (i + 1)
        return out
    seed = sum(values[:p]) / p
    out[p - 1] = seed
    for i in range(p - 1):
        out[i] = sum(values[: i + 1]) / (i + 1)
    k = 2.0 / (p + 1)
    for i in range(p, n):
        out[i] = values[i] * k + out[i - 1] * (1.0 - k)
    return out


def _ema_stack_bias(px: float, e9: float, e21: float, e50: float) -> str:
    if e9 <= 0 or e21 <= 0 or e50 <= 0:
        return "none"
    if px > e50 and e9 > e21 > e50:
        return "bull"
    if px < e50 and e9 < e21 < e50:
        return "bear"
    return "mixed"


def _structure_bias(ta: Any | None, bars: Sequence[Any] | None) -> str:
    label = (getattr(ta, "structure_label", "") or "").lower() if ta else ""
    if "быч" in label or "hh" in label:
        return "bullish"
    if "медв" in label or "lh" in label or "ll" in label:
        return "bearish"
    smc = getattr(ta, "smc", None) if ta else None
    if smc is not None:
        ltf = (getattr(smc, "ltf_structure", "") or "").lower()
        if ltf in {"bullish", "bearish"}:
            return ltf
    # fallback swings
    if bars and len(bars) >= 20:
        try:
            from .ta_analysis import classify_structure, find_swing_points

            swings = find_swing_points(list(bars), window=2)
            lab = (classify_structure(swings) or "").lower()
            if "быч" in lab:
                return "bullish"
            if "медв" in lab:
                return "bearish"
        except Exception:
            pass
    return "range" if label else "unknown"


def _detect_order_blocks(bars: Sequence[Any], *, lookback: int = 40) -> tuple[OilOrderBlock | None, OilOrderBlock | None]:
    """Последняя база перед импульсом ≥ 0.35% = demand/supply."""
    raw = list(bars or [])
    if len(raw) < 12:
        return None, None
    window = raw[-lookback:] if len(raw) > lookback else raw
    offset = len(raw) - len(window)
    demand: OilOrderBlock | None = None
    supply: OilOrderBlock | None = None

    for i in range(3, len(window) - 2):
        b0, b1, b2 = window[i - 1], window[i], window[i + 1]
        try:
            c0 = float(b0.close)
            c2 = float(b2.close)
            o1, h1, l1, c1 = float(b1.open), float(b1.high), float(b1.low), float(b1.close)
        except (TypeError, ValueError):
            continue
        if c0 <= 0:
            continue
        # bullish impulse after base candle
        move_up = (c2 - c0) / c0 * 100.0
        move_dn = (c0 - c2) / c0 * 100.0
        body = abs(c1 - o1)
        rng = max(h1 - l1, 1e-9)
        if move_up >= 0.35 and body / rng < 0.55:
            demand = OilOrderBlock(
                top=max(o1, c1),
                bottom=min(l1, min(o1, c1)),
                side="demand",
                start_idx=offset + i,
                end_idx=offset + i,
                label_ru="Зона спроса / OB",
            )
        if move_dn >= 0.35 and body / rng < 0.55:
            supply = OilOrderBlock(
                top=max(h1, max(o1, c1)),
                bottom=min(o1, c1),
                side="supply",
                start_idx=offset + i,
                end_idx=offset + i,
                label_ru="Зона предложения / OB",
            )
    return demand, supply


def _near(px: float, level: float, pct: float = 0.25) -> bool:
    if px <= 0 or level <= 0:
        return False
    return abs(px - level) / px * 100.0 <= pct


def _in_zone(px: float, top: float, bottom: float, pad_pct: float = 0.08) -> bool:
    if px <= 0 or top <= bottom:
        return False
    pad = px * pad_pct / 100.0
    return (bottom - pad) <= px <= (top + pad)


def _pattern_mode(ta: Any | None) -> tuple[str, str]:
    """mode hint + direction from primary chart pattern."""
    pat = getattr(ta, "primary_chart_pattern", None) if ta else None
    if pat is None:
        return "", "neutral"
    kind = (getattr(pat, "kind", "") or "").lower()
    status = (getattr(pat, "status", "") or "").lower()
    direction = (getattr(pat, "direction", "") or "neutral").lower()
    side = "long" if direction in {"bullish", "long"} else "short" if direction in {"bearish", "short"} else "neutral"
    if kind in {"flag", "pennant", "rectangle"} and status == "confirmed":
        return "breakout", side
    if kind.startswith("triangle") or kind.startswith("wedge"):
        if status == "confirmed":
            return "breakout", side
        return "wait", side
    if kind in {"head_shoulders", "double_top", "triple_top"} and status == "confirmed":
        return "breakout", "short"
    if kind in {"inverse_head_shoulders", "double_bottom"} and status == "confirmed":
        return "breakout", "long"
    if kind == "false_breakout":
        return "retest", side
    return "", side


def _candle_bias(ta: Any | None) -> tuple[str, str]:
    patterns = list(getattr(ta, "patterns", None) or []) if ta else []
    if not patterns:
        return "neutral", ""
    last = patterns[-1]
    name = (getattr(last, "name", "") or getattr(last, "kind", "") or "").lower()
    bullish = getattr(last, "bullish", None)
    label = getattr(last, "label_ru", None) or name
    if bullish is True or name in {"hammer", "bull_engulf", "morning"}:
        return "long", str(label)
    if bullish is False or name in {"pin_bar", "bear_engulf", "shooting"}:
        return "short", str(label)
    if "молот" in str(label).lower() or "поглощ" in str(label).lower() and "быч" in str(label).lower():
        return "long", str(label)
    if "медв" in str(label).lower():
        return "short", str(label)
    return "neutral", str(label)


def analyze_oil_price_action(
    bars: Sequence[Any] | None,
    ta: Any | None = None,
) -> OilPriceActionPlan | None:
    """Полный разбор PA для нефти."""
    raw = list(bars or [])
    if len(raw) < 30:
        return None
    closes = _closes(raw)
    if len(closes) < 30:
        return None
    px = closes[-1]
    e9s = compute_ema_series(closes, 9)
    e21s = compute_ema_series(closes, 21)
    e50s = compute_ema_series(closes, 50)
    e9, e21, e50 = e9s[-1], e21s[-1], e50s[-1]
    ema_stack = _ema_stack_bias(px, e9, e21, e50)
    structure = _structure_bias(ta, raw)
    demand, supply = _detect_order_blocks(raw)
    pat_mode, pat_side = _pattern_mode(ta)
    candle_side, candle_label = _candle_bias(ta)

    # уровни из TA
    s = float(getattr(ta, "nearest_support", 0) or 0) if ta else 0.0
    r = float(getattr(ta, "nearest_resistance", 0) or 0) if ta else 0.0
    bo = float(getattr(ta, "breakout_level", 0) or 0) if ta else 0.0
    bd = float(getattr(ta, "breakdown_level", 0) or 0) if ta else 0.0

    mode = "wait"
    bias = "neutral"
    factors: list[str] = []
    stop: float | None = None
    tp1: float | None = None

    # EMA голос
    if ema_stack == "bull":
        factors.append("EMA 9>21>50 · цена выше 50 — бычий стек")
    elif ema_stack == "bear":
        factors.append("EMA 9<21<50 · цена ниже 50 — медвежий стек")
    else:
        factors.append("EMA mixed — без чистого тренда")

    if structure == "bullish":
        factors.append("Структура HH+HL (бычья)")
    elif structure == "bearish":
        factors.append("Структура LH+LL (медвежья)")

    # --- Режимы входа (приоритет) ---
    # 1) Retest demand после роста / supply после падения
    if demand is not None and _in_zone(px, demand.top, demand.bottom):
        mode = "retest"
        bias = "long"
        stop = demand.bottom * 0.997
        tp1 = r or bo or px * 1.006
        factors.append(f"Ретест спроса {demand.bottom:.2f}–{demand.top:.2f}")
    elif supply is not None and _in_zone(px, supply.top, supply.bottom):
        mode = "retest"
        bias = "short"
        stop = supply.top * 1.003
        tp1 = s or bd or px * 0.994
        factors.append(f"Ретест предложения {supply.bottom:.2f}–{supply.top:.2f}")
    # 2) Bounce от S/R
    elif s > 0 and _near(px, s, 0.28) and ema_stack != "bear":
        mode = "bounce"
        bias = "long"
        stop = s * 0.995
        tp1 = r or bo or px * 1.005
        factors.append(f"Отскок от поддержки {s:.2f}")
    elif r > 0 and _near(px, r, 0.28) and ema_stack != "bull":
        mode = "bounce"
        bias = "short"
        stop = r * 1.005
        tp1 = s or bd or px * 0.995
        factors.append(f"Отскок от сопротивления {r:.2f}")
    # 3) Breakout confirmed pattern / levels
    elif pat_mode == "breakout" and pat_side in {"long", "short"}:
        mode = "breakout"
        bias = pat_side
        factors.append(f"Пробой фигуры → {pat_side.upper()}")
        if pat_side == "long":
            stop = s or e21 * 0.997
            tp1 = getattr(getattr(ta, "primary_chart_pattern", None), "target_price", None) or (r or px * 1.008)
        else:
            stop = r or e21 * 1.003
            tp1 = getattr(getattr(ta, "primary_chart_pattern", None), "target_price", None) or (s or px * 0.992)
    elif bo > 0 and px > bo and closes[-2] <= bo and ema_stack != "bear":
        mode = "breakout"
        bias = "long"
        stop = bo * 0.997
        tp1 = px * 1.006
        factors.append(f"Пробой сопротивления {bo:.2f}")
    elif bd > 0 and px < bd and closes[-2] >= bd and ema_stack != "bull":
        mode = "breakout"
        bias = "short"
        stop = bd * 1.003
        tp1 = px * 0.994
        factors.append(f"Пробой поддержки {bd:.2f}")
    # 4) EMA cross as soft bias only
    elif ema_stack == "bull" and structure != "bearish":
        bias = "long"
        mode = "wait"
        factors.append("Стек EMA бычий — ждать ретест/отскок, не chase")
    elif ema_stack == "bear" and structure != "bullish":
        bias = "short"
        mode = "wait"
        factors.append("Стек EMA медвежий — ждать ретест/отскок, не chase")

    if candle_side in {"long", "short"} and candle_label:
        factors.append(f"Свеча: {candle_label}")
        if mode == "wait" and candle_side == bias:
            pass
        elif mode in {"bounce", "retest"} and candle_side == bias:
            factors.append("свеча подтверждает зону")
        elif mode in {"bounce", "retest"} and candle_side != bias and candle_side != "neutral":
            factors.append("свеча против зоны — осторожно")

    # Разрешение сторон: система не торгует против жёсткого стека+структуры
    allow_long = True
    allow_short = True
    if ema_stack == "bear" and structure == "bearish":
        allow_long = False
    if ema_stack == "bull" and structure == "bullish":
        allow_short = False
    if mode == "wait" and bias == "long":
        allow_short = allow_short and structure != "bullish"
    if mode == "wait" and bias == "short":
        allow_long = allow_long and structure != "bearish"
    if bias == "long" and mode in {"breakout", "bounce", "retest"}:
        allow_short = False
    if bias == "short" and mode in {"breakout", "bounce", "retest"}:
        allow_long = False

    mode_ru = {
        "breakout": "ПРОБОЙ",
        "bounce": "ОТСКОК",
        "retest": "РЕТЕСТ",
        "wait": "WAIT",
    }.get(mode, mode)
    line = f"PA {mode_ru} · bias {bias.upper()} · EMA {ema_stack} · {structure}"
    why = "; ".join(factors[:3]) if factors else "нет чистого PA-сетапа"

    try:
        stop_f = float(stop) if stop else None
    except (TypeError, ValueError):
        stop_f = None
    try:
        tp_f = float(tp1) if tp1 else None
    except (TypeError, ValueError):
        tp_f = None

    return OilPriceActionPlan(
        bias=bias,
        mode=mode,
        structure=structure,
        ema_stack=ema_stack,
        ema9=float(e9),
        ema21=float(e21),
        ema50=float(e50),
        allow_long=allow_long,
        allow_short=allow_short,
        stop=stop_f,
        tp1=tp_f,
        demand=demand,
        supply=supply,
        line_ru=line[:140],
        why_ru=why[:220],
        factors_ru=tuple(factors[:6]),
        ema9_series=tuple(e9s),
        ema21_series=tuple(e21s),
        ema50_series=tuple(e50s),
    )


def score_price_action_votes(
    plan: OilPriceActionPlan | None,
    *,
    weight: float = 1.0,
) -> tuple[int, int, list[str], dict[str, float | None]]:
    """Очки для confluence + уровни stop/tp1."""
    levels: dict[str, float | None] = {"stop": None, "tp1": None, "entry": None}
    if plan is None:
        return 0, 0, [], levels
    w = max(0.5, float(weight))
    long_pts = short_pts = 0
    factors = list(plan.factors_ru)

    if plan.mode in {"breakout", "retest"} and plan.bias == "long":
        long_pts += int(round((4 if plan.ema_stack == "bull" else 3) * w))
    elif plan.mode in {"breakout", "retest"} and plan.bias == "short":
        short_pts += int(round((4 if plan.ema_stack == "bear" else 3) * w))
    elif plan.mode == "bounce" and plan.bias == "long":
        long_pts += int(round(2 * w))
    elif plan.mode == "bounce" and plan.bias == "short":
        short_pts += int(round(2 * w))
    elif plan.mode == "wait":
        if plan.bias == "long" and plan.ema_stack == "bull":
            long_pts += int(round(1 * w))
        elif plan.bias == "short" and plan.ema_stack == "bear":
            short_pts += int(round(1 * w))

    if not plan.allow_long:
        long_pts = 0
        factors.append("PA: LONG закрыт (против стека/структуры)")
    if not plan.allow_short:
        short_pts = 0
        factors.append("PA: SHORT закрыт (против стека/структуры)")

    factors.insert(0, plan.line_ru)
    levels["stop"] = plan.stop
    levels["tp1"] = plan.tp1
    return long_pts, short_pts, factors[:5], levels


def pa_blocks_side(plan: OilPriceActionPlan | None, side: str) -> bool:
    if plan is None:
        return False
    s = (side or "").lower()
    if s in {"long", "buy"} and not plan.allow_long:
        return True
    if s in {"short", "sell"} and not plan.allow_short:
        return True
    return False
