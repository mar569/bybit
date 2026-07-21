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


def build_most_likely_path(
    *,
    impulse: ElliottImpulse | None,
    abc: ElliottAbc | None,
    triangle: ElliottTriangle | None,
    complex_corr: ElliottComplexCorrection | None,
    fib_targets: list[ElliottFibTarget],
    current: float,
) -> ElliottPathForecast:
    """Наиболее вероятный путь цены по найденным структурам PPT."""
    if current <= 0:
        return ElliottPathForecast(bias="neutral")

    # Приоритет: треугольник E → complex Y/Z → ABC C → импульс волна 5 / после 5
    if triangle and triangle.valid and triangle.breakout_bias in {"long", "short"}:
        e = triangle.points[-1] if triangle.points else None
        entry = e.price if e else current
        bias = triangle.breakout_bias
        # цель ≈ высота треугольника (A→B)
        if len(triangle.points) >= 2:
            height = abs(triangle.points[1].price - triangle.points[0].price)
        else:
            height = current * 0.02
        tp = entry + height if bias == "long" else entry - height
        inv = entry - height * 0.35 if bias == "long" else entry + height * 0.35
        return ElliottPathForecast(
            bias=bias,
            prices=[entry, entry + (tp - entry) * 0.4, tp, inv],
            labels=["entry", "path", "tp1", "invalidation"],
            confidence=7 if triangle.kind == "contracting" else 6,
            reason_ru=triangle.label_ru,
        )

    if complex_corr and complex_corr.valid and complex_corr.resume_bias in {"long", "short"}:
        end = complex_corr.points[-1]
        bias = complex_corr.resume_bias
        span = abs(complex_corr.points[0].price - end.price) or current * 0.015
        tp = end.price + span * 1.2 if bias == "long" else end.price - span * 1.2
        inv = end.price - span * 0.4 if bias == "long" else end.price + span * 0.4
        return ElliottPathForecast(
            bias=bias,
            prices=[end.price, end.price + (tp - end.price) * 0.45, tp, inv],
            labels=["entry", "path", "tp1", "invalidation"],
            confidence=6,
            reason_ru=complex_corr.label_ru,
        )

    if impulse and impulse.points:
        by = {p.label: p for p in impulse.points}
        # формирующаяся 5 — цель Fib
        if impulse.current_wave in {"3", "4", "forming"} or (
            "4" in by and "5" not in by
        ):
            bias = "long" if impulse.direction == "up" else "short"
            base = by["4"].price if "4" in by else current
            tps = [t.price for t in fib_targets[:2]] or [
                base + (abs(by["1"].price - by["0"].price) if "0" in by and "1" in by else current * 0.02)
                * (1 if bias == "long" else -1)
            ]
            tp1 = tps[0]
            inv = by["4"].price if "4" in by else (
                by["2"].price if "2" in by else current * (0.99 if bias == "long" else 1.01)
            )
            return ElliottPathForecast(
                bias=bias,
                prices=[current, current + (tp1 - current) * 0.5, tp1, inv],
                labels=["entry", "path", "tp1", "invalidation"],
                confidence=6 if fib_targets else 4,
                reason_ru=(
                    f"ожидаем волну 5 · {fib_targets[0].label}"
                    if fib_targets
                    else "ожидаем волну 5"
                ),
            )

        # импульс завершён → ABC / разворот
        if impulse.current_wave == "complete" or "5" in by:
            bias = "short" if impulse.direction == "up" else "long"
            p5 = by.get("5") or impulse.points[-1]
            w = abs(p5.price - by["0"].price) if "0" in by else current * 0.03
            # типичная глубина A ≈ 0.5–0.618 × 5
            depth = w * 0.55
            tp = p5.price - depth if bias == "short" else p5.price + depth
            if abc and abc.points:
                last = abc.points[-1]
                entry = last.price
                if abc.phase in {"C", "complete"} and abc.at_aggressive_zone:
                    # конец C → возобновление импульсного направления
                    bias = "long" if impulse.direction == "up" else "short"
                    tp = entry + depth * 0.8 if bias == "long" else entry - depth * 0.8
                    return ElliottPathForecast(
                        bias=bias,
                        prices=[entry, entry + (tp - entry) * 0.4, tp, by.get("4", p5).price],
                        labels=["entry", "path", "tp1", "invalidation"],
                        confidence=7,
                        reason_ru="конец ABC → продолжение тренда",
                    )
            return ElliottPathForecast(
                bias=bias,
                prices=[current, current + (tp - current) * 0.45, tp, p5.price],
                labels=["entry", "path", "tp1", "invalidation"],
                confidence=5,
                reason_ru="после 5 → коррекция ABC",
            )

    return ElliottPathForecast(bias="neutral", reason_ru="")


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
        tri = detect_expanding_triangle(swings, bars)

    complex_corr = detect_double_triple_three(swings, bars, prior_trend=prior)
    alt_ok, alt_note = score_alternation(impulse, bars)
    if impulse is not None and alt_ok:
        impulse.alternating_2_4 = True

    fib_targets = project_wave5_fib_targets(impulse)
    current = bars[-1].close if bars else 0.0
    path = build_most_likely_path(
        impulse=impulse,
        abc=abc,
        triangle=tri,
        complex_corr=complex_corr,
        fib_targets=fib_targets,
        current=current,
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
    if path.reason_ru:
        notes.append(path.reason_ru)

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
        "path": path,
        "alternation_ok": alt_ok,
        "alternation_note": alt_note,
        "extra_draw_points": extra_pts,
        "notes": notes,
    }
