"""Полные волны Эллиотта (1–5 + ABC) — практические правила.

Основано на классике + практическом гайде (BTC/USD):
https://tradingsworld.com/base/prakticheskoe-rukovodstvo-po-volnam-elliotta-na-primere-btc-usd/

Чек-лист импульса:
- волна 2 не заходит за основание волны 1
- волна 3 самая длинная из 1/3/5 и превышает вершину волны 1
- волны 1 и 5 примерно равны
- волна 4 не пересекает территорию волны 1
- волна B не обновляет основание волны A
- ABC не обновляет основание 5-волновки

Fib-пропорции (ориентиры):
  2 = 0.382–0.618 × 1;  3 = 1.618–2.618 × 1;
  4 = 0.382–0.50 × 3;   5 = 0.382–0.618 × 3;
  A ≈ 0.50–0.618 × 5;   B ≈ 0.382–0.50 × A;
  C часто @ 1.272 / 1.618 × B (агрессивный вход)

Входы:
  conservative — обновление хая/лоя волны 1 после ABC (волна 2/4)
  aggressive — лимит у 1.272/1.618 волны B, стоп 3×ATR, TP 3×ATR (R:R ≈ 1:3)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bybit_klines import KlineBar
    from .ta_analysis import SwingPoint

# --- пороги ---
MIN_SWINGS_FOR_IMPULSE = 6  # точки 0..5
MIN_WAVE_PCT = 0.45         # мин. размер одной волны, %
EQUAL_WAVES_TOL = 0.45      # |1−5|/max ≤ 45% ≈ «примерно равны»
MIN_IMPULSE_QUALITY = 55
RECENT_SWING_WINDOW = 24
ATR_STOP_MULT = 3.0
ATR_TP_MULT = 3.0
C_EXT_SOFT = 1.272
C_EXT_HARD = 1.618


@dataclass(frozen=True)
class ElliottPoint:
    label: str  # 0|1|2|3|4|5|A|B|C
    index: int
    price: float


@dataclass
class ElliottImpulse:
    direction: str  # up | down
    points: list[ElliottPoint] = field(default_factory=list)
    current_wave: str = ""  # 1..5 | complete | forming
    valid: bool = False
    quality: int = 0
    violations: list[str] = field(default_factory=list)
    fib_notes: list[str] = field(default_factory=list)
    wave3_longest: bool = False
    alternating_2_4: bool = False  # чередование резкой/боковой (эвристика)

    def point(self, label: str) -> ElliottPoint | None:
        for p in self.points:
            if p.label == label:
                return p
        return None

    @property
    def labels(self) -> list[str]:
        return [p.label for p in self.points]


@dataclass
class ElliottAbc:
    direction: str  # направление коррекции (против импульса)
    points: list[ElliottPoint] = field(default_factory=list)
    phase: str = ""  # A | B | C | complete | forming
    valid: bool = False
    b_retrace: float = 0.0
    c_ext_of_b: float = 0.0
    at_aggressive_zone: bool = False
    label_ru: str = ""


@dataclass
class ElliottEntryPlan:
    mode: str  # conservative | aggressive | wait
    side: str  # long | short
    entry_price: float | None = None
    stop_price: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    trigger: str = ""
    rr: float = 0.0
    ready: bool = False  # цена уже у зоны / триггер сработал


@dataclass
class ElliottWaveResult:
    impulse: ElliottImpulse | None = None
    abc: ElliottAbc | None = None
    label_ru: str = ""
    phase: str = ""  # impulse_1..5 | impulse_complete | abc_A/B/C | abc_complete | unknown
    entry_plan: ElliottEntryPlan | None = None
    draw_points: list[ElliottPoint] = field(default_factory=list)
    confidence: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def has_structure(self) -> bool:
        return self.impulse is not None and len(self.impulse.points) >= 3


def _atr(bars: list["KlineBar"], *, period: int = 14) -> float:
    if len(bars) < 3:
        return 0.0
    n = min(period, len(bars) - 1)
    trs: list[float] = []
    for i in range(len(bars) - n, len(bars)):
        if i <= 0:
            continue
        b, p = bars[i], bars[i - 1]
        tr = max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _pct(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return abs(a - b) / max(a, b) * 100.0


def _alternate_swings(swings: list["SwingPoint"]) -> list["SwingPoint"]:
    if not swings:
        return []
    out = [swings[0]]
    for s in swings[1:]:
        if s.kind == out[-1].kind:
            if s.kind == "high" and s.price >= out[-1].price:
                out[-1] = s
            elif s.kind == "low" and s.price <= out[-1].price:
                out[-1] = s
            continue
        out.append(s)
    return out


def _leg_size(p0: ElliottPoint, p1: ElliottPoint) -> float:
    return abs(p1.price - p0.price)


def _is_sharp_correction(p_start: ElliottPoint, p_end: ElliottPoint, bars: list["KlineBar"]) -> bool:
    """Резкая коррекция ≈ короткий путь по барам + большой %."""
    bars_n = max(1, p_end.index - p_start.index)
    size_pct = _pct(p_start.price, p_end.price)
    return bars_n <= 8 and size_pct >= 1.2


def _validate_impulse_rules(
    pts: list[ElliottPoint],
    direction: str,
) -> tuple[list[str], bool]:
    """Классический чек-лист. pts = [0,1,2,3,4,5] или префикс."""
    violations: list[str] = []
    by = {p.label: p for p in pts}
    need = ["0", "1", "2"]
    if not all(k in by for k in need):
        return ["мало точек"], False

    p0, p1, p2 = by["0"], by["1"], by["2"]
    if direction == "up":
        if p1.price <= p0.price:
            violations.append("волна 1 не вверх")
        if p2.price <= p0.price:
            violations.append("волна 2 зашла за основание 1")
        if p2.price >= p1.price:
            violations.append("волна 2 не откат")
    else:
        if p1.price >= p0.price:
            violations.append("волна 1 не вниз")
        if p2.price >= p0.price:
            violations.append("волна 2 зашла за основание 1")
        if p2.price <= p1.price:
            violations.append("волна 2 не откат")

    if "3" in by:
        p3 = by["3"]
        w1 = _leg_size(p0, p1)
        w3 = _leg_size(p2, p3)
        if direction == "up":
            if p3.price <= p1.price:
                violations.append("волна 3 не превысила вершину 1")
        else:
            if p3.price >= p1.price:
                violations.append("волна 3 не превысила дно 1")
        if w3 + 1e-12 < w1 * 0.95:
            # мягко: 3 должна быть самой длинной — проверим когда есть 5
            pass

    if "4" in by and "1" in by and "3" in by:
        p4 = by["4"]
        p1 = by["1"]
        # волна 4 не пересекает волну 1 (классика: не заходит в зону волны 1)
        if direction == "up":
            if p4.price <= p1.price:
                violations.append("волна 4 пересекла волну 1")
            if p4.price >= by["3"].price:
                violations.append("волна 4 не откат")
        else:
            if p4.price >= p1.price:
                violations.append("волна 4 пересекла волну 1")
            if p4.price <= by["3"].price:
                violations.append("волна 4 не откат")

    if "5" in by and "3" in by and "4" in by:
        p5 = by["5"]
        p0, p1, p3, p4 = by["0"], by["1"], by["3"], by["4"]
        w1 = _leg_size(p0, p1)
        w3 = _leg_size(by["2"], p3)
        w5 = _leg_size(p4, p5)
        if direction == "up" and p5.price <= p3.price:
            violations.append("волна 5 не обновила хай 3")
        if direction == "down" and p5.price >= p3.price:
            violations.append("волна 5 не обновила лой 3")
        # 3 — самая длинная
        if w3 < w1 * 0.98 or w3 < w5 * 0.98:
            violations.append("волна 3 не самая длинная")
        # 1 ≈ 5
        if max(w1, w5) > 0 and abs(w1 - w5) / max(w1, w5) > EQUAL_WAVES_TOL:
            # мягкое нарушение — не фатал, только note
            pass

    # фатальные
    fatal = [
        v for v in violations
        if any(
            k in v
            for k in (
                "зашла за основание",
                "пересекла волну 1",
                "не превысила",
                "не вверх",
                "не вниз",
            )
        )
    ]
    return violations, len(fatal) == 0


def _fib_proportion_notes(pts: list[ElliottPoint], direction: str) -> list[str]:
    by = {p.label: p for p in pts}
    notes: list[str] = []
    if not all(k in by for k in ("0", "1", "2")):
        return notes
    w1 = _leg_size(by["0"], by["1"])
    if w1 <= 0:
        return notes
    w2 = _leg_size(by["1"], by["2"])
    r2 = w2 / w1
    if 0.30 <= r2 <= 0.70:
        notes.append(f"2≈{r2:.0%}×1 (Fib ok)")
    elif r2 > 0:
        notes.append(f"2≈{r2:.0%}×1")

    if "3" in by:
        w3 = _leg_size(by["2"], by["3"])
        r3 = w3 / w1
        if 1.40 <= r3 <= 2.80:
            notes.append(f"3≈{r3:.2f}×1 (Fib ok)")
        else:
            notes.append(f"3≈{r3:.2f}×1")
    if "3" in by and "4" in by:
        w3 = _leg_size(by["2"], by["3"])
        w4 = _leg_size(by["3"], by["4"])
        if w3 > 0:
            r4 = w4 / w3
            if 0.30 <= r4 <= 0.55:
                notes.append(f"4≈{r4:.0%}×3 (Fib ok)")
    if "3" in by and "4" in by and "5" in by:
        w3 = _leg_size(by["2"], by["3"])
        w5 = _leg_size(by["4"], by["5"])
        if w3 > 0:
            r5 = w5 / w3
            if 0.30 <= r5 <= 0.70:
                notes.append(f"5≈{r5:.0%}×3 (Fib ok)")
    _ = direction
    return notes


def _score_impulse(
    pts: list[ElliottPoint],
    direction: str,
    violations: list[str],
    fib_notes: list[str],
    *,
    bars: list["KlineBar"],
) -> int:
    score = 40
    n = len(pts)
    score += min(25, (n - 2) * 5)
    fib_ok = sum(1 for x in fib_notes if "Fib ok" in x)
    score += fib_ok * 8
    score -= len(violations) * 12
    # мин. размер волн
    for i in range(1, len(pts)):
        if _pct(pts[i - 1].price, pts[i].price) < MIN_WAVE_PCT * 0.5:
            score -= 5
    # чередование 2/4
    by = {p.label: p for p in pts}
    if all(k in by for k in ("1", "2", "3", "4")):
        sharp2 = _is_sharp_correction(by["1"], by["2"], bars)
        sharp4 = _is_sharp_correction(by["3"], by["4"], bars)
        if sharp2 != sharp4:
            score += 8
    return max(0, min(100, score))


def _label_prefix(
    swings_chunk: list["SwingPoint"],
    direction: str,
) -> list[ElliottPoint] | None:
    """Пометить префикс 0..k из чередующихся свингов."""
    if len(swings_chunk) < 3:
        return None
    if direction == "up":
        expect0 = "low"
    else:
        expect0 = "high"
    if swings_chunk[0].kind != expect0:
        return None
    labels = ["0", "1", "2", "3", "4", "5"]
    expect = (
        ["low", "high", "low", "high", "low", "high"]
        if direction == "up"
        else ["high", "low", "high", "low", "high", "low"]
    )
    pts: list[ElliottPoint] = []
    for i, s in enumerate(swings_chunk[:6]):
        if s.kind != expect[i]:
            break
        if i > 0 and _pct(swings_chunk[i - 1].price, s.price) < MIN_WAVE_PCT * 0.3:
            break
        pts.append(ElliottPoint(labels[i], s.index, s.price))
    return pts if len(pts) >= 3 else None


def detect_elliott_impulse(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
) -> ElliottImpulse | None:
    """Ищем лучшую 5-волновку (или формирующуюся) в недавних свингах."""
    if not swings or not bars or len(swings) < 3:
        return None
    alt = _alternate_swings(swings)
    if len(alt) < 3:
        return None
    recent = alt[-RECENT_SWING_WINDOW:]
    candidates: list[ElliottImpulse] = []

    for direction in ("up", "down"):
        # полные 6 точек
        for start in range(0, max(1, len(recent) - 5)):
            chunk = recent[start : start + 6]
            if len(chunk) < 6:
                continue
            pts = _label_prefix(chunk, direction)
            if not pts or len(pts) < 6:
                continue
            violations, ok = _validate_impulse_rules(pts, direction)
            fib_notes = _fib_proportion_notes(pts, direction)
            q = _score_impulse(pts, direction, violations, fib_notes, bars=bars)
            if not ok and q < MIN_IMPULSE_QUALITY:
                continue
            by = {p.label: p for p in pts}
            w1 = _leg_size(by["0"], by["1"])
            w3 = _leg_size(by["2"], by["3"])
            w5 = _leg_size(by["4"], by["5"])
            wave3_longest = w3 >= w1 * 0.98 and w3 >= w5 * 0.98
            alt_24 = False
            if all(k in by for k in ("1", "2", "3", "4")):
                alt_24 = _is_sharp_correction(by["1"], by["2"], bars) != _is_sharp_correction(
                    by["3"], by["4"], bars
                )
            candidates.append(
                ElliottImpulse(
                    direction=direction,
                    points=pts,
                    current_wave="complete",
                    valid=ok and q >= MIN_IMPULSE_QUALITY,
                    quality=q,
                    violations=violations[:4],
                    fib_notes=fib_notes[:4],
                    wave3_longest=wave3_longest,
                    alternating_2_4=alt_24,
                )
            )

        # частичные 3–5 точек (формирование)
        for n in (5, 4, 3):
            if len(recent) < n:
                continue
            for start in range(max(0, len(recent) - n - 4), max(1, len(recent) - n + 1)):
                chunk = recent[start : start + n]
                pts = _label_prefix(chunk, direction)
                if not pts or len(pts) < 3:
                    continue
                # не дублировать полные
                if len(pts) >= 6:
                    continue
                violations, ok = _validate_impulse_rules(pts, direction)
                fib_notes = _fib_proportion_notes(pts, direction)
                q = _score_impulse(pts, direction, violations, fib_notes, bars=bars)
                q = min(q, 72)  # частичные ниже потолка
                if q < 48 and not ok:
                    continue
                last_lbl = pts[-1].label
                candidates.append(
                    ElliottImpulse(
                        direction=direction,
                        points=pts,
                        current_wave=last_lbl if last_lbl != "5" else "complete",
                        valid=ok and q >= 52,
                        quality=q,
                        violations=violations[:3],
                        fib_notes=fib_notes[:3],
                    )
                )

    if not candidates:
        return None
    # предпочитаем валидные полные, затем по quality и свежести (макс index)
    candidates.sort(
        key=lambda c: (
            int(c.valid),
            int(c.current_wave == "complete"),
            c.quality,
            c.points[-1].index if c.points else 0,
        ),
        reverse=True,
    )
    return candidates[0]


def detect_elliott_abc(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    impulse: ElliottImpulse,
) -> ElliottAbc | None:
    """ABC после импульса (после точки 5 или текущего конца импульса)."""
    if not impulse.points or not bars:
        return None
    end = impulse.points[-1]
    # коррекция против импульса
    corr_dir = "down" if impulse.direction == "up" else "up"
    alt = _alternate_swings(swings)
    post = [s for s in alt if s.index >= end.index]
    current = bars[-1].close
    if current <= 0:
        return None

    origin = end.price  # основание для правила «ABC не обновляет 5»
    points: list[ElliottPoint] = [ElliottPoint("5", end.index, end.price)]

    if impulse.direction == "up":
        # A=low, B=high, C=low
        lows = [s for s in post if s.kind == "low" and s.index > end.index]
        highs = [s for s in post if s.kind == "high" and s.index > end.index]
        if not lows:
            if current < origin * 0.997:
                return ElliottAbc(
                    direction=corr_dir,
                    points=points + [ElliottPoint("A", len(bars) - 1, current)],
                    phase="forming",
                    label_ru="формируется волна A",
                )
            return None
        a = lows[0]
        points.append(ElliottPoint("A", a.index, a.price))
        # B не обновляет основание A (для up-корр. B < A? нет: коррекция вниз, B — отскок, не выше 5)
        if not highs:
            phase = "A"
            return ElliottAbc(
                direction=corr_dir,
                points=points,
                phase=phase,
                valid=a.price > (impulse.point("0").price if impulse.point("0") else 0),
                label_ru="волна A коррекции",
            )
        b = highs[0]
        if b.index <= a.index:
            return None
        # B не должна обновлять хай волны 5 сильно вверх — ок; правило: B не обновляет основание A
        # (для медвежьей ABC: B не ниже A) → здесь A низ, B высокий: B > A всегда
        a_leg = origin - a.price
        if a_leg <= 0:
            return None
        b_ret = (b.price - a.price) / a_leg
        points.append(ElliottPoint("B", b.index, b.price))
        c_lows = [s for s in lows if s.index > b.index]
        if c_lows:
            c = c_lows[0]
            points.append(ElliottPoint("C", c.index, c.price))
            b_leg = b.price - a.price
            c_ext = (b.price - c.price) / b_leg if b_leg > 0 else 0.0
            # ABC не обновляет основание 5-волновки (точку 0)
            p0 = impulse.point("0")
            valid = True
            if p0 and c.price < p0.price:
                valid = False
            if b.price < a.price:  # B обновила основание A
                valid = False
            at_agg = C_EXT_SOFT * 0.92 <= c_ext <= C_EXT_HARD * 1.08
            return ElliottAbc(
                direction=corr_dir,
                points=points,
                phase="complete",
                valid=valid,
                b_retrace=b_ret,
                c_ext_of_b=c_ext,
                at_aggressive_zone=at_agg,
                label_ru=f"ABC завершена · C @ {c_ext:.2f}×B",
            )
        # волна C в процессе
        b_leg = b.price - a.price
        c_ext = (b.price - current) / b_leg if b_leg > 0 and current < b.price else 0.0
        at_agg = C_EXT_SOFT * 0.90 <= c_ext <= C_EXT_HARD * 1.12
        phase = "C" if current < b.price * 0.998 else "B"
        return ElliottAbc(
            direction=corr_dir,
            points=points + ([ElliottPoint("C", len(bars) - 1, current)] if phase == "C" else []),
            phase=phase,
            valid=True,
            b_retrace=b_ret,
            c_ext_of_b=c_ext,
            at_aggressive_zone=at_agg,
            label_ru=(
                f"волна C · зона агрессивного входа @ {c_ext:.2f}×B"
                if phase == "C" and at_agg
                else (f"волна C коррекции · B@{b_ret:.0%}A" if phase == "C" else f"волна B · откат {b_ret:.0%}A")
            ),
        )

    # impulse down → коррекция вверх
    highs = [s for s in post if s.kind == "high" and s.index > end.index]
    lows = [s for s in post if s.kind == "low" and s.index > end.index]
    if not highs:
        if current > origin * 1.003:
            return ElliottAbc(
                direction=corr_dir,
                points=points + [ElliottPoint("A", len(bars) - 1, current)],
                phase="forming",
                label_ru="формируется волна A",
            )
        return None
    a = highs[0]
    points.append(ElliottPoint("A", a.index, a.price))
    if not lows:
        return ElliottAbc(
            direction=corr_dir,
            points=points,
            phase="A",
            valid=True,
            label_ru="волна A коррекции",
        )
    b = lows[0]
    if b.index <= a.index:
        return None
    a_leg = a.price - origin
    if a_leg <= 0:
        return None
    b_ret = (a.price - b.price) / a_leg
    points.append(ElliottPoint("B", b.index, b.price))
    c_highs = [s for s in highs if s.index > b.index]
    if c_highs:
        c = c_highs[0]
        points.append(ElliottPoint("C", c.index, c.price))
        b_leg = a.price - b.price
        c_ext = (c.price - b.price) / b_leg if b_leg > 0 else 0.0
        p0 = impulse.point("0")
        valid = True
        if p0 and c.price > p0.price:
            valid = False
        if b.price > a.price:
            valid = False
        at_agg = C_EXT_SOFT * 0.92 <= c_ext <= C_EXT_HARD * 1.08
        return ElliottAbc(
            direction=corr_dir,
            points=points,
            phase="complete",
            valid=valid,
            b_retrace=b_ret,
            c_ext_of_b=c_ext,
            at_aggressive_zone=at_agg,
            label_ru=f"ABC завершена · C @ {c_ext:.2f}×B",
        )
    b_leg = a.price - b.price
    c_ext = (current - b.price) / b_leg if b_leg > 0 and current > b.price else 0.0
    at_agg = C_EXT_SOFT * 0.90 <= c_ext <= C_EXT_HARD * 1.12
    phase = "C" if current > b.price * 1.002 else "B"
    return ElliottAbc(
        direction=corr_dir,
        points=points + ([ElliottPoint("C", len(bars) - 1, current)] if phase == "C" else []),
        phase=phase,
        valid=True,
        b_retrace=b_ret,
        c_ext_of_b=c_ext,
        at_aggressive_zone=at_agg,
        label_ru=(
            f"волна C · зона агрессивного входа @ {c_ext:.2f}×B"
            if phase == "C" and at_agg
            else (f"волна C коррекции · B@{b_ret:.0%}A" if phase == "C" else f"волна B · откат {b_ret:.0%}A")
        ),
    )


def build_elliott_entry_plan(
    impulse: ElliottImpulse,
    abc: ElliottAbc | None,
    bars: list["KlineBar"],
    *,
    current: float | None = None,
) -> ElliottEntryPlan | None:
    """Консервативный и агрессивный вход по гайду."""
    if not impulse.points or not bars:
        return None
    px = current if current and current > 0 else bars[-1].close
    atr = _atr(bars)
    side = "long" if impulse.direction == "up" else "short"
    p1 = impulse.point("1")
    p0 = impulse.point("0")
    p2 = impulse.point("2")
    p3 = impulse.point("3")
    p4 = impulse.point("4")

    # --- Агрессивный: зона C @ 1.272 / 1.618 × B ---
    if abc and abc.phase in {"C", "complete"} and len(abc.points) >= 3:
        by = {p.label: p for p in abc.points}
        if "A" in by and "B" in by:
            a, b = by["A"], by["B"]
            b_leg = abs(b.price - a.price)
            if b_leg > 0:
                if impulse.direction == "up":
                    # коррекция вниз: C ниже B
                    e127 = b.price - b_leg * C_EXT_SOFT
                    e161 = b.price - b_leg * C_EXT_HARD
                    entry = e127 if abs(px - e127) <= abs(px - e161) else e161
                    stop = entry - ATR_STOP_MULT * atr if atr > 0 else (p0.price if p0 else entry * 0.985)
                    tp1 = entry + ATR_TP_MULT * atr if atr > 0 else (p1.price if p1 else entry * 1.03)
                    # цель 3-й волны после ABC ≈ продолжение импульса
                    if p3:
                        tp2 = p3.price
                    elif p1:
                        tp2 = p1.price + abs(p1.price - (p0.price if p0 else p1.price)) * 1.618
                    else:
                        tp2 = tp1
                    near = abs(px - entry) / px * 100.0 <= 0.55 or (
                        abc.at_aggressive_zone and abc.phase == "C"
                    )
                    risk = abs(entry - stop)
                    reward = abs(tp1 - entry)
                    return ElliottEntryPlan(
                        mode="aggressive",
                        side=side,
                        entry_price=entry,
                        stop_price=stop,
                        tp1=tp1,
                        tp2=tp2,
                        trigger=f"лимит @ Fib C {C_EXT_SOFT:.3f}/{C_EXT_HARD:.3f}×B · стоп {ATR_STOP_MULT:.0f}×ATR",
                        rr=(reward / risk) if risk > 0 else 0.0,
                        ready=near and px <= entry * 1.004,
                    )
                else:
                    e127 = b.price + b_leg * C_EXT_SOFT
                    e161 = b.price + b_leg * C_EXT_HARD
                    entry = e127 if abs(px - e127) <= abs(px - e161) else e161
                    stop = entry + ATR_STOP_MULT * atr if atr > 0 else (p0.price if p0 else entry * 1.015)
                    tp1 = entry - ATR_TP_MULT * atr if atr > 0 else (p1.price if p1 else entry * 0.97)
                    if p3:
                        tp2 = p3.price
                    elif p1:
                        tp2 = p1.price - abs((p0.price if p0 else p1.price) - p1.price) * 1.618
                    else:
                        tp2 = tp1
                    near = abs(px - entry) / px * 100.0 <= 0.55 or (
                        abc.at_aggressive_zone and abc.phase == "C"
                    )
                    risk = abs(stop - entry)
                    reward = abs(entry - tp1)
                    return ElliottEntryPlan(
                        mode="aggressive",
                        side=side,
                        entry_price=entry,
                        stop_price=stop,
                        tp1=tp1,
                        tp2=tp2,
                        trigger=f"лимит @ Fib C {C_EXT_SOFT:.3f}/{C_EXT_HARD:.3f}×B · стоп {ATR_STOP_MULT:.0f}×ATR",
                        rr=(reward / risk) if risk > 0 else 0.0,
                        ready=near and px >= entry * 0.996,
                    )

    # --- Консервативный: обновление хая/лоя волны 1 после волны 2 (или 3 после волны 4) ---
    # После волны 2: ждём пробой p1
    if p1 and p2 and impulse.current_wave in {"2", "3", "forming"}:
        if impulse.direction == "up":
            entry = p1.price
            stop = (p0.price if p0 else p2.price) * 0.998
            # TP = 3× риск (как в гайде) или проекция волны 3
            risk = abs(entry - stop)
            tp1 = entry + risk * 3.0
            tp2 = entry + abs(p1.price - (p0.price if p0 else entry)) * 1.618
            triggered = px >= entry * 0.999
            return ElliottEntryPlan(
                mode="conservative",
                side="long",
                entry_price=entry,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                trigger="консерв.: обновление хая волны 1 после волны 2",
                rr=3.0,
                ready=triggered and px <= entry * 1.01,
            )
        else:
            entry = p1.price
            stop = (p0.price if p0 else p2.price) * 1.002
            risk = abs(stop - entry)
            tp1 = entry - risk * 3.0
            tp2 = entry - abs((p0.price if p0 else entry) - p1.price) * 1.618
            triggered = px <= entry * 1.001
            return ElliottEntryPlan(
                mode="conservative",
                side="short",
                entry_price=entry,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                trigger="консерв.: обновление лоя волны 1 после волны 2",
                rr=3.0,
                ready=triggered and px >= entry * 0.99,
            )

    # После волны 4: пробой хая/лоя волны 3 → волна 5
    if p3 and p4 and impulse.current_wave in {"4", "5", "complete"}:
        if impulse.direction == "up":
            entry = p3.price
            stop = p4.price * 0.997
            risk = abs(entry - stop)
            tp1 = entry + risk * 3.0
            tp2 = entry + abs(p3.price - p4.price) * 1.0
            return ElliottEntryPlan(
                mode="conservative",
                side="long",
                entry_price=entry,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                trigger="консерв.: пробой хая волны 3 → волна 5",
                rr=3.0,
                ready=px >= entry * 0.999 and px <= entry * 1.012,
            )
        else:
            entry = p3.price
            stop = p4.price * 1.003
            risk = abs(stop - entry)
            tp1 = entry - risk * 3.0
            tp2 = entry - abs(p4.price - p3.price) * 1.0
            return ElliottEntryPlan(
                mode="conservative",
                side="short",
                entry_price=entry,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                trigger="консерв.: пробой лоя волны 3 → волна 5",
                rr=3.0,
                ready=px <= entry * 1.001 and px >= entry * 0.988,
            )

    # После полного ABC — консервативный вход на обновлении хая волны 1 старшего порядка
    if abc and abc.phase == "complete" and p1 and impulse.current_wave == "complete":
        if impulse.direction == "up":
            entry = p1.price
            stop = (abc.points[-1].price if abc.points else (p0.price if p0 else entry)) * 0.997
            risk = abs(entry - stop)
            return ElliottEntryPlan(
                mode="conservative",
                side="long",
                entry_price=entry,
                stop_price=stop,
                tp1=entry + risk * 3.0,
                tp2=entry + risk * 5.0,
                trigger="консерв.: пробой хая 1 после ABC → новый импульс",
                rr=3.0,
                ready=px >= entry * 0.999,
            )
        else:
            entry = p1.price
            stop = (abc.points[-1].price if abc.points else (p0.price if p0 else entry)) * 1.003
            risk = abs(stop - entry)
            return ElliottEntryPlan(
                mode="conservative",
                side="short",
                entry_price=entry,
                stop_price=stop,
                tp1=entry - risk * 3.0,
                tp2=entry - risk * 5.0,
                trigger="консерв.: пробой лоя 1 после ABC → новый импульс",
                rr=3.0,
                ready=px <= entry * 1.001,
            )

    return ElliottEntryPlan(
        mode="wait",
        side=side,
        trigger="ждать завершения волны 2/4 или зоны C коррекции ABC",
        ready=False,
    )


def _label_ru(impulse: ElliottImpulse | None, abc: ElliottAbc | None) -> str:
    if impulse is None:
        return ""
    d = "бычий" if impulse.direction == "up" else "медв."
    wave = impulse.current_wave
    base = f"{d} импульс 1–5"
    if wave == "complete":
        base += " (завершён)"
    elif wave.isdigit():
        base += f" · сейчас волна {wave}"
    if abc and abc.label_ru:
        return f"{base} → {abc.label_ru}"
    if impulse.fib_notes:
        return f"{base} · {impulse.fib_notes[0]}"
    return base


def analyze_elliott_waves(
    bars: list["KlineBar"],
    swings: list["SwingPoint"],
) -> ElliottWaveResult:
    """Главная точка входа: импульс 1–5 + ABC + план входа."""
    empty = ElliottWaveResult(phase="unknown")
    if not bars or not swings or len(bars) < 16:
        return empty

    impulse = detect_elliott_impulse(swings, bars)
    if impulse is None:
        return empty

    abc = None
    if impulse.current_wave == "complete" or (
        impulse.points and impulse.points[-1].label in {"3", "4", "5"}
    ):
        # ABC ищем после достаточно развитого импульса
        if impulse.current_wave == "complete" or (
            impulse.points and impulse.points[-1].label == "5"
        ):
            abc = detect_elliott_abc(swings, bars, impulse)

    plan = build_elliott_entry_plan(impulse, abc, bars)

    draw: list[ElliottPoint] = list(impulse.points)
    if abc:
        for p in abc.points:
            if p.label in {"A", "B", "C"}:
                draw.append(p)

    if abc and abc.phase:
        phase = f"abc_{abc.phase.lower()}" if abc.phase != "complete" else "abc_complete"
    elif impulse.current_wave == "complete":
        phase = "impulse_complete"
    elif impulse.current_wave.isdigit():
        phase = f"impulse_{impulse.current_wave}"
    else:
        phase = "impulse_forming"

    conf = max(3, min(9, impulse.quality // 12))
    if impulse.valid:
        conf = min(9, conf + 1)
    if abc and abc.valid and abc.phase in {"C", "complete"}:
        conf = min(9, conf + 1)
    if plan and plan.ready:
        conf = min(9, conf + 1)

    notes: list[str] = []
    if impulse.violations:
        notes.append("EW: " + "; ".join(impulse.violations[:2]))
    if impulse.fib_notes:
        notes.extend(impulse.fib_notes[:2])
    if plan and plan.mode != "wait":
        notes.append(plan.trigger)

    return ElliottWaveResult(
        impulse=impulse,
        abc=abc,
        label_ru=_label_ru(impulse, abc),
        phase=phase,
        entry_plan=plan,
        draw_points=draw,
        confidence=conf,
        notes=notes[:4],
    )


def elliott_location_ok(result: ElliottWaveResult | None, side: str) -> bool:
    """Локация для trade gate: готовый консервативный/агрессивный вход."""
    if result is None or result.entry_plan is None:
        return False
    plan = result.entry_plan
    if not plan.ready or plan.mode == "wait":
        return False
    if plan.side != side:
        return False
    if result.impulse and not result.impulse.valid and result.impulse.quality < 60:
        return False
    return True
