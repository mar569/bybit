"""Полные волны Эллиотта (1–5 + ABC) — правила по TradeRevolution + PPT.

Источник: https://traderevolution.by/elliott-wave-principle-basics/motive-wave/impuls-pravila/

=== ПРАВИЛА ИМПУЛЬСА ===
- Импульс = 5 подволн: структура 5-3-5-3-5 (21 волна), затем коррекция 5-3-5 (13 волн).
- Волна 3 — чаще всего самая длинная и НИКОГДА самая короткая.
- Волна 2 НИКОГДА не заходит за основание волны 1.
- Волна 3 должна заходить за окончание волны 1.
- Волна 4 не должна заходить в зону волны 1 (правило перекрытия).
- Волна 5 чаще всего пробивает максимум волны 3 (кроме усечения).

=== ЧЕРЕДОВАНИЕ ===
- W2 глубокая (зигзаг/двойной зигзаг) → W4 плоская (флет/треугольник/комбинация) и наоборот.
- W2 чаще зигзаг: откат 50/61.8/78.6% от W1.
- W4 чаще треугольник/флет: откат 38.2/50% от W3.
- W4 обычно заканчивается в зоне iv внутри W3.

=== РАСТЯЖЕНИЕ ===
- Только одна из 1/3/5 растянута (в суперциклах м.б. 3+5).
- Растяжение W3 → W1≈W5 пропорциональны.
- Растяжение W1 → W5 ≈ 61.8% расширения Fib от W1.
- Растяжение W5 → W5 ≈ 161.8% расширения Fib от W3.

=== ДИАГОНАЛИ ===
- Начальная (W1/A): структура 3-3-3-3-3 или 5-3-5-3-5; W4 заходит в зону W1, но НЕ за W2.
- Конечная (W5/C): структура 3-3-3-3-3; W4 заходит в зону W1, но НЕ за W2; возможно усечение W5.
- Сужающаяся: W3<W1, W4<W2, W5<W3.
- Расширяющаяся: W3>W1, W4>W2, W5>W3.

=== УСЕЧЕНИЕ ===
- Только в W5 импульса или конечной сужающейся диагонали.
- Формирует двойную вершину/дно.
- Крайне редко.

=== КОРРЕКЦИИ ===
- Зигзаг 5-3-5: B никогда за начало A, C всегда за окончание A. A≈C по длине.
- Двойной зигзаг 3-3-3: X не за начало W, Y за окончание W.
- Расширенный флет 3-3-5: B всегда пробивает макс. A, C всегда пробивает мин. A. C ≈ 100–161.8% Fib от A.
- Бегущий флет 3-3-5: B пробивает макс. A, C НЕ пробивает мин. A. A≈C.
- Обычный флет 3-3-5: B ≈ 90%+ от A. На практике почти не встречается.

=== ТРЕУГОЛЬНИКИ ===
- В W4, B зигзага, X комбинации. Всегда предпоследняя волна.
- Симметричный: C не за A, D не за B, E около линии A-C.
- Восходящий: C не за A, D ≈ B (прямая линия), E около A-C.
- Расширяющийся: C за A, D за B, E за C. Самый редкий.

=== ДИВЕРГЕНЦИЯ ===
- Пик MACD/AO = W3 (или растянутая W5).
- Дивергенция между W3 и W5 — сильнейший сигнал разворота.

=== ФИБОНАЧЧИ (кластеры) ===
- W2: 50%, 61.8%, реже 78.6% от W1.
- W4: 38.2%, реже 50% от W3.
- W3 target: 161.8%, 200%, 261.8% × W1.
- W5 target: 61.8%, 78.6%, 100%, 161.8% × расширение.
- Кластер = 2-3 уровня Fib от разных измерений, сходящихся в одной зоне.
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
MIN_IMPULSE_QUALITY = 58
RECENT_SWING_WINDOW = 36
GLOBAL_SWING_WINDOW = 48
LOCAL_LOOKBACK_BARS = 72  # ~6ч на 5m — локальные 1–5 / abc / abcde
ATR_STOP_MULT = 3.0
ATR_TP_MULT = 3.0
C_EXT_SOFT = 1.272
C_EXT_HARD = 1.618

# TradeRevolution Fib — правила импульса
FIB_W2_OF_W1 = (0.50, 0.786)        # W2 = 50/61.8/78.6% × W1
FIB_W2_GOLD = (0.50, 0.618)         # идеальная зона
FIB_W2_DEEP = 0.786                 # максимальный допустимый откат
FIB_W3_OF_W1 = (1.618, 2.618)       # W3 target = 161.8–261.8% × W1
FIB_W3_SOFT = (1.40, 3.00)          # шире для детекта
FIB_W4_OF_W3 = (0.382, 0.50)        # W4 = 38.2–50% × W3
FIB_W5_OF_W3 = (0.382, 0.618)       # W5 = 38.2–61.8% × W3 (classic)
FIB_W5_EXT = (0.618, 1.618)         # W5 расширение: 61.8/78.6/100/161.8%
FIB_A_OF_W5 = (0.50, 0.618)         # A ≈ 50–61.8% × 5
FIB_B_OF_A = (0.382, 0.50)          # B ≈ 38.2–50% × A
# Flat rules (TradeRevolution)
FIB_FLAT_B_MIN = 0.90               # обычный флет: B ≥ 90% от A
FIB_EXPANDED_FLAT_C = (1.00, 1.618) # расширенный: C = 100–161.8% от A
# Растяжение
EXT_MIN_VS_PEER = 1.618
EXT_MIN_VS_W1 = 1.618


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
    # Классические Fib-пропорции (обязательны для «торгового» valid)
    fib_classic_ok: bool = False
    fib_w2_ratio: float = 0.0
    fib_w3_ratio: float = 0.0
    fib_w4_ratio: float = 0.0
    fib_w5_ratio: float = 0.0
    fib_w2_ok: bool = False
    fib_w3_ok: bool = False
    fib_w4_ok: bool = False
    fib_w5_ok: bool = False
    fib_w2_gold: bool = False  # 2 в зоне 0.50–0.618
    # Расширенная теория (PPT / Prechter)
    extension: str = ""          # "1" | "3" | "5" | ""
    truncated: bool = False      # усечение волны 5
    diagonal: str = ""           # "ending" | "leading" | ""
    structure_note_ru: str = ""  # для графика / тезиса

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
    corr_type: str = ""  # zigzag | flat | triangle | unknown


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
    # Аннотации для отрисовки (PPT-структуры)
    extension: str = ""
    truncated: bool = False
    diagonal: str = ""
    corr_type: str = ""
    structure_note_ru: str = ""
    # PPT advanced
    triangle_kind: str = ""  # contracting | expanding
    triangle_bias: str = ""
    complex_kind: str = ""  # double_three | triple_three
    fib_target_prices: list[float] = field(default_factory=list)
    fib_target_labels: list[str] = field(default_factory=list)
    path_bias: str = ""
    path_prices: list[float] = field(default_factory=list)
    path_labels: list[str] = field(default_factory=list)
    path_reason_ru: str = ""
    path_horizon_hours: float = 0.0
    path_scenario: str = ""
    path_invalidation: float | None = None
    fib_cluster_zones: list[str] = field(default_factory=list)  # "price_lo–price_hi (N levels)"
    # сырые объекты для отрисовки линий треугольника
    triangle_obj: object | None = None
    complex_obj: object | None = None
    # Два масштаба: глобальный (весь импульс) + локальный (внутри / недавно)
    global_draw_points: list[ElliottPoint] = field(default_factory=list)
    local_draw_points: list[ElliottPoint] = field(default_factory=list)
    global_label_ru: str = ""
    local_label_ru: str = ""
    has_global: bool = False
    has_local: bool = False

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


def _impulse_leg_sizes(by: dict[str, ElliottPoint]) -> tuple[float, float, float]:
    w1 = _leg_size(by["0"], by["1"]) if "0" in by and "1" in by else 0.0
    w3 = _leg_size(by["2"], by["3"]) if "2" in by and "3" in by else 0.0
    w5 = _leg_size(by["4"], by["5"]) if "4" in by and "5" in by else 0.0
    return w1, w3, w5


def classify_extension(pts: list[ElliottPoint]) -> str:
    """Какая импульсная волна растянута: 1 / 3 / 5 (PPT). Пусто если нет явного."""
    by = {p.label: p for p in pts}
    if not all(k in by for k in ("0", "1", "2", "3")):
        return ""
    w1, w3, w5 = _impulse_leg_sizes(by)
    if w1 <= 0 or w3 <= 0:
        return ""
    # Полный импульс: выбираем явно самую длинную при пороге 1.618
    if w5 > 0:
        peers = [("1", w1), ("3", w3), ("5", w5)]
        peers.sort(key=lambda x: x[1], reverse=True)
        top_lab, top_sz = peers[0]
        mid_sz = peers[1][1]
        if top_sz >= mid_sz * EXT_MIN_VS_PEER * 0.92:
            return top_lab
        # Частый кейс: растяжение 3 при w3 ≥ 1.618×w1 даже если 5 ещё длинновата
        if w3 >= w1 * EXT_MIN_VS_W1 and w3 >= w5 * 0.95:
            return "3"
        return ""
    # Формирующаяся: только 1–3
    if w3 >= w1 * EXT_MIN_VS_W1:
        return "3"
    if w1 >= w3 * EXT_MIN_VS_PEER:
        return "1"
    return ""


def detect_truncation(pts: list[ElliottPoint], direction: str) -> bool:
    """Усечение волны 5: не обновляет экстремум 3 после сильной (растянутой) 3."""
    by = {p.label: p for p in pts}
    if not all(k in by for k in ("0", "1", "2", "3", "4", "5")):
        return False
    w1, w3, _w5 = _impulse_leg_sizes(by)
    if w1 <= 0 or w3 < w1 * EXT_MIN_VS_W1 * 0.95:
        return False
    p3, p5 = by["3"], by["5"]
    if direction == "up":
        return p5.price <= p3.price
    return p5.price >= p3.price


def detect_diagonal_type(
    pts: list[ElliottPoint],
    direction: str,
    bars: list["KlineBar"],
) -> str:
    """leading | ending | '' — диагональный треугольник по TradeRevolution.

    Правила:
    - W2 НИКОГДА не заходит за основание W1.
    - W3 заходит за окончание W1.
    - W4 ДОЛЖНА заходить в зону W1 (перекрытие обязательно), но НЕ за окончание W2.
    - Начальная: W5 обязана пробить W3 (без усечения).
    - Конечная: W5 может не дойти до W3 (усечение допустимо).
    - Сужающаяся: W3<W1, W4<W2, W5<W3.
    - Расширяющаяся: W3>W1, W4>W2, W5>W3.
    """
    by = {p.label: p for p in pts}
    if not all(k in by for k in ("0", "1", "2", "3", "4")):
        return ""
    p0, p1, p2, p3, p4 = by["0"], by["1"], by["2"], by["3"], by["4"]

    # Required: W4 заходит в зону W1 (перекрытие) — иначе это не диагональ
    overlap = (
        (direction == "up" and p4.price <= p1.price)
        or (direction == "down" and p4.price >= p1.price)
    )
    if not overlap:
        return ""

    # Required: W4 НЕ за окончание W2 (жёстко)
    w4_past_w2 = (
        (direction == "up" and p4.price < p2.price)
        or (direction == "down" and p4.price > p2.price)
    )
    if w4_past_w2:
        return ""

    # Determine contracting vs expanding
    w1 = _leg_size(p0, p1)
    w2 = _leg_size(p1, p2)
    w3 = _leg_size(p2, p3)
    w4 = _leg_size(p3, p4)
    contracting = w3 < w1 * 1.02 and w4 < w2 * 1.02
    expanding = w3 > w1 * 0.98 and w4 > w2 * 0.98

    if "5" in by:
        p5 = by["5"]
        w5 = _leg_size(p4, p5)
        if contracting:
            contracting = contracting and w5 < w3 * 1.02
        if expanding:
            expanding = expanding and w5 > w3 * 0.98
        # Ending: overlap + (клин или усечение/канал)
        if contracting or expanding:
            return "ending"
        # overlap есть, но без клина — слабая диагональ, не помечаем
        return ""
    # Leading: overlap + сужение
    if contracting:
        return "leading"
    return ""


def classify_abc_type(
    abc_pts: list[ElliottPoint],
    b_retrace: float,
    *,
    impulse_direction: str = "",
) -> str:
    """zigzag | flat | expanded_flat | running_flat | unknown по TradeRevolution.

    - Зигзаг: B = 38–78% от A, C всегда за окончание A.
    - Обычный флет: B ≈ 90%+ от A, C ≈ окончание A. (На практике почти не встречается.)
    - Расширенный флет: B пробивает начало A (>100%), C пробивает окончание A. C ≈ 100–161.8% Fib от A.
    - Бегущий флет: B пробивает начало A (>100%), C НЕ пробивает окончание A.
    """
    by = {p.label: p for p in abc_pts}
    if not all(k in by for k in ("A", "B")):
        return "unknown"

    # Определяем пробои для C
    has_c = "C" in by
    if has_c and impulse_direction:
        pa, pc = by["A"], by["C"]
        if impulse_direction == "up":
            c_beyond_a = pc.price < pa.price  # C ниже A (коррекция вниз)
        else:
            c_beyond_a = pc.price > pa.price  # C выше A (коррекция вверх)
    else:
        c_beyond_a = None

    # Expanded/Running: B > 100% от A
    if b_retrace >= 1.00:
        if has_c and c_beyond_a is True:
            return "expanded_flat"
        if has_c and c_beyond_a is False:
            return "running_flat"
        return "expanded_flat" if b_retrace >= 1.05 else "flat"

    # Flat: B ≈ 90%+ от A
    if b_retrace >= FIB_FLAT_B_MIN - 0.02:
        return "flat"

    # Zigzag: B = 38–78% от A
    if 0.30 <= b_retrace <= 0.80:
        return "zigzag"

    return "unknown"


def _structure_note_ru(
    *,
    extension: str,
    truncated: bool,
    diagonal: str,
    corr_type: str = "",
) -> str:
    parts: list[str] = []
    if extension == "3":
        parts.append("растяжение волны 3")
    elif extension == "1":
        parts.append("растяжение волны 1")
    elif extension == "5":
        parts.append("растяжение волны 5")
    if truncated:
        parts.append("усечение волны 5")
    if diagonal == "ending":
        parts.append("конечная диагональ")
    elif diagonal == "leading":
        parts.append("начальная диагональ")
    if corr_type == "zigzag":
        parts.append("ABC зигзаг")
    elif corr_type == "expanded_flat":
        parts.append("ABC расширенный флет")
    elif corr_type == "running_flat":
        parts.append("ABC бегущий флет")
    elif corr_type == "flat":
        parts.append("ABC плоская")
    elif corr_type == "triangle":
        parts.append("треугольник ABCDE")
    return ", ".join(parts)


def _validate_impulse_rules(
    pts: list[ElliottPoint],
    direction: str,
    *,
    allow_overlap_4_1: bool = False,
    strict_w4_vs_w2: bool = True,
) -> tuple[list[str], bool]:
    """Жёсткий чек-лист Эллиотта (импульс вверх и вниз одинаково).

    Правила (Prechter / TradeRevolution / analiz.md):
    1. Волна 2 НИКОГДА не заходит за начало волны 1 (основание 0).
    2. Волна 3 всегда проходит за окончание волны 1; никогда не самая короткая.
    3. Волна 4 НЕ заходит в территорию волны 1 (кроме настоящей диагонали).
    4. Волна 4 НЕ пробивает экстремум волны 2 (в диагонали — обязательно;
       в обычном импульсе — тоже держим как правило «чистой» разметки).
    5. Волна 5 обычно обновляет экстремум 3 (усечение — отдельный флаг).

    allow_overlap_4_1: только для подтверждённой диагонали.
    """
    violations: list[str] = []
    by = {p.label: p for p in pts}
    need = ["0", "1", "2"]
    if not all(k in by for k in need):
        return ["мало точек"], False

    p0, p1, p2 = by["0"], by["1"], by["2"]
    if direction == "up":
        if p1.price <= p0.price:
            violations.append("волна 1 не вверх")
        # W2 не ниже старта W1
        if p2.price <= p0.price:
            violations.append("волна 2 зашла за основание 1")
        if p2.price >= p1.price:
            violations.append("волна 2 не откат")
    else:
        # Падающий импульс: 0=хай, 1=лой, 2=откат вверх < 0
        if p1.price >= p0.price:
            violations.append("волна 1 не вниз")
        if p2.price >= p0.price:
            violations.append("волна 2 зашла за основание 1")
        if p2.price <= p1.price:
            violations.append("волна 2 не откат")

    if "3" in by:
        p3 = by["3"]
        if direction == "up":
            if p3.price <= p1.price:
                violations.append("волна 3 не превысила вершину 1")
        else:
            if p3.price >= p1.price:
                violations.append("волна 3 не превысила дно 1")

    if "4" in by and "1" in by and "3" in by:
        p4 = by["4"]
        p1 = by["1"]
        p2 = by["2"]
        p3 = by["3"]
        if direction == "up":
            # Перекрытие с W1
            if p4.price <= p1.price and not allow_overlap_4_1:
                violations.append("волна 4 пересекла волну 1")
            elif p4.price <= p1.price and allow_overlap_4_1:
                violations.append("перекрытие 4↔1 (диагональ)")
            if p4.price >= p3.price:
                violations.append("волна 4 не откат")
            # W4 не пробивает лой W2
            if strict_w4_vs_w2 and p4.price < p2.price:
                violations.append("волна 4 пробила волну 2")
        else:
            if p4.price >= p1.price and not allow_overlap_4_1:
                violations.append("волна 4 пересекла волну 1")
            elif p4.price >= p1.price and allow_overlap_4_1:
                violations.append("перекрытие 4↔1 (диагональ)")
            if p4.price <= p3.price:
                violations.append("волна 4 не откат")
            # W4 не пробивает хай W2
            if strict_w4_vs_w2 and p4.price > p2.price:
                violations.append("волна 4 пробила волну 2")

    if "5" in by and "3" in by and "4" in by:
        p5 = by["5"]
        p0, p1, p3, p4 = by["0"], by["1"], by["3"], by["4"]
        w1 = _leg_size(p0, p1)
        w3 = _leg_size(by["2"], p3)
        w5 = _leg_size(p4, p5)
        fail5 = (direction == "up" and p5.price <= p3.price) or (
            direction == "down" and p5.price >= p3.price
        )
        if fail5:
            if w3 >= w1 * EXT_MIN_VS_W1 * 0.95:
                violations.append("усечение волны 5 (допустимо после сильной 3)")
            else:
                violations.append(
                    "волна 5 не обновила хай 3"
                    if direction == "up"
                    else "волна 5 не обновила лой 3"
                )
        # W3 НИКОГДА самая короткая (fatal)
        if w3 < w1 * 0.98 and w3 < w5 * 0.98:
            violations.append("волна 3 самая короткая (запрещено)")
        elif w3 < w1 * 0.98 or w3 < w5 * 0.98:
            violations.append("волна 3 не самая длинная (допуск)")
    elif "3" in by:
        p0, p1, p3 = by["0"], by["1"], by["3"]
        w1 = _leg_size(p0, p1)
        w3 = _leg_size(by["2"], p3)
        if w1 > 0 and w3 > 0 and w3 < w1 * 0.65:
            violations.append("волна 3 слишком мала относительно 1")

    fatal_keys = (
        "зашла за основание",
        "пересекла волну 1",
        "пробила волну 2",
        "не превысила",
        "не вверх",
        "не вниз",
        "не обновила",
        "самая короткая",
        "не откат",
    )
    fatal = [
        v
        for v in violations
        if any(k in v for k in fatal_keys)
        and "усечение" not in v
        and "диагональ" not in v
        and "допуск" not in v
    ]
    return violations, len(fatal) == 0


def _in_band(ratio: float, band: tuple[float, float], *, slack: float = 0.0) -> bool:
    lo, hi = band
    return (lo - slack) <= ratio <= (hi + slack)


def _fib_proportion_check(pts: list[ElliottPoint]) -> dict:
    """Классические Fib-связи волн (гайд). Возвращает ratios + ok-флаги + notes."""
    by = {p.label: p for p in pts}
    out: dict = {
        "notes": [],
        "w2": 0.0,
        "w3": 0.0,
        "w4": 0.0,
        "w5": 0.0,
        "w2_ok": False,
        "w3_ok": False,
        "w4_ok": False,
        "w5_ok": False,
        "w2_gold": False,
        "classic_ok": False,
        "required_ok": False,
    }
    if not all(k in by for k in ("0", "1", "2")):
        return out

    w1 = _leg_size(by["0"], by["1"])
    if w1 <= 0:
        return out
    w2 = _leg_size(by["1"], by["2"])
    r2 = w2 / w1
    out["w2"] = r2
    out["w2_ok"] = _in_band(r2, FIB_W2_OF_W1, slack=0.03)
    out["w2_gold"] = _in_band(r2, FIB_W2_GOLD, slack=0.02)
    # TradeRev: допуск 38.2% как минимум (плоская коррекция)
    if not out["w2_ok"] and _in_band(r2, (0.382, 0.50), slack=0.03):
        out["w2_ok"] = True  # shallow W2 допустима
    if out["w2_ok"]:
        if out["w2_gold"]:
            tag = "золото 50–61.8"
        elif r2 >= 0.72:
            tag = "глубокая 78.6"
        else:
            tag = f"Fib {r2:.0%}"
        out["notes"].append(f"2={r2:.0%}×1 ({tag})")
    else:
        out["notes"].append(f"2={r2:.0%}×1 ✗ вне 38–78.6")

    if "3" in by:
        w3 = _leg_size(by["2"], by["3"])
        r3 = w3 / w1
        out["w3"] = r3
        out["w3_ok"] = _in_band(r3, FIB_W3_SOFT, slack=0.05)
        if _in_band(r3, FIB_W3_OF_W1, slack=0.05):
            out["notes"].append(f"3={r3:.2f}×1 (Fib 1.618–2.618)")
        elif out["w3_ok"]:
            out["notes"].append(f"3={r3:.2f}×1 (допуск)")
        else:
            out["notes"].append(f"3={r3:.2f}×1 ✗ слабо/сильно")

    if "3" in by and "4" in by:
        w3 = _leg_size(by["2"], by["3"])
        w4 = _leg_size(by["3"], by["4"])
        if w3 > 0:
            r4 = w4 / w3
            out["w4"] = r4
            out["w4_ok"] = _in_band(r4, FIB_W4_OF_W3, slack=0.04)
            if out["w4_ok"]:
                out["notes"].append(f"4={r4:.0%}×3 (Fib 38.2–50)")
            else:
                out["notes"].append(f"4={r4:.0%}×3 ✗ не 38.2–50")

    if "3" in by and "4" in by and "5" in by:
        w3 = _leg_size(by["2"], by["3"])
        w5 = _leg_size(by["4"], by["5"])
        if w3 > 0:
            r5 = w5 / w3
            out["w5"] = r5
            out["w5_ok"] = _in_band(r5, FIB_W5_OF_W3, slack=0.05)
            if out["w5_ok"]:
                out["notes"].append(f"5={r5:.0%}×3 (Fib 38.2–61.8)")
            else:
                out["notes"].append(f"5={r5:.0%}×3 ✗ вне 38.2–61.8")

    # required: всё что уже сформировано — должно быть в коридоре
    required = [out["w2_ok"]]
    if "3" in by:
        required.append(out["w3_ok"])
    if "4" in by:
        required.append(out["w4_ok"])
    if "5" in by:
        required.append(out["w5_ok"])
    out["required_ok"] = all(required)
    # classic_ok: минимум волна 2 в классике; при 4+ ещё и волна 4
    if "4" in by:
        out["classic_ok"] = out["w2_ok"] and out["w4_ok"]
    else:
        out["classic_ok"] = out["w2_ok"]
    return out


def _fib_proportion_notes(pts: list[ElliottPoint], direction: str) -> list[str]:
    _ = direction
    return list(_fib_proportion_check(pts).get("notes") or [])


def _score_impulse(
    pts: list[ElliottPoint],
    direction: str,
    violations: list[str],
    fib_notes: list[str],
    *,
    bars: list["KlineBar"],
    fib: dict | None = None,
) -> int:
    score = 35
    n = len(pts)
    score += min(25, (n - 2) * 5)
    fib = fib or _fib_proportion_check(pts)
    if fib.get("w2_ok"):
        score += 12
    if fib.get("w2_gold"):
        score += 6
    if fib.get("w3_ok"):
        score += 8
    if fib.get("w4_ok"):
        score += 12
    if fib.get("w5_ok"):
        score += 6
    if fib.get("classic_ok"):
        score += 8
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
    _ = direction
    _ = fib_notes
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


def _make_impulse(
    pts: list[ElliottPoint],
    direction: str,
    *,
    bars: list["KlineBar"],
    current_wave: str,
    quality_cap: int | None = None,
    min_q: int = MIN_IMPULSE_QUALITY,
) -> ElliottImpulse | None:
    # Сначала настоящая диагональ по клину; только она разрешает overlap 4↔1
    diagonal = detect_diagonal_type(pts, direction, bars)
    violations, rules_ok = _validate_impulse_rules(
        pts,
        direction,
        allow_overlap_4_1=bool(diagonal),
        strict_w4_vs_w2=True,
    )
    # НЕ авто-помечать любой overlap как диагональ — иначе «ломаные» импульсы
    # проходят в сигналы. Диагональ только если detect_diagonal_type её увидел.

    fib = _fib_proportion_check(pts)
    fib_notes = list(fib.get("notes") or [])
    extension = classify_extension(pts)
    truncated = detect_truncation(pts, direction)
    if extension:
        fib_notes.insert(0, f"растяжение волны {extension}")
    if truncated:
        fib_notes.insert(0, "усечение волны 5")
    if diagonal == "ending":
        fib_notes.insert(0, "конечная диагональ")
    elif diagonal == "leading":
        fib_notes.insert(0, "начальная диагональ")

    q = _score_impulse(pts, direction, violations, fib_notes, bars=bars, fib=fib)
    if extension == "3":
        q = min(100, q + 6)
    if truncated:
        q = min(100, q + 3)
    if diagonal:
        q = min(100, q + 4)
    if quality_cap is not None:
        q = min(q, quality_cap)
    fib_ok = bool(fib.get("classic_ok"))
    # Диагональ: Fib classic часто ломается — только мягкий допуск по quality,
    # но НЕ для торгового valid без отдельного флага
    if diagonal and not fib_ok and q >= max(min_q + 8, 66):
        fib_ok = True
    if not rules_ok:
        return None
    if q < min_q and not fib_ok:
        return None
    by = {p.label: p for p in pts}
    wave3_longest = False
    if all(k in by for k in ("0", "1", "2", "3", "4", "5")):
        w1 = _leg_size(by["0"], by["1"])
        w3 = _leg_size(by["2"], by["3"])
        w5 = _leg_size(by["4"], by["5"])
        wave3_longest = w3 >= w1 * 0.98 and w3 >= w5 * 0.98
    alt_24 = False
    if all(k in by for k in ("1", "2", "3", "4")):
        alt_24 = _is_sharp_correction(by["1"], by["2"], bars) != _is_sharp_correction(
            by["3"], by["4"], bars
        )
    # 3 самая длинная ИЛИ явное растяжение 1/5 ИЛИ усечение ИЛИ диагональ
    shape_ok = (
        wave3_longest
        or "5" not in by
        or extension in {"1", "5"}
        or truncated
        or bool(diagonal)
    )
    # Торговый valid: правила + Fib; диагональ — отдельный тип (не «чистый импульс»)
    trade_valid = rules_ok and fib_ok and q >= min_q and shape_ok and not (
        # мягкие «допуск» violations не валят, fatal уже отсечены rules_ok
        False
    )
    note = _structure_note_ru(
        extension=extension, truncated=truncated, diagonal=diagonal
    )
    return ElliottImpulse(
        direction=direction,
        points=pts,
        current_wave=current_wave,
        valid=trade_valid,
        quality=q,
        violations=violations[:4],
        fib_notes=fib_notes[:6],
        wave3_longest=wave3_longest,
        alternating_2_4=alt_24,
        fib_classic_ok=fib_ok,
        fib_w2_ratio=float(fib.get("w2") or 0),
        fib_w3_ratio=float(fib.get("w3") or 0),
        fib_w4_ratio=float(fib.get("w4") or 0),
        fib_w5_ratio=float(fib.get("w5") or 0),
        fib_w2_ok=bool(fib.get("w2_ok")),
        fib_w3_ok=bool(fib.get("w3_ok")),
        fib_w4_ok=bool(fib.get("w4_ok")),
        fib_w5_ok=bool(fib.get("w5_ok")),
        fib_w2_gold=bool(fib.get("w2_gold")),
        extension=extension,
        truncated=truncated,
        diagonal=diagonal,
        structure_note_ru=note,
    )


def _impulse_span_pct(c: ElliottImpulse) -> float:
    if not c.points:
        return 0.0
    prices = [p.price for p in c.points]
    mid = (max(prices) + min(prices)) / 2.0
    if mid <= 0:
        return 0.0
    return (max(prices) - min(prices)) / mid * 100.0


def _impulse_index_set(c: ElliottImpulse) -> set[int]:
    return {p.index for p in c.points}


def _structures_similar(a: ElliottImpulse, b: ElliottImpulse, *, min_overlap: float = 0.6) -> bool:
    sa, sb = _impulse_index_set(a), _impulse_index_set(b)
    if not sa or not sb:
        return False
    inter = len(sa & sb)
    return inter / min(len(sa), len(sb)) >= min_overlap


def _collect_impulse_candidates(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    *,
    swing_window: int = RECENT_SWING_WINDOW,
    min_q_full: int = MIN_IMPULSE_QUALITY,
) -> list[ElliottImpulse]:
    if not swings or not bars or len(swings) < 3:
        return []
    alt = _alternate_swings(swings)
    if len(alt) < 3:
        return []
    recent = alt[-swing_window:]
    candidates: list[ElliottImpulse] = []

    for direction in ("up", "down"):
        for start in range(0, max(1, len(recent) - 5)):
            chunk = recent[start : start + 6]
            if len(chunk) < 6:
                continue
            pts = _label_prefix(chunk, direction)
            if not pts or len(pts) < 6:
                continue
            imp = _make_impulse(
                pts, direction, bars=bars, current_wave="complete", min_q=min_q_full,
            )
            if imp is not None:
                candidates.append(imp)

        for n in (5, 4, 3):
            if len(recent) < n:
                continue
            for start in range(max(0, len(recent) - n - 4), max(1, len(recent) - n + 1)):
                chunk = recent[start : start + n]
                pts = _label_prefix(chunk, direction)
                if not pts or len(pts) < 3 or len(pts) >= 6:
                    continue
                last_lbl = pts[-1].label
                fib = _fib_proportion_check(pts)
                if "2" in {p.label for p in pts} and not fib.get("w2_ok"):
                    continue
                if "4" in {p.label for p in pts} and not fib.get("w4_ok"):
                    continue
                imp = _make_impulse(
                    pts,
                    direction,
                    bars=bars,
                    current_wave=last_lbl if last_lbl != "5" else "complete",
                    quality_cap=78,
                    min_q=max(48, min_q_full - 10),
                )
                if imp is not None:
                    candidates.append(imp)
    return candidates


def _pick_trade_impulse(candidates: list[ElliottImpulse]) -> ElliottImpulse | None:
    if not candidates:
        return None
    candidates = list(candidates)
    candidates.sort(
        key=lambda c: (
            int(c.valid),
            int(c.fib_classic_ok),
            int(c.current_wave == "complete"),
            c.quality,
            _impulse_span_pct(c),
            c.points[-1].index if c.points else 0,
        ),
        reverse=True,
    )
    best = candidates[0]
    for c in candidates[1:]:
        if (
            c.valid
            and c.fib_classic_ok
            and c.direction == "down"
            and _impulse_span_pct(c) >= 8.0
            and best.direction == "up"
            and _impulse_span_pct(best) < _impulse_span_pct(c) * 0.40
            and c.quality >= best.quality - 12
        ):
            return c
    return best


def _pick_global_impulse(candidates: list[ElliottImpulse]) -> ElliottImpulse | None:
    """Глобальный = максимальный размах среди качественных кандидатов."""
    if not candidates:
        return None
    scored = [
        c for c in candidates
        if c.quality >= 48 and len(c.points) >= 4
    ] or list(candidates)
    return max(scored, key=lambda c: (_impulse_span_pct(c), c.quality, int(c.valid)))


def detect_elliott_impulse(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
) -> ElliottImpulse | None:
    """Ищем лучшую 5-волновку (или формирующуюся) в недавних свингах.

    Кандидат без классических Fib (2∈38.2–61.8, 4∈38.2–50) не становится trade-valid.
    """
    candidates = _collect_impulse_candidates(
        swings, bars, swing_window=RECENT_SWING_WINDOW, min_q_full=MIN_IMPULSE_QUALITY,
    )
    return _pick_trade_impulse(candidates)


def _build_draw_bundle(
    impulse: ElliottImpulse | None,
    abc: ElliottAbc | None,
    extra: list[ElliottPoint] | None = None,
) -> list[ElliottPoint]:
    draw: list[ElliottPoint] = []
    if impulse:
        draw.extend(impulse.points)
    if abc:
        for p in abc.points:
            if p.label in {"A", "B", "C", "D", "E"}:
                draw.append(p)
    if extra:
        labs = {p.label for p in extra}
        # extra (ABCDE/WXY) заменяет одноимённые
        draw = [x for x in draw if x.label not in labs]
        draw.extend(extra)
    return draw


def _relabel_local(points: list[ElliottPoint]) -> list[ElliottPoint]:
    """Локальные метки: i–v / a–e — не путать с глобальными 1–5 / A–E."""
    mapping = {
        "0": "·0",
        "1": "i",
        "2": "ii",
        "3": "iii",
        "4": "iv",
        "5": "v",
        "A": "a",
        "B": "b",
        "C": "c",
        "D": "d",
        "E": "e",
        "W": "w",
        "X": "x",
        "Y": "y",
        "X2": "x2",
        "Z": "z",
    }
    out: list[ElliottPoint] = []
    for p in points:
        out.append(ElliottPoint(mapping.get(p.label, p.label), p.index, p.price))
    return out


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
    """Консервативный и агрессивный вход строго по гайду + Fib-коридоры.

    Без классических пропорций (2∈38.2–61.8, 4∈38.2–50) — только WAIT.
    """
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

    wait = ElliottEntryPlan(
        mode="wait",
        side=side,
        trigger="ждать Fib-зону волны 2/4 или C @ 1.272/1.618×B",
        ready=False,
    )

    # Жёстко: нет classic Fib по сформированным волнам → не торгуем
    if p2 and not impulse.fib_w2_ok and not impulse.fib_classic_ok:
        return ElliottEntryPlan(
            mode="wait",
            side=side,
            trigger=f"волна 2 вне Fib 38.2–61.8 (сейчас {impulse.fib_w2_ratio:.0%}) — переразметка",
            ready=False,
        )

    # --- Агрессивный: зона C @ 1.272 / 1.618 × B ---
    if abc and abc.phase in {"C", "complete"} and len(abc.points) >= 3:
        by = {p.label: p for p in abc.points}
        if "A" in by and "B" in by:
            a, b = by["A"], by["B"]
            b_leg = abs(b.price - a.price)
            # B ≈ 38.2–50% от A (мягкий допуск)
            b_ok = (
                abc.b_retrace <= 0
                or FIB_B_OF_A[0] - 0.05 <= abc.b_retrace <= FIB_B_OF_A[1] + 0.08
            )
            if b_leg > 0 and (b_ok or abc.at_aggressive_zone):
                if impulse.direction == "up":
                    e127 = b.price - b_leg * C_EXT_SOFT
                    e161 = b.price - b_leg * C_EXT_HARD
                    entry = e127 if abs(px - e127) <= abs(px - e161) else e161
                    stop = entry - ATR_STOP_MULT * atr if atr > 0 else (p0.price if p0 else entry * 0.985)
                    tp1 = entry + ATR_TP_MULT * atr if atr > 0 else (p1.price if p1 else entry * 1.03)
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
                        trigger=f"агр.: лимит C @ {C_EXT_SOFT:.3f}/{C_EXT_HARD:.3f}×B · стоп {ATR_STOP_MULT:.0f}×ATR",
                        rr=(reward / risk) if risk > 0 else 0.0,
                        ready=bool(near and px <= entry * 1.004 and impulse.fib_classic_ok),
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
                        trigger=f"агр.: лимит C @ {C_EXT_SOFT:.3f}/{C_EXT_HARD:.3f}×B · стоп {ATR_STOP_MULT:.0f}×ATR",
                        rr=(reward / risk) if risk > 0 else 0.0,
                        ready=bool(near and px >= entry * 0.996 and impulse.fib_classic_ok),
                    )

    # --- Консервативный после волны 2: только если 2 в Fib 38.2–61.8 ---
    # Идеал: откат в золото 50–61.8, вход на обновлении хая/лоя волны 1
    if p0 and p1 and p2 and impulse.current_wave in {"2", "3", "forming"} and impulse.fib_w2_ok:
        # зона конца волны 2
        w1 = abs(p1.price - p0.price)
        if impulse.direction == "up":
            gold_lo = p1.price - w1 * FIB_W2_GOLD[1]
            gold_hi = p1.price - w1 * FIB_W2_GOLD[0]
            in_gold = gold_lo <= p2.price <= gold_hi * 1.002 or impulse.fib_w2_gold
            entry = p1.price
            stop = p0.price * 0.998
            risk = abs(entry - stop)
            tp1 = entry + risk * 3.0
            tp2 = entry + w1 * 1.618
            triggered = px >= entry * 0.999
            # ready: пробой волны 1 после корректного Fib-отката (лучше из золота)
            ready = triggered and px <= entry * 1.012 and (in_gold or impulse.fib_w2_ok)
            return ElliottEntryPlan(
                mode="conservative",
                side="long",
                entry_price=entry,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                trigger=(
                    "консерв.: хай волны 1 после волны 2 @ Fib "
                    + ("50–61.8" if in_gold else "38.2–61.8")
                ),
                rr=3.0,
                ready=ready,
            )
        else:
            gold_hi = p1.price + w1 * FIB_W2_GOLD[1]
            gold_lo = p1.price + w1 * FIB_W2_GOLD[0]
            in_gold = gold_lo * 0.998 <= p2.price <= gold_hi or impulse.fib_w2_gold
            entry = p1.price
            stop = p0.price * 1.002
            risk = abs(stop - entry)
            tp1 = entry - risk * 3.0
            tp2 = entry - w1 * 1.618
            triggered = px <= entry * 1.001
            ready = triggered and px >= entry * 0.988 and (in_gold or impulse.fib_w2_ok)
            return ElliottEntryPlan(
                mode="conservative",
                side="short",
                entry_price=entry,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                trigger=(
                    "консерв.: лой волны 1 после волны 2 @ Fib "
                    + ("50–61.8" if in_gold else "38.2–61.8")
                ),
                rr=3.0,
                ready=ready,
            )

    # --- После волны 4: только если 4 ∈ 38.2–50% × волны 3 ---
    if p3 and p4 and impulse.current_wave == "4":
        if not impulse.fib_w4_ok:
            return ElliottEntryPlan(
                mode="wait",
                side=side,
                trigger=f"волна 4 вне Fib 38.2–50 ×3 (сейчас {impulse.fib_w4_ratio:.0%})",
                ready=False,
            )
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
                trigger="консерв.: пробой хая 3 после волны 4 @ Fib 38.2–50 → волна 5",
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
                trigger="консерв.: пробой лоя 3 после волны 4 @ Fib 38.2–50 → волна 5",
                rr=3.0,
                ready=px <= entry * 1.001 and px >= entry * 0.988,
            )

    if impulse.current_wave in {"5", "complete"} and not (abc and abc.phase in {"C", "complete"}):
        return ElliottEntryPlan(
            mode="wait",
            side=side,
            trigger="волна 5/complete — ждать ABC, не вдогонку",
            ready=False,
        )

    # После полного ABC — консервативный вход на обновлении хая волны 1
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
                ready=px >= entry * 0.999 and impulse.fib_classic_ok,
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
                ready=px <= entry * 1.001 and impulse.fib_classic_ok,
            )

    return wait


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
    if impulse.structure_note_ru:
        base += f" · {impulse.structure_note_ru}"
    if abc and abc.label_ru:
        extra = f" ({abc.corr_type})" if abc.corr_type and abc.corr_type != "unknown" else ""
        return f"{base} → {abc.label_ru}{extra}"
    if impulse.fib_notes:
        return f"{base} · {impulse.fib_notes[0]}"
    return base


def analyze_elliott_waves(
    bars: list["KlineBar"],
    swings: list["SwingPoint"],
) -> ElliottWaveResult:
    """Импульс 1–5 + ABC/ABCDE на двух масштабах: глобальный и локальный.

    Глобальный — максимальный размах (весь дамп/импульс).
    Локальный — структура в последних ~6ч (внутри волны / после 5).
    Оба слоя идут в draw; торговый план — по лучшему valid (часто локальный).
    """
    empty = ElliottWaveResult(phase="unknown")
    if not bars or not swings or len(bars) < 16:
        return empty

    from .elliott_advanced import analyze_elliott_advanced

    # --- Глобальные кандидаты (широкое окно свингов) ---
    global_cands = _collect_impulse_candidates(
        swings, bars, swing_window=GLOBAL_SWING_WINDOW, min_q_full=max(48, MIN_IMPULSE_QUALITY - 8),
    )
    global_imp = _pick_global_impulse(global_cands)

    # --- Локальные: только свежие бары ---
    local_start = max(0, len(bars) - LOCAL_LOOKBACK_BARS)
    local_swings = [s for s in swings if s.index >= local_start]
    local_cands = _collect_impulse_candidates(
        local_swings, bars, swing_window=RECENT_SWING_WINDOW, min_q_full=max(48, MIN_IMPULSE_QUALITY - 8),
    )
    local_imp = _pick_trade_impulse(local_cands)

    # Дедуп: локальный не должен быть копией глобального
    if global_imp and local_imp:
        if _structures_similar(global_imp, local_imp):
            # оставить более «размашистый» как global
            if _impulse_span_pct(local_imp) > _impulse_span_pct(global_imp) * 1.05:
                global_imp, local_imp = local_imp, None
            else:
                local_imp = None
        elif _impulse_span_pct(local_imp) >= _impulse_span_pct(global_imp) * 0.85:
            # почти тот же масштаб — один слой
            if _impulse_span_pct(local_imp) > _impulse_span_pct(global_imp):
                global_imp = local_imp
            local_imp = None

    if global_imp is None and local_imp is None:
        # fallback: старый одиночный поиск
        impulse = detect_elliott_impulse(swings, bars)
        if impulse is None:
            return empty
        global_imp = impulse

    # Торговый импульс: valid локальный приоритетнее
    if local_imp and local_imp.valid:
        impulse = local_imp
    elif global_imp and global_imp.valid:
        impulse = global_imp
    else:
        impulse = local_imp or global_imp
    assert impulse is not None

    def _abc_for(imp: ElliottImpulse | None) -> ElliottAbc | None:
        if imp is None:
            return None
        if imp.current_wave == "complete" or (
            imp.points and imp.points[-1].label in {"3", "4", "5"}
        ):
            if imp.current_wave == "complete" or (
                imp.points and imp.points[-1].label == "5"
            ):
                return detect_elliott_abc(swings, bars, imp)
        return None

    abc_g = _abc_for(global_imp)
    abc_l = _abc_for(local_imp)
    abc = abc_l or abc_g
    if abc is not None:
        abc_pts = [p for p in abc.points if p.label in {"A", "B", "C"}]
        abc.corr_type = classify_abc_type(
            abc_pts, abc.b_retrace,
            impulse_direction=impulse.direction,
        )
        impulse.structure_note_ru = _structure_note_ru(
            extension=impulse.extension,
            truncated=impulse.truncated,
            diagonal=impulse.diagonal,
            corr_type=abc.corr_type,
        )

    plan = build_elliott_entry_plan(impulse, abc, bars)

    # Advanced на полном окне + отдельно на локальном
    adv_g = analyze_elliott_advanced(bars, swings, global_imp or impulse, abc_g)
    adv_l = (
        analyze_elliott_advanced(bars, local_swings, local_imp, abc_l)
        if local_imp is not None
        else {"triangle": None, "complex_corr": None, "extra_draw_points": [], "notes": [], "fib_targets": [], "path": None}
    )
    # Для торговли/пути — advanced торгового импульса
    adv = adv_l if impulse is local_imp and local_imp is not None else adv_g
    tri = adv.get("triangle") or adv_g.get("triangle")
    complex_corr = adv.get("complex_corr") or adv_g.get("complex_corr")
    fib_targets = list(adv.get("fib_targets") or adv_g.get("fib_targets") or [])
    fib_clusters = list(adv.get("fib_clusters") or adv_g.get("fib_clusters") or [])
    path = adv.get("path") or adv_g.get("path")

    g_draw = _build_draw_bundle(
        global_imp, abc_g, list(adv_g.get("extra_draw_points") or []),
    )
    l_draw_raw = _build_draw_bundle(
        local_imp, abc_l, list(adv_l.get("extra_draw_points") or []),
    )
    l_draw = _relabel_local(l_draw_raw) if l_draw_raw else []

    # draw_points = оба слоя (глобальный + локальный с другими метками)
    draw: list[ElliottPoint] = list(g_draw)
    draw.extend(l_draw)

    if tri and tri.valid:
        impulse.structure_note_ru = _structure_note_ru(
            extension=impulse.extension,
            truncated=impulse.truncated,
            diagonal=impulse.diagonal,
            corr_type="triangle",
        )

    if abc and abc.phase:
        phase = f"abc_{abc.phase.lower()}" if abc.phase != "complete" else "abc_complete"
    elif tri and tri.valid:
        phase = "abcde_triangle"
    elif complex_corr and complex_corr.valid:
        phase = f"complex_{complex_corr.kind}"
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
    if impulse.extension == "3" or impulse.truncated or impulse.diagonal:
        conf = min(9, conf + 1)
    if tri and tri.valid:
        conf = min(9, conf + 1)
    if path and getattr(path, "confidence", 0) >= 6:
        conf = min(9, conf + 1)
    if global_imp and local_imp:
        conf = min(9, conf + 1)

    notes: list[str] = []
    if global_imp and local_imp:
        notes.append("EW: глобальный + локальный слой")
    if impulse.structure_note_ru:
        notes.append("EW: " + impulse.structure_note_ru)
    for n in (adv.get("notes") or []) + (adv_g.get("notes") or []):
        if n and n not in notes:
            notes.append(n)
    if impulse.violations:
        notes.append("EW: " + "; ".join(impulse.violations[:2]))
    if impulse.fib_notes:
        notes.extend([n for n in impulse.fib_notes[:2] if n not in (notes[0] if notes else "")])
    if plan and plan.mode != "wait":
        notes.append(plan.trigger)

    if plan and fib_targets and plan.mode in {"conservative", "aggressive", "wait"}:
        if plan.tp1 is None and fib_targets:
            plan.tp1 = fib_targets[0].price
        elif plan.tp2 is None and len(fib_targets) > 1:
            plan.tp2 = fib_targets[1].price

    corr_type = (abc.corr_type if abc else "")
    if tri and tri.valid:
        corr_type = "triangle"

    g_label = _label_ru(global_imp, abc_g) if global_imp else ""
    l_label = _label_ru(local_imp, abc_l) if local_imp else ""
    if adv_g.get("triangle") and getattr(adv_g["triangle"], "valid", False):
        g_label = (g_label + " · " if g_label else "") + adv_g["triangle"].label_ru
    if adv_l.get("triangle") and getattr(adv_l["triangle"], "valid", False):
        l_label = (l_label + " · " if l_label else "") + adv_l["triangle"].label_ru

    label = _label_ru(impulse, abc)
    if g_label and l_label:
        label = f"G: {g_label} | L: {l_label}"
    elif tri and tri.valid:
        label = f"{label} · {tri.label_ru}" if label else tri.label_ru
    elif complex_corr and complex_corr.valid:
        label = f"{label} · {complex_corr.label_ru}" if label else complex_corr.label_ru

    return ElliottWaveResult(
        impulse=impulse,
        abc=abc,
        label_ru=label,
        phase=phase,
        entry_plan=plan,
        draw_points=draw,
        confidence=conf,
        notes=notes[:6],
        extension=impulse.extension,
        truncated=impulse.truncated,
        diagonal=impulse.diagonal,
        corr_type=corr_type,
        structure_note_ru=impulse.structure_note_ru,
        triangle_kind=tri.kind if tri else "",
        triangle_bias=tri.breakout_bias if tri else "",
        complex_kind=complex_corr.kind if complex_corr else "",
        fib_target_prices=[t.price for t in fib_targets],
        fib_target_labels=[t.label for t in fib_targets],
        path_bias=getattr(path, "bias", "") if path else "",
        path_prices=list(getattr(path, "prices", []) or []) if path else [],
        path_labels=list(getattr(path, "labels", []) or []) if path else [],
        path_reason_ru=getattr(path, "reason_ru", "") if path else "",
        path_horizon_hours=float(getattr(path, "horizon_hours", 0) or 0) if path else 0.0,
        path_scenario=str(getattr(path, "scenario", "") or "") if path else "",
        path_invalidation=getattr(path, "invalidation", None) if path else None,
        fib_cluster_zones=[
            f"{c.mid:.6g} ({c.strength} ур.)" for c in fib_clusters[:3]
        ],
        triangle_obj=tri,
        complex_obj=complex_corr,
        global_draw_points=g_draw,
        local_draw_points=l_draw,
        global_label_ru=g_label,
        local_label_ru=l_label,
        has_global=bool(g_draw),
        has_local=bool(l_draw),
    )


def elliott_location_ok(result: ElliottWaveResult | None, side: str) -> bool:
    """Локация для trade gate: готовый вход только при classic Fib + ready."""
    if result is None or result.entry_plan is None:
        return False
    plan = result.entry_plan
    if not plan.ready or plan.mode == "wait":
        return False
    if plan.side != side:
        return False
    if result.impulse is None:
        return False
    # Торговать только по чек-листу + Fib-пропорциям гайда
    if not result.impulse.fib_classic_ok and result.impulse.quality < 70:
        return False
    if not result.impulse.valid and result.impulse.quality < 65:
        return False
    return True
