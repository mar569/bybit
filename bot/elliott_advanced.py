"""Расширенная теория Эллиотта (PPT Prechter): треугольники, WXY, Fib-проекции, путь.

Формулы по презентации https://ppt-online.org/574890:
- горизонтальный (сходящийся) треугольник ABCDE 3-3-3-3-3
- расходящийся треугольник
- двойные/тройные тройки W-X-Y / W-X-Y-X-Z
- чередование простая↔сложная (волны 2 и 4)
- Fib-цели волны 5 при растяжении 1 / 3 / 5
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .elliott_wave import (
    ElliottAbc,
    ElliottImpulse,
    ElliottPoint,
    _alternate_swings,
    _leg_size,
    classify_extension,
)

if TYPE_CHECKING:
    from .bybit_klines import KlineBar
    from .ta_analysis import SwingPoint


@dataclass
class ElliottTriangle:
    kind: str  # contracting | expanding
    direction: str  # down | up — направление коррекции
    points: list[ElliottPoint] = field(default_factory=list)  # A..E
    valid: bool = False
    breakout_bias: str = "neutral"  # long|short — куда чаще ломает
    label_ru: str = ""
    # линии клина: lower A–C, upper B–D
    lower_a: ElliottPoint | None = None
    lower_c: ElliottPoint | None = None
    upper_b: ElliottPoint | None = None
    upper_d: ElliottPoint | None = None


@dataclass
class ElliottComplexCorrection:
    kind: str  # double_three | triple_three
    direction: str  # down | up
    points: list[ElliottPoint] = field(default_factory=list)  # W X Y [X2 Z]
    valid: bool = False
    resume_bias: str = "neutral"  # возобновление тренда до коррекции
    label_ru: str = ""


@dataclass
class ElliottFibTarget:
    price: float
    label: str  # w5=1×w1 | w5=1.618×(0→3) | ...
    source: str  # extension_3 | extension_5 | extension_1 | total_0618 | total_0382


@dataclass
class ElliottPathForecast:
    bias: str  # long | short | neutral
    prices: list[float] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    confidence: int = 0
    reason_ru: str = ""
    horizon_hours: float = 2.0
    invalidation: float | None = None
    scenario: str = ""  # abc_correction | wave3 | wave5 | triangle_break | resume_impulse


def _estimate_path_horizon_hours(
    *,
    impulse: ElliottImpulse | None,
    bars: list["KlineBar"] | None,
    current: float,
) -> float:
    """Горизонт пути 1–3ч: крупная глобальная волна → ближе к 3ч, мелкая → ~1ч."""
    hz = 2.0
    if impulse and impulse.points and current > 0:
        prices = [p.price for p in impulse.points]
        span_pct = (max(prices) - min(prices)) / current * 100.0
        if span_pct >= 8.0:
            hz = 3.0
        elif span_pct >= 4.0:
            hz = 2.5
        elif span_pct >= 2.0:
            hz = 2.0
        else:
            hz = 1.25
    # ATR hot → чуть короче
    if bars and len(bars) >= 20 and current > 0:
        recent = bars[-20:]
        atr = sum(b.high - b.low for b in recent) / len(recent)
        atr_pct = atr / current * 100.0
        if atr_pct >= 1.5:
            hz = max(1.0, hz * 0.7)
        elif atr_pct >= 0.9:
            hz = max(1.0, hz * 0.85)
    return max(1.0, min(3.0, hz))


def _zigzag(prices: list[float], labels: list[str]) -> tuple[list[float], list[str]]:
    """Убрать подряд одинаковые точки."""
    out_p: list[float] = []
    out_l: list[str] = []
    for p, lab in zip(prices, labels):
        if out_p and abs(p - out_p[-1]) / max(abs(out_p[-1]), 1e-9) < 0.0008:
            continue
        out_p.append(float(p))
        out_l.append(lab)
    return out_p, out_l


def build_most_likely_path(
    *,
    impulse: ElliottImpulse | None,
    abc: ElliottAbc | None,
    triangle: ElliottTriangle | None,
    complex_corr: ElliottComplexCorrection | None,
    fib_targets: list[ElliottFibTarget],
    current: float,
    bars: list["KlineBar"] | None = None,
    fib_clusters: list | None = None,
) -> ElliottPathForecast:
    """Вероятный путь цены на 1–3ч — зигзаг как «как пойдёт график» по правилам волн.

    Не просто entry→tp, а сценарий: откат A-B-C / дожим W3 / пробой треугольника /
    возобновление импульса после C. Уровни из Fib TradeRev + кластеров.
    """
    if current <= 0:
        return ElliottPathForecast(bias="neutral")

    hz = _estimate_path_horizon_hours(impulse=impulse, bars=bars, current=current)
    cluster_mid = None
    if fib_clusters:
        c0 = fib_clusters[0]
        cluster_mid = getattr(c0, "mid", None) or (
            (getattr(c0, "price_lo", 0) + getattr(c0, "price_hi", 0)) / 2.0
        )

    # --- 1) Треугольник у E → пробой на высоту A-B ---
    if triangle and triangle.valid and triangle.breakout_bias in {"long", "short"}:
        e = triangle.points[-1] if triangle.points else None
        now = e.price if e else current
        bias = triangle.breakout_bias
        height = (
            abs(triangle.points[1].price - triangle.points[0].price)
            if len(triangle.points) >= 2
            else current * 0.02
        )
        sign = 1.0 if bias == "long" else -1.0
        # retest границы → импульс пробоя
        retest = now - sign * height * 0.12
        mid = now + sign * height * 0.55
        tp = now + sign * height
        inv = now - sign * height * 0.35
        prices, labels = _zigzag(
            [current, now, retest, mid, tp],
            ["сейчас", "E", "ретест", "путь", "цель △"],
        )
        return ElliottPathForecast(
            bias=bias,
            prices=prices,
            labels=labels,
            confidence=7 if triangle.kind == "contracting" else 6,
            reason_ru=f"{triangle.label_ru} · горизонт ~{hz:.1f}ч",
            horizon_hours=hz,
            invalidation=inv,
            scenario="triangle_break",
        )

    # --- 2) Сложная коррекция WXY → возобновление тренда ---
    if complex_corr and complex_corr.valid and complex_corr.resume_bias in {"long", "short"}:
        end = complex_corr.points[-1]
        bias = complex_corr.resume_bias
        span = abs(complex_corr.points[0].price - end.price) or current * 0.015
        sign = 1.0 if bias == "long" else -1.0
        p1 = end.price + sign * span * 0.35
        pull = p1 - sign * span * 0.15
        tp = end.price + sign * span * 1.2
        prices, labels = _zigzag(
            [current, end.price, p1, pull, tp],
            ["сейчас", "Y/Z", "i", "ii", "iii"],
        )
        return ElliottPathForecast(
            bias=bias,
            prices=prices,
            labels=labels,
            confidence=6,
            reason_ru=f"{complex_corr.label_ru} · ~{hz:.1f}ч",
            horizon_hours=hz,
            invalidation=end.price - sign * span * 0.4,
            scenario="resume_impulse",
        )

    if impulse and impulse.points:
        by = {p.label: p for p in impulse.points}
        direction = impulse.direction
        sign_imp = 1.0 if direction == "up" else -1.0

        # --- 3) Конец ABC → новый импульс (1-2-3) ---
        if abc and abc.points and abc.phase in {"C", "complete"}:
            last = abc.points[-1]
            depth = abs(last.price - (by.get("5") or impulse.points[-1]).price) or current * 0.02
            # После C — продолжение исходного импульса
            if abc.at_aggressive_zone or abc.phase == "complete":
                bias = "long" if direction == "up" else "short"
                sign = sign_imp
                w1 = last.price + sign * depth * 0.55
                w2 = w1 - sign * depth * 0.22
                w3 = last.price + sign * depth * 1.15
                if cluster_mid and (
                    (bias == "long" and cluster_mid > last.price)
                    or (bias == "short" and cluster_mid < last.price)
                ):
                    w3 = cluster_mid
                prices, labels = _zigzag(
                    [current, last.price, w1, w2, w3],
                    ["сейчас", "C", "1", "2", "3"],
                )
                return ElliottPathForecast(
                    bias=bias,
                    prices=prices,
                    labels=labels,
                    confidence=7,
                    reason_ru=f"конец ABC → импульс · ~{hz:.1f}ч",
                    horizon_hours=hz,
                    invalidation=(by.get("4") or last).price,
                    scenario="resume_impulse",
                )
            # C ещё формируется — добить C, потом разворот
            bias_corr = "short" if direction == "up" else "long"
            sign_c = -sign_imp
            c_tgt = last.price + sign_c * depth * 0.35
            # Fib 61.8 импульса как магнит конца C
            if "0" in by and "5" in by:
                span = by["5"].price - by["0"].price
                fib618 = by["5"].price - span * 0.618
                c_tgt = fib618
            then_w1 = c_tgt - sign_c * depth * 0.5
            prices, labels = _zigzag(
                [current, last.price, c_tgt, c_tgt - sign_c * depth * 0.12, then_w1],
                ["сейчас", "C?", "C fib", "разворот", "1"],
            )
            return ElliottPathForecast(
                bias=bias_corr if abs(c_tgt - current) > abs(then_w1 - current) * 0.4 else (
                    "long" if direction == "up" else "short"
                ),
                prices=prices,
                labels=labels,
                confidence=6,
                reason_ru=f"добить C → разворот · ~{hz:.1f}ч",
                horizon_hours=hz,
                invalidation=by.get("5", last).price,
                scenario="abc_correction",
            )

        # --- 4) Формируется W3 (после W2) ---
        if impulse.current_wave in {"2", "3", "forming"} and "2" in by and "5" not in by:
            bias = "long" if direction == "up" else "short"
            p0, p1, p2 = by.get("0"), by.get("1"), by["2"]
            w1 = abs(p1.price - p0.price) if p0 and p1 else current * 0.02
            # лёгкий дожим/ретест W2 → W3 к FE 100 → FE 161.8
            fe100 = p2.price + sign_imp * w1 * 1.0
            fe161 = p2.price + sign_imp * w1 * 1.618
            mid = p2.price + sign_imp * w1 * 0.55
            retest = p2.price - sign_imp * w1 * 0.08
            tp = fe161 if impulse.extension == "3" or (fib_targets and "1.618" in (fib_targets[0].label or "")) else fe100
            if cluster_mid:
                # ближайший кластер в сторону импульса
                if (sign_imp > 0 and cluster_mid > p2.price) or (sign_imp < 0 and cluster_mid < p2.price):
                    tp = cluster_mid
            prices, labels = _zigzag(
                [current, p2.price, retest, mid, tp],
                ["сейчас", "2", "ретест", "3 mid", "3 FE"],
            )
            return ElliottPathForecast(
                bias=bias,
                prices=prices,
                labels=labels,
                confidence=7 if impulse.fib_w2_ok else 5,
                reason_ru=f"ожидаем волну 3 · FE · ~{hz:.1f}ч",
                horizon_hours=hz,
                invalidation=p0.price if p0 else p2.price - sign_imp * w1 * 0.15,
                scenario="wave3",
            )

        # --- 5) После W4 → волна 5 ---
        if "4" in by and "5" not in by:
            bias = "long" if direction == "up" else "short"
            p4 = by["4"]
            p0, p1 = by.get("0"), by.get("1")
            w1 = abs(p1.price - p0.price) if p0 and p1 else current * 0.015
            tp1 = fib_targets[0].price if fib_targets else (p4.price + sign_imp * w1)
            tp2 = fib_targets[1].price if len(fib_targets) > 1 else (p4.price + sign_imp * w1 * 1.0)
            mid = p4.price + (tp1 - p4.price) * 0.45
            prices, labels = _zigzag(
                [current, p4.price, mid, tp1, tp2],
                ["сейчас", "4", "5 mid", "5 Fib", "5 ext"],
            )
            return ElliottPathForecast(
                bias=bias,
                prices=prices,
                labels=labels,
                confidence=6 if fib_targets else 4,
                reason_ru=f"ожидаем волну 5 · ~{hz:.1f}ч",
                horizon_hours=hz,
                invalidation=p4.price - sign_imp * w1 * 0.25,
                scenario="wave5",
            )

        # --- 6) Импульс завершён → ABC коррекция на 1–3ч ---
        if impulse.current_wave == "complete" or "5" in by:
            bias = "short" if direction == "up" else "long"  # направление коррекции
            p5 = by.get("5") or impulse.points[-1]
            p0 = by.get("0")
            w = abs(p5.price - p0.price) if p0 else current * 0.03
            # A ≈ 50–61.8% от хода / от W5; B ≈ 38–50% A; C ≈ 100% A или Fib 61.8 всего
            a_depth = w * 0.50
            a_px = p5.price - sign_imp * a_depth
            b_px = a_px + sign_imp * a_depth * 0.40
            c_px = p5.price - sign_imp * w * 0.618  # TradeRev глубокая коррекция
            if cluster_mid:
                # если кластер в зоне коррекции — цель C
                if (sign_imp > 0 and cluster_mid < p5.price) or (sign_imp < 0 and cluster_mid > p5.price):
                    c_px = cluster_mid
            prices, labels = _zigzag(
                [current, p5.price, a_px, b_px, c_px],
                ["сейчас", "5", "A", "B", "C 61.8"],
            )
            return ElliottPathForecast(
                bias=bias,
                prices=prices,
                labels=labels,
                confidence=6,
                reason_ru=f"после 5 → ABC к Fib 61.8 · ~{hz:.1f}ч",
                horizon_hours=hz,
                invalidation=p5.price + sign_imp * w * 0.05,
                scenario="abc_correction",
            )

        # --- 7) Ранний импульс (только 0-1) ---
        if "1" in by and "2" not in by:
            bias = "short" if direction == "up" else "long"  # ждём W2
            p0, p1 = by["0"], by["1"]
            w1 = abs(p1.price - p0.price)
            w2_618 = p1.price - sign_imp * w1 * 0.618
            w2_50 = p1.price - sign_imp * w1 * 0.50
            then3 = w2_618 + sign_imp * w1 * 1.0
            prices, labels = _zigzag(
                [current, p1.price, w2_50, w2_618, then3],
                ["сейчас", "1", "2 50%", "2 61.8", "3"],
            )
            return ElliottPathForecast(
                bias=bias,  # сначала коррекция W2
                prices=prices,
                labels=labels,
                confidence=5,
                reason_ru=f"волна 2 к 50–61.8 → потом 3 · ~{hz:.1f}ч",
                horizon_hours=hz,
                invalidation=p0.price,
                scenario="wave3",
            )

    return ElliottPathForecast(bias="neutral", reason_ru="", horizon_hours=hz)


def _pct(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return abs(a - b) / max(a, b) * 100.0


def detect_horizontal_triangle(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    *,
    direction: str | None = None,
) -> ElliottTriangle | None:
    """Горизонтальный (сходящийся) треугольник A-B-C-D-E.

    PPT: пять перекрывающихся волн; границы B–D (верх) и A–C (низ) сходятся.
    Пробой чаще в сторону тренда *до* треугольника.
    """
    if not swings or not bars or len(swings) < 5:
        return None
    alt = _alternate_swings(swings)[-12:]
    if len(alt) < 5:
        return None

    dirs = [direction] if direction in {"up", "down"} else ["down", "up"]
    best: ElliottTriangle | None = None
    best_q = 0

    for d in dirs:
        if d == "down":
            expect = ["low", "high", "low", "high", "low"]
        else:
            expect = ["high", "low", "high", "low", "high"]
        labels = ["A", "B", "C", "D", "E"]

        for start in range(0, len(alt) - 4):
            chunk = alt[start : start + 5]
            if not all(chunk[i].kind == expect[i] for i in range(5)):
                continue
            pts = [
                ElliottPoint(labels[i], chunk[i].index, chunk[i].price)
                for i in range(5)
            ]
            a, b, c, dd, e = pts
            # Схождение: |B-D| и |A-C| — противоположные наклоны, E внутри
            if d == "down":
                # higher lows A < C < E? типично C выше A, E выше C или около
                higher_lows = c.price > a.price * 0.998 and e.price >= min(a.price, c.price) * 0.997
                lower_highs = dd.price < b.price * 1.002
                # E не пробивает сильно A
                e_ok = e.price >= a.price * 0.97
                converging = higher_lows and lower_highs and e_ok
                # верх B→D вниз, низ A→C вверх
                upper_ok = dd.price <= b.price
                lower_ok = c.price >= a.price
            else:
                lower_highs = c.price < a.price * 1.002 and e.price <= max(a.price, c.price) * 1.003
                higher_lows = dd.price > b.price * 0.998
                e_ok = e.price <= a.price * 1.03
                converging = lower_highs and higher_lows and e_ok
                upper_ok = c.price <= a.price
                lower_ok = dd.price >= b.price

            span0 = abs(b.price - a.price)
            span_last = abs(e.price - dd.price)
            shrink = span0 > 0 and span_last < span0 * 0.90

            if not (converging and upper_ok and lower_ok and shrink):
                continue

            q = 55 + (15 if shrink else 0) + (10 if e_ok else 0)
            # breakout = против направления коррекции = тренд до треугольника
            bias = "long" if d == "down" else "short"
            if q > best_q:
                best_q = q
                best = ElliottTriangle(
                    kind="contracting",
                    direction=d,
                    points=pts,
                    valid=True,
                    breakout_bias=bias,
                    label_ru=f"гориз. треугольник ABCDE → {'LONG' if bias == 'long' else 'SHORT'}",
                    lower_a=a if d == "down" else b,
                    lower_c=c if d == "down" else dd,
                    upper_b=b if d == "down" else a,
                    upper_d=dd if d == "down" else c,
                )
                # для up-коррекции линии: upper=A-C, lower=B-D
                if d == "up":
                    best.lower_a, best.lower_c = b, dd
                    best.upper_b, best.upper_d = a, c

    return best if best_q >= 60 else None


def detect_ascending_triangle(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    *,
    direction: str | None = None,
) -> ElliottTriangle | None:
    """Восходящий треугольник по TradeRevolution.

    C не за A, D ≈ B (прямая верхняя линия), E около линии A-C.
    """
    if not swings or not bars or len(swings) < 5:
        return None
    alt = _alternate_swings(swings)[-12:]
    if len(alt) < 5:
        return None

    dirs = [direction] if direction in {"up", "down"} else ["down", "up"]
    best: ElliottTriangle | None = None
    best_q = 0

    for d in dirs:
        if d == "down":
            expect = ["low", "high", "low", "high", "low"]
        else:
            expect = ["high", "low", "high", "low", "high"]
        labels = ["A", "B", "C", "D", "E"]

        for start in range(0, len(alt) - 4):
            chunk = alt[start : start + 5]
            if not all(chunk[i].kind == expect[i] for i in range(5)):
                continue
            pts = [
                ElliottPoint(labels[i], chunk[i].index, chunk[i].price)
                for i in range(5)
            ]
            a, b, c, dd, e = pts
            if d == "down":
                # Ascending: higher lows (C > A), D ≈ B (flat top)
                higher_lows = c.price > a.price * 0.998
                flat_top = abs(dd.price - b.price) / max(abs(b.price), 1e-9) < 0.008
                e_ok = e.price >= a.price * 0.97
                c_not_past_a = True  # C > A for ascending (not past = higher)
            else:
                higher_lows = dd.price > b.price * 0.998
                flat_top = abs(c.price - a.price) / max(abs(a.price), 1e-9) < 0.008
                e_ok = e.price <= a.price * 1.03
                c_not_past_a = True

            if not (higher_lows and flat_top and e_ok):
                continue

            q = 60 + (10 if e_ok else 0)
            bias = "long" if d == "down" else "short"
            if q > best_q:
                best_q = q
                best = ElliottTriangle(
                    kind="ascending",
                    direction=d,
                    points=pts,
                    valid=True,
                    breakout_bias=bias,
                    label_ru=f"восход. треугольник ABCDE → {'LONG' if bias == 'long' else 'SHORT'}",
                    lower_a=a if d == "down" else b,
                    lower_c=c if d == "down" else dd,
                    upper_b=b if d == "down" else a,
                    upper_d=dd if d == "down" else c,
                )

    return best if best_q >= 60 else None


def detect_expanding_triangle(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    *,
    direction: str | None = None,
) -> ElliottTriangle | None:
    """Расходящийся треугольник: хаи растут, лои падают (мегафон).

    PPT: в 4-й волновой позиции 5-я часто стремительная.
    """
    if not swings or not bars or len(swings) < 5:
        return None
    alt = _alternate_swings(swings)[-12:]
    if len(alt) < 5:
        return None

    dirs = [direction] if direction in {"up", "down"} else ["down", "up"]
    best: ElliottTriangle | None = None
    best_q = 0

    for d in dirs:
        if d == "down":
            # A low, B high, C lower, D higher, E lowest
            expect = ["low", "high", "low", "high", "low"]
        else:
            expect = ["high", "low", "high", "low", "high"]
        labels = ["A", "B", "C", "D", "E"]

        for start in range(0, len(alt) - 4):
            chunk = alt[start : start + 5]
            if not all(chunk[i].kind == expect[i] for i in range(5)):
                continue
            pts = [
                ElliottPoint(labels[i], chunk[i].index, chunk[i].price)
                for i in range(5)
            ]
            a, b, c, dd, e = pts
            if d == "down":
                expanding = (
                    c.price < a.price * 0.999
                    and e.price < c.price * 0.999
                    and dd.price > b.price * 1.001
                )
            else:
                expanding = (
                    c.price > a.price * 1.001
                    and e.price > c.price * 1.001
                    and dd.price < b.price * 0.999
                )
            if not expanding:
                continue
            span0 = abs(b.price - a.price)
            span_last = abs(e.price - dd.price)
            grow = span0 > 0 and span_last > span0 * 1.08
            if not grow:
                continue
            q = 60 + 15
            bias = "long" if d == "down" else "short"
            if q > best_q:
                best_q = q
                best = ElliottTriangle(
                    kind="expanding",
                    direction=d,
                    points=pts,
                    valid=True,
                    breakout_bias=bias,
                    label_ru=(
                        f"расход. треугольник ABCDE → "
                        f"{'стремит. 5 / LONG' if bias == 'long' else 'стремит. 5 / SHORT'}"
                    ),
                    lower_a=a if d == "down" else dd,
                    lower_c=c if d == "down" else b,
                    upper_b=b if d == "down" else a,
                    upper_d=dd if d == "down" else c,
                )
    return best if best_q >= 60 else None


def detect_double_triple_three(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    *,
    prior_trend: str | None = None,
) -> ElliottComplexCorrection | None:
    """Двойная (W-X-Y) / тройная (W-X-Y-X-Z) тройка — боковая сложная коррекция.

    Эвристика: 5 или 7 чередующихся свингов примерно одного размаха (flat range).
    После Y/Z — возобновление prior_trend.
    """
    if not swings or not bars or len(swings) < 5:
        return None
    alt = _alternate_swings(swings)[-14:]
    if len(alt) < 5:
        return None

    # Берём последние 5 (W X Y) или 7 (W X Y X Z) свингов
    candidates: list[tuple[str, list]] = []
    if len(alt) >= 7:
        candidates.append(("triple_three", alt[-7:]))
    if len(alt) >= 5:
        candidates.append(("double_three", alt[-5:]))

    best: ElliottComplexCorrection | None = None
    best_q = 0

    for kind, chunk in candidates:
        prices = [s.price for s in chunk]
        mid = (max(prices) + min(prices)) / 2.0
        if mid <= 0:
            continue
        rng = (max(prices) - min(prices)) / mid * 100.0
        # боковик: размах не огромный
        if rng < 0.4 or rng > 12.0:
            continue
        # чередование уже есть; проверяем «тройки» — примерно равные ноги
        legs = [abs(chunk[i + 1].price - chunk[i].price) for i in range(len(chunk) - 1)]
        if not legs or min(legs) <= 0:
            continue
        avg = sum(legs) / len(legs)
        even = all(abs(L - avg) / avg < 0.65 for L in legs)
        if not even:
            continue

        # направление коррекции: от первого к последнему
        if chunk[-1].price < chunk[0].price:
            corr_dir = "down"
        else:
            corr_dir = "up"
        resume = prior_trend if prior_trend in {"long", "short"} else (
            "long" if corr_dir == "down" else "short"
        )

        if kind == "double_three":
            # W X Y — индексы 0,2,4 как «низы/верхи» коррекции; X = 1 или 2
            labs = ["W", "X", "Y"]
            # берём экстремумы: 0→W, 2→X?, упрощённо: 0=W start end, mid peak X, end Y
            # Используем точки 0, 1/2, 4 как W, X, Y для 5 свингов
            pick_idx = [0, 2, 4]
            pts = [
                ElliottPoint(labs[i], chunk[pick_idx[i]].index, chunk[pick_idx[i]].price)
                for i in range(3)
            ]
            # X должен быть откатом против W→Y
            label = "двойная тройка W-X-Y"
        else:
            labs = ["W", "X", "Y", "X2", "Z"]
            pick_idx = [0, 2, 4, 5, 6]
            pts = [
                ElliottPoint(labs[i], chunk[pick_idx[i]].index, chunk[pick_idx[i]].price)
                for i in range(5)
            ]
            label = "тройная тройка W-X-Y-X-Z"

        q = 58 + (12 if kind == "double_three" else 8) + (10 if rng < 6 else 0)
        if q > best_q:
            best_q = q
            best = ElliottComplexCorrection(
                kind=kind,
                direction=corr_dir,
                points=pts,
                valid=True,
                resume_bias=resume,
                label_ru=f"{label} → {'LONG' if resume == 'long' else 'SHORT'}",
            )

    return best if best_q >= 58 else None


def score_alternation(impulse: ElliottImpulse | None, bars: list["KlineBar"]) -> tuple[bool, str]:
    """Правило чередования: если 2 простая → 4 сложная (и наоборот)."""
    if impulse is None or not bars:
        return False, ""
    by = {p.label: p for p in impulse.points}
    if not all(k in by for k in ("1", "2", "3", "4")):
        return False, ""
    from .elliott_wave import _is_sharp_correction

    sharp2 = _is_sharp_correction(by["1"], by["2"], bars)
    sharp4 = _is_sharp_correction(by["3"], by["4"], bars)
    # длительность
    bars2 = max(1, by["2"].index - by["1"].index)
    bars4 = max(1, by["4"].index - by["3"].index)
    simple2 = sharp2 or bars2 <= max(6, bars4 // 2)
    simple4 = sharp4 or bars4 <= max(6, bars2 // 2)
    ok = simple2 != simple4
    if ok:
        note = (
            "чередование: 2 простая → 4 сложная"
            if simple2 and not simple4
            else "чередование: 2 сложная → 4 простая"
        )
        return True, note
    return False, "чередование 2/4 слабое"


def project_wave5_fib_targets(impulse: ElliottImpulse | None) -> list[ElliottFibTarget]:
    """Fib-цели конца волны 5 по слайдам «соотношения движущих волн».

    Растяжение 3:  w5 ≈ 1.00 × w1 от конца 4
    Растяжение 5:  w5 ≈ 1.618 × (0→3) от конца 4
    Растяжение 1:  (3→5) ≈ 0.618 × w1 от конца 2
    Общие: total 0→5: доля 0→3 = 0.618 → цель; доля 0→4 = 0.382 → цель
    """
    if impulse is None:
        return []
    by = {p.label: p for p in impulse.points}
    need = ("0", "1", "2", "3")
    if not all(k in by for k in need):
        return []
    p0, p1, p2, p3 = by["0"], by["1"], by["2"], by["3"]
    p4 = by.get("4")
    direction = impulse.direction
    sign = 1.0 if direction == "up" else -1.0
    w1 = _leg_size(p0, p1)
    if w1 <= 0:
        return []

    ext = impulse.extension or classify_extension(impulse.points)
    out: list[ElliottFibTarget] = []

    def _add(price: float, label: str, source: str) -> None:
        if price > 0:
            out.append(ElliottFibTarget(price=price, label=label, source=source))

    # Цель: 0→3 = 0.618 всего хода 0→5  =>  total = (p3-p0)/0.618
    span03 = abs(p3.price - p0.price)
    if span03 > 0:
        total = span03 / 0.618
        t5 = p0.price + sign * total
        _add(t5, "w5 · 0→3=61.8% хода", "total_0618")

    if p4 is not None:
        # Растяжение 3: w5 = 1.00 × w1 от p4
        _add(p4.price + sign * w1, "w5=1.00×w1", "extension_3")
        # Растяжение 5: w5 = 1.618 × (0→3) от p4
        _add(p4.price + sign * 1.618 * span03, "w5=1.618×(0→3)", "extension_5")
        # 0→4 = 0.382 всего => total = span04/0.382
        span04 = abs(p4.price - p0.price)
        if span04 > 0:
            total2 = span04 / 0.382
            _add(p0.price + sign * total2, "w5 · 0→4=38.2% хода", "total_0382")

    # Растяжение 1: 3→5 = 0.618 × w1 от p2 (конец 2 = старт 3)
    _add(p2.price + sign * 0.618 * w1, "3→5=0.618×w1", "extension_1")

    # Приоритет по типу растяжения — первые 2–3 цели
    priority = {
        "3": ("extension_3", "total_0618", "extension_5"),
        "5": ("extension_5", "total_0382", "extension_3"),
        "1": ("extension_1", "total_0618", "extension_3"),
        "": ("extension_3", "total_0618", "total_0382"),
    }.get(ext, ("extension_3", "total_0618", "total_0382"))

    ranked: list[ElliottFibTarget] = []
    seen: set[str] = set()
    for src in priority:
        for t in out:
            if t.source == src and t.source not in seen:
                ranked.append(t)
                seen.add(t.source)
    for t in out:
        if t.source not in seen:
            ranked.append(t)
            seen.add(t.source)
    return ranked[:4]


@dataclass
class FibCluster:
    """Кластер Fib: зона где сходятся 2-3 уровня от разных измерений."""
    price_lo: float
    price_hi: float
    levels: list[str] = field(default_factory=list)  # descriptions
    strength: int = 0  # how many levels converge

    @property
    def mid(self) -> float:
        return (self.price_lo + self.price_hi) / 2.0


def build_fib_clusters(
    impulse: ElliottImpulse | None,
    abc: "ElliottAbc | None" = None,
    *,
    tolerance_pct: float = 0.6,
) -> list[FibCluster]:
    """Build Fib clusters from grid + extension measurements per TradeRevolution.

    Кластер = зона, где сходятся уровни от различных измерений (сетка + расширение).
    Наиболее вероятный кластер — из наиболее вероятных значений.
    """
    if impulse is None:
        return []
    by = {p.label: p for p in impulse.points}
    if not all(k in by for k in ("0", "1", "2")):
        return []

    levels: list[tuple[float, str]] = []  # (price, description)
    p0, p1, p2 = by["0"], by["1"], by["2"]
    w1 = _leg_size(p0, p1)
    sign = 1.0 if impulse.direction == "up" else -1.0

    # Grid levels for W3 target (from W2 base)
    for fib, label in ((1.618, "W3 сетка 161.8%"), (2.0, "W3 сетка 200%"), (2.618, "W3 сетка 261.8%")):
        levels.append((p2.price + sign * w1 * fib, label))

    if "3" in by:
        p3 = by["3"]
        w3 = _leg_size(p2, p3)
        # W4 retracement targets
        for fib, label in ((0.382, "W4 сетка 38.2%"), (0.50, "W4 сетка 50%")):
            levels.append((p3.price - sign * w3 * fib, label))

        if "4" in by:
            p4 = by["4"]
            # W5 extension targets
            for fib, label in (
                (0.618, "W5 расш. 61.8%"), (0.786, "W5 расш. 78.6%"),
                (1.0, "W5 расш. 100%"), (1.618, "W5 расш. 161.8%"),
            ):
                levels.append((p4.price + sign * w1 * fib, label))

            # W5 from W3: extension ratios
            span03 = abs(p3.price - p0.price)
            if span03 > 0:
                total = span03 / 0.618
                levels.append((p0.price + sign * total, "W5 total 61.8%"))

    if not levels:
        return []

    # Cluster: group nearby levels
    levels.sort(key=lambda x: x[0])
    clusters: list[FibCluster] = []
    used = [False] * len(levels)
    for i, (price_i, label_i) in enumerate(levels):
        if used[i]:
            continue
        group = [(price_i, label_i)]
        used[i] = True
        for j in range(i + 1, len(levels)):
            if used[j]:
                continue
            if abs(levels[j][0] - price_i) / max(abs(price_i), 1e-9) * 100 <= tolerance_pct:
                group.append(levels[j])
                used[j] = True
        if len(group) >= 2:
            prices = [g[0] for g in group]
            clusters.append(FibCluster(
                price_lo=min(prices),
                price_hi=max(prices),
                levels=[g[1] for g in group],
                strength=len(group),
            ))

    clusters.sort(key=lambda c: c.strength, reverse=True)
    return clusters[:3]


def analyze_elliott_advanced(
    bars: list["KlineBar"],
    swings: list["SwingPoint"],
    impulse: ElliottImpulse | None,
    abc: ElliottAbc | None,
) -> dict:
    """Пакет PPT-структур + Fib-цели + most-likely path."""
    prior = None
    if impulse:
        prior = "long" if impulse.direction == "up" else "short"

    tri = detect_horizontal_triangle(swings, bars)
    if tri is None:
        tri = detect_ascending_triangle(swings, bars)
    if tri is None:
        tri = detect_expanding_triangle(swings, bars)

    complex_corr = detect_double_triple_three(swings, bars, prior_trend=prior)
    alt_ok, alt_note = score_alternation(impulse, bars)
    if impulse is not None and alt_ok:
        impulse.alternating_2_4 = True

    fib_targets = project_wave5_fib_targets(impulse)
    fib_clusters = build_fib_clusters(impulse, abc)
    current = bars[-1].close if bars else 0.0
    path = build_most_likely_path(
        impulse=impulse,
        abc=abc,
        triangle=tri,
        complex_corr=complex_corr,
        fib_targets=fib_targets,
        current=current,
        bars=bars,
        fib_clusters=fib_clusters,
    )

    notes: list[str] = []
    if tri:
        notes.append(tri.label_ru)
    if complex_corr:
        notes.append(complex_corr.label_ru)
    if alt_note:
        notes.append(alt_note)
    if fib_targets:
        notes.append(f"Fib цель 5: {fib_targets[0].label} @ {fib_targets[0].price:.6g}")
    if fib_clusters:
        c = fib_clusters[0]
        notes.append(f"Fib кластер ({c.strength} уровней) @ {c.mid:.6g}")
    if path.reason_ru:
        notes.append(path.reason_ru)
    elif path.scenario:
        notes.append(f"путь {path.scenario} ~{path.horizon_hours:.1f}ч")

    # draw extras
    extra_pts: list[ElliottPoint] = []
    if tri and tri.points:
        extra_pts.extend(tri.points)
    elif complex_corr and complex_corr.points:
        extra_pts.extend(complex_corr.points)

    return {
        "triangle": tri,
        "complex_corr": complex_corr,
        "fib_targets": fib_targets,
        "fib_clusters": fib_clusters,
        "path": path,
        "alternation_ok": alt_ok,
        "alternation_note": alt_note,
        "extra_draw_points": extra_pts,
        "notes": notes,
    }
