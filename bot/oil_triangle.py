"""Треугольник для нефти — логика Vataga / BuyHold.

Источник: https://vataga.trading/ru/blog/trading/pattern-treugolnik-signaly-i-strategii-v-treidinge

Правила для сделки:
- фигура = рамка (границы + приоритет), не вход сама по себе;
- внутри сжатия → WAIT / слабый голос;
- вход только на confirmed close-пробое границы;
- возврат внутрь после пробоя → ложный, сценарий отменён;
- восходящий → приоритет LONG; нисходящий → SHORT; симметричный → нейтраль/приор тренд;
- расширяющийся → осторожность, много ложных.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


TRIANGLE_KINDS = frozenset(
    {
        "triangle_symmetric",
        "triangle_ascending",
        "triangle_descending",
        "expanding_triangle",
        "wedge_rising",
        "wedge_falling",
    }
)


@dataclass(frozen=True)
class OilTrianglePlan:
    """Интерпретация треугольника для нефти."""

    kind: str
    label_ru: str
    status: str  # forming | confirmed
    priority: str  # long | short | neutral | caution
    action: str  # wait | long | short | cancel
    allow_long: bool
    allow_short: bool
    confidence: float
    stop: float | None
    tp1: float | None
    line_ru: str
    why_ru: str
    factors_ru: tuple[str, ...]


def _side(direction: str) -> str:
    d = (direction or "").lower()
    if d in {"long", "bullish", "buy"}:
        return "long"
    if d in {"short", "bearish", "sell"}:
        return "short"
    return "neutral"


def _priority_for_kind(kind: str, direction: str) -> str:
    k = (kind or "").lower()
    if k == "triangle_ascending":
        return "long"
    if k == "triangle_descending":
        return "short"
    if k == "expanding_triangle":
        return "caution"
    if k == "wedge_rising":
        return "short"
    if k == "wedge_falling":
        return "long"
    # symmetric — берём direction (prior trend), иначе neutral
    s = _side(direction)
    return s if s != "neutral" else "neutral"


def pick_oil_triangle_pattern(ta: Any | None) -> Any | None:
    """Главный треугольник/клин/ложный пробой с TA."""
    if ta is None:
        return None
    try:
        from .chart_patterns import pick_primary_pattern
    except Exception:
        pick_primary_pattern = None  # type: ignore[assignment]

    primary = getattr(ta, "primary_chart_pattern", None)
    patterns = list(getattr(ta, "chart_patterns", None) or [])

    def _is_tri(p: Any) -> bool:
        kind = (getattr(p, "kind", "") or "").lower()
        return kind in TRIANGLE_KINDS or kind == "false_breakout"

    cands = [p for p in patterns if _is_tri(p)]
    if primary is not None and _is_tri(primary):
        return primary
    if cands and pick_primary_pattern is not None:
        return pick_primary_pattern(cands)
    if cands:
        return max(cands, key=lambda p: float(getattr(p, "confidence", 0) or 0))
    return None


def interpret_oil_triangle(
    ta: Any | None,
    *,
    bars: Sequence[Any] | None = None,
    proposed_side: str | None = None,
) -> OilTrianglePlan | None:
    """Vataga-план по треугольнику на текущем TA."""
    pat = pick_oil_triangle_pattern(ta)
    if pat is None:
        # Elliott ABCDE triangle fallback
        e_kind = (getattr(ta, "elliott_triangle_kind", "") or "").strip() if ta else ""
        e_bias = (getattr(ta, "elliott_triangle_bias", "") or "").lower() if ta else ""
        if not e_kind:
            return None
        priority = _side(e_bias) if e_bias else "neutral"
        if "expand" in e_kind.lower():
            priority = "caution"
        return OilTrianglePlan(
            kind=f"elliott_{e_kind}",
            label_ru=f"EW-треугольник ({e_kind})",
            status="forming",
            priority=priority,
            action="wait",
            allow_long=priority in {"long", "neutral"},
            allow_short=priority in {"short", "neutral"},
            confidence=0.55,
            stop=None,
            tp1=None,
            line_ru=f"△ EW {e_kind} · приоритет {priority.upper() if priority != 'neutral' else 'WAIT'}",
            why_ru="Elliott-треугольник: внутри сжатия ждать close-пробой",
            factors_ru=(f"EW-треугольник {e_kind} → {priority}",),
        )

    kind = (getattr(pat, "kind", "") or "").lower()
    status = (getattr(pat, "status", "") or "forming").lower()
    conf = float(getattr(pat, "confidence", 0) or 0)
    label = getattr(pat, "label_ru", None) or kind
    direction = getattr(pat, "direction", "") or "neutral"
    stop = getattr(pat, "stop_price", None)
    tp1 = getattr(pat, "target_price", None)
    try:
        stop_f = float(stop) if stop else None
    except (TypeError, ValueError):
        stop_f = None
    try:
        tp_f = float(tp1) if tp1 else None
    except (TypeError, ValueError):
        tp_f = None

    # Ложный пробой — отмена сценария пробоя
    if kind == "false_breakout":
        side = _side(direction)
        return OilTrianglePlan(
            kind=kind,
            label_ru=str(label),
            status=status,
            priority=side if side != "neutral" else "caution",
            action="cancel" if status != "confirmed" else side if side != "neutral" else "wait",
            allow_long=side == "long",
            allow_short=side == "short",
            confidence=conf,
            stop=stop_f,
            tp1=tp_f,
            line_ru=f"△ ложный пробой → разворот {side.upper() if side != 'neutral' else '?'}",
            why_ru="Возврат внутрь после пробоя — сценарий пробоя отменён (Vataga)",
            factors_ru=(f"Ложный пробой ({status})",),
        )

    priority = _priority_for_kind(kind, direction)

    # Confirmed breakout → action = direction
    if status == "confirmed" and _side(direction) in {"long", "short"}:
        action = _side(direction)
        allow_long = action == "long"
        allow_short = action == "short"
        why = (
            f"Close-пробой {label}: вход по направлению пробоя "
            f"(цель ≈ ширина основания). Стоп — за противоположную границу."
        )
        line = f"△ {label} · ПРОБОЙ → {action.upper()}"
    elif priority == "caution":
        action = "wait"
        allow_long = False
        allow_short = False
        why = "Расходящийся треугольник — хаос/ложные; без подтверждения объёмом не входим"
        line = f"△ {label} · ОСТОРОЖНО (расходящийся)"
    elif status == "forming":
        action = "wait"
        # Приоритет стороны мягкий: не режем наглухо, но против приоритета — блок
        allow_long = priority in {"long", "neutral"}
        allow_short = priority in {"short", "neutral"}
        bias_ru = {
            "long": "приоритет покупок",
            "short": "приоритет продаж",
            "neutral": "нейтраль — ждать сторону",
        }.get(priority, "ждать")
        why = (
            f"Сжатие ({label}): {bias_ru}. Внутри фигуры WAIT — "
            f"вход только после close за границей (Vataga)."
        )
        line = f"△ {label} · форм. · {bias_ru}"
    else:
        action = "wait"
        allow_long = True
        allow_short = True
        why = f"{label}: нет чистого пробоя"
        line = f"△ {label}"

    # Предложенная сторона против приоритета при forming → запрет
    prop = (proposed_side or "").upper()
    if status == "forming" and priority == "long" and prop == "SHORT":
        allow_short = False
    if status == "forming" and priority == "short" and prop == "LONG":
        allow_long = False

    factors = [
        line,
        f"статус {status} · conf {conf:.0%}",
    ]
    if getattr(pat, "volume_breakout", False):
        factors.append("объём на пробое ✓")
    elif status == "forming" and getattr(pat, "volume_contracted", False):
        factors.append("объём сжат (типично для △)")

    _ = bars  # reserved for future false-break reclaim check on live bars
    return OilTrianglePlan(
        kind=kind,
        label_ru=str(label),
        status=status,
        priority=priority,
        action=action,
        allow_long=allow_long,
        allow_short=allow_short,
        confidence=conf,
        stop=stop_f,
        tp1=tp_f,
        line_ru=line[:120],
        why_ru=why[:220],
        factors_ru=tuple(factors[:4]),
    )


def triangle_blocks_side(plan: OilTrianglePlan | None, side: str) -> bool:
    """True если треугольник запрещает сторону."""
    if plan is None:
        return False
    s = (side or "").lower()
    if s in {"long", "buy"} and not plan.allow_long:
        return True
    if s in {"short", "sell"} and not plan.allow_short:
        return True
    return False


def score_triangle_votes(plan: OilTrianglePlan | None, *, weight: float = 1.0) -> tuple[int, int, list[str]]:
    """Очки long/short + factors для confluence."""
    if plan is None or plan.confidence < 0.50:
        return 0, 0, []
    w = max(0.5, float(weight))
    long_pts = short_pts = 0
    factors = list(plan.factors_ru)

    if plan.action == "long" and plan.status == "confirmed":
        pts = int(round((4 if plan.confidence >= 0.70 else 3) * w))
        long_pts += pts
    elif plan.action == "short" and plan.status == "confirmed":
        pts = int(round((4 if plan.confidence >= 0.70 else 3) * w))
        short_pts += pts
    elif plan.priority == "long" and plan.status == "forming":
        # слабый приоритет, не вход
        long_pts += int(round(1 * w))
        factors.append("Vataga: внутри △ — WAIT до пробоя ↑")
    elif plan.priority == "short" and plan.status == "forming":
        short_pts += int(round(1 * w))
        factors.append("Vataga: внутри △ — WAIT до пробоя ↓")
    elif plan.priority == "caution":
        factors.append("Vataga: расширяющийся △ — не догонять")

    if plan.action == "cancel":
        factors.append("Vataga: ложный пробой — отмена")

    return long_pts, short_pts, factors
