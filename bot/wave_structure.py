"""Wave Lite + Fibonacci — только по качественному импульсу.

Правила (как у профи):
1. Fib строится НЕ на любой свече, а на значимой импульсной ноге.
2. Нога = swing A → swing B, размер ≥ max(ATR×k, min_pct).
3. Импульс должен быть «чистым» (высокая efficiency пути).
4. Импульс должен быть завершён (есть хотя бы начало отката) —
   иначе это ещё не точка B, а текущий ход.
5. Без валидной ноги — Fib пустой, план не трогаем.

Три главных сочетания (сначала сильные факторы, потом Fib):
1. Fib + уровни поддержки/сопротивления
2. Fib + круглые уровни
3. Fib + ретест уровня пробоя после слома структуры

Fib — мягкая подсказка, не окончательное решение для входа.
Без ≥1 confluence план/вход не двигаем (на графике Fib можно показать).

Не для текста в Telegram — только уровни/bias/график.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bybit_klines import KlineBar
    from .ta_analysis import SwingPoint

FIB_RETRACEMENT_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXTENSION_RATIOS: tuple[float, ...] = (1.0, 1.272, 1.618)
FIB_CHART_RATIOS: tuple[float, ...] = (0.382, 0.5, 0.618, 1.272, 1.618)

# Жёсткие пороги качества
MIN_IMPULSE_PCT = 2.2          # абсолютный минимум хода, %
MIN_IMPULSE_ATR_MULT = 1.8     # ход ≥ ATR × это
MIN_EFFICIENCY = 0.52          # |Δ| / path — иначе «пила», не импульс
MIN_BARS_IN_LEG = 3
MAX_BARS_IN_LEG = 48
MIN_PULLBACK_OF_LEG = 0.12     # откат ≥ 12% ноги = импульс завершён
MAX_PULLBACK_OF_LEG = 1.05     # откат >105% = импульс сломан, Fib не для входа
RECENT_WINDOW_BARS = 72        # ищем ногу только в недавнем окне
MIN_QUALITY_TO_USE = 62        # ниже — не применяем к плану/графику


@dataclass(frozen=True)
class FibLevel:
    ratio: float
    price: float
    kind: str  # retracement | extension
    label: str


@dataclass(frozen=True)
class ImpulseLeg:
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    direction: str  # up | down
    size_pct: float = 0.0
    efficiency: float = 0.0
    atr_mult: float = 0.0
    pullback_frac: float = 0.0
    quality: int = 0

    @property
    def range_size(self) -> float:
        return abs(self.end_price - self.start_price)

    @property
    def mid(self) -> float:
        return (self.start_price + self.end_price) / 2.0


@dataclass
class WaveStructureResult:
    leg: ImpulseLeg | None = None
    fib_levels: list[FibLevel] = field(default_factory=list)
    wave_phase: str = "unknown"
    wave_bias: str = "neutral"
    confidence: int = 0
    entry_hint_price: float | None = None
    stop_hint_price: float | None = None
    target_hint_prices: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    valid: bool = False
    # Три главных сочетания Fib (подсказка, не вход)
    confluence_sr: bool = False
    confluence_round: bool = False
    confluence_retest: bool = False
    confluence_count: int = 0
    # Эллиотт Lite: импульс + коррекция ABC
    elliott_label: str = ""
    abc_phase: str = ""  # A | B | C | complete | forming | ""
    abc_label_ru: str = ""

    @property
    def has_confluence(self) -> bool:
        """Fib имеет смысл как подсказка только при ≥1 сильном факторе."""
        return self.confluence_count >= 1

    @property
    def chart_fib_levels(self) -> list[FibLevel]:
        if not self.valid:
            return []
        allowed = set(FIB_CHART_RATIOS)
        return [lv for lv in self.fib_levels if lv.ratio in allowed]


def _atr_pct(bars: list["KlineBar"], *, period: int = 14) -> float:
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
    if not trs:
        return 0.0
    atr = sum(trs) / len(trs)
    ref = bars[-1].close or 1.0
    return (atr / ref) * 100.0 if ref > 0 else 0.0


def _alternate_swings(swings: list["SwingPoint"]) -> list["SwingPoint"]:
    if not swings:
        return []
    out: list[SwingPoint] = [swings[0]]
    for s in swings[1:]:
        if s.kind == out[-1].kind:
            if s.kind == "high" and s.price >= out[-1].price:
                out[-1] = s
            elif s.kind == "low" and s.price <= out[-1].price:
                out[-1] = s
            continue
        out.append(s)
    return out


def _path_efficiency(
    bars: list["KlineBar"],
    start_idx: int,
    end_idx: int,
    *,
    direction: str,
) -> float:
    """Насколько ход «прямой»: net / суммарный путь closes."""
    if end_idx <= start_idx or end_idx >= len(bars):
        return 0.0
    seg = bars[start_idx : end_idx + 1]
    if len(seg) < 2:
        return 0.0
    net = seg[-1].close - seg[0].open
    if direction == "down":
        net = -net
    path = 0.0
    for i in range(1, len(seg)):
        path += abs(seg[i].close - seg[i - 1].close)
    if path <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, abs(net) / path))


def _pullback_fraction(
    bars: list["KlineBar"],
    leg: ImpulseLeg,
) -> float:
    """Доля отката от экстремума B к текущей цене / размер ноги."""
    if leg.range_size <= 0 or not bars:
        return 0.0
    current = bars[-1].close
    if leg.direction == "up":
        # откат вниз от end_price
        if current >= leg.end_price:
            return 0.0
        return (leg.end_price - current) / leg.range_size
    if current <= leg.end_price:
        return 0.0
    return (current - leg.end_price) / leg.range_size


def _counter_trend_share(
    bars: list["KlineBar"],
    start_idx: int,
    end_idx: int,
    *,
    direction: str,
) -> float:
    """Доля свечей против импульса внутри ноги."""
    seg = bars[start_idx : end_idx + 1]
    if not seg:
        return 1.0
    against = 0
    for b in seg:
        bull = b.close >= b.open
        if direction == "up" and not bull:
            against += 1
        elif direction == "down" and bull:
            against += 1
    return against / len(seg)


def _score_leg(
    bars: list["KlineBar"],
    start_idx: int,
    end_idx: int,
    start_price: float,
    end_price: float,
    direction: str,
    *,
    atr_pct: float,
) -> ImpulseLeg | None:
    bars_len = end_idx - start_idx + 1
    if bars_len < MIN_BARS_IN_LEG or bars_len > MAX_BARS_IN_LEG:
        return None
    if start_price <= 0 or end_price <= 0:
        return None

    size = abs(end_price - start_price)
    size_pct = size / start_price * 100.0
    min_pct = max(MIN_IMPULSE_PCT, atr_pct * MIN_IMPULSE_ATR_MULT if atr_pct > 0 else MIN_IMPULSE_PCT)
    if size_pct < min_pct:
        return None

    eff = _path_efficiency(bars, start_idx, end_idx, direction=direction)
    if eff < MIN_EFFICIENCY:
        return None

    against = _counter_trend_share(bars, start_idx, end_idx, direction=direction)
    if against > 0.45:
        return None

    atr_mult = size_pct / atr_pct if atr_pct > 0 else size_pct / MIN_IMPULSE_PCT

    # Черновик ноги для оценки отката
    draft = ImpulseLeg(
        start_idx=start_idx,
        end_idx=end_idx,
        start_price=start_price,
        end_price=end_price,
        direction=direction,
        size_pct=size_pct,
        efficiency=eff,
        atr_mult=atr_mult,
    )
    pb = _pullback_fraction(bars, draft)

    # Импульс не завершён — Fib рано (ещё рисуем ход, а не коррекция к нему)
    if pb < MIN_PULLBACK_OF_LEG:
        return None
    # Полный слом ноги — не строим Fib для входа по тренду
    if pb > MAX_PULLBACK_OF_LEG:
        return None

    # Качество 0–100
    q = 0
    q += min(35, int(size_pct * 6))           # размер
    q += int(eff * 30)                        # чистота
    q += min(20, int(atr_mult * 8))           # vs ATR
    # Идеальный откат для Fib — 0.25–0.70 ноги
    if 0.25 <= pb <= 0.70:
        q += 15
    elif 0.12 <= pb < 0.25 or 0.70 < pb <= 0.90:
        q += 6
    q -= int(against * 20)

    # Свежесть: конец ноги не слишком старый
    age = len(bars) - 1 - end_idx
    if age > 36:
        q -= 15
    elif age > 24:
        q -= 8

    q = max(0, min(100, q))
    if q < MIN_QUALITY_TO_USE:
        return None

    return ImpulseLeg(
        start_idx=start_idx,
        end_idx=end_idx,
        start_price=start_price,
        end_price=end_price,
        direction=direction,
        size_pct=round(size_pct, 3),
        efficiency=round(eff, 3),
        atr_mult=round(atr_mult, 3),
        pullback_frac=round(pb, 3),
        quality=q,
    )


def detect_impulse_leg(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
) -> ImpulseLeg | None:
    """Только качественная завершённая импульсная нога — иначе None."""
    if not bars or len(bars) < 12 or len(swings) < 2:
        return None

    atr_pct = _atr_pct(bars)
    window_start = max(0, len(bars) - RECENT_WINDOW_BARS)
    alt = _alternate_swings([s for s in swings if s.index >= window_start])
    if len(alt) < 2:
        return None

    scored: list[ImpulseLeg] = []
    # Перебираем пары swing low→high / high→low в окне
    for i in range(len(alt) - 1):
        for j in range(i + 1, min(i + 4, len(alt))):
            a, b = alt[i], alt[j]
            # Между A и B не должно быть более сильного экстремума того же типа,
            # иначе якорь неверный
            if a.kind == "low" and b.kind == "high" and b.price > a.price:
                # B должен быть max high между ними
                mid_highs = [s for s in alt[i : j + 1] if s.kind == "high"]
                if mid_highs and b.price < max(s.price for s in mid_highs) * 0.999:
                    continue
                leg = _score_leg(
                    bars, a.index, b.index, a.price, b.price, "up", atr_pct=atr_pct,
                )
                if leg:
                    scored.append(leg)
            elif a.kind == "high" and b.kind == "low" and b.price < a.price:
                mid_lows = [s for s in alt[i : j + 1] if s.kind == "low"]
                if mid_lows and b.price > min(s.price for s in mid_lows) * 1.001:
                    continue
                leg = _score_leg(
                    bars, a.index, b.index, a.price, b.price, "down", atr_pct=atr_pct,
                )
                if leg:
                    scored.append(leg)

    if not scored:
        return None

    # Лучшая нога: качество, затем свежесть
    scored.sort(key=lambda L: (L.quality, L.end_idx), reverse=True)
    return scored[0]


def build_fib_levels(leg: ImpulseLeg) -> list[FibLevel]:
    """Классика: ретрейсмент от B к A, extensions за B."""
    lo = min(leg.start_price, leg.end_price)
    hi = max(leg.start_price, leg.end_price)
    span = hi - lo
    if span <= 0:
        return []

    levels: list[FibLevel] = []
    for r in FIB_RETRACEMENT_RATIOS:
        if leg.direction == "up":
            price = hi - span * r
        else:
            price = lo + span * r
        levels.append(
            FibLevel(ratio=r, price=price, kind="retracement", label=f"Fib {r:g}")
        )

    for r in FIB_EXTENSION_RATIOS:
        if r <= 1.0:
            price = hi if leg.direction == "up" else lo
        elif leg.direction == "up":
            price = hi + span * (r - 1.0)
        else:
            price = lo - span * (r - 1.0)
        levels.append(
            FibLevel(ratio=r, price=price, kind="extension", label=f"Fib {r:g}")
        )
    return levels


def _fib_price(levels: list[FibLevel], ratio: float) -> float | None:
    for lv in levels:
        if abs(lv.ratio - ratio) < 1e-9:
            return lv.price
    return None


def classify_wave_phase(
    leg: ImpulseLeg,
    current: float,
    fib_levels: list[FibLevel],
    *,
    structure_label: str = "",
) -> tuple[str, str, int, list[str]]:
    notes: list[str] = []
    if current <= 0 or leg.range_size <= 0:
        return "unknown", "neutral", 0, notes

    f382 = _fib_price(fib_levels, 0.382)
    f500 = _fib_price(fib_levels, 0.5)
    f618 = _fib_price(fib_levels, 0.618)
    f786 = _fib_price(fib_levels, 0.786)
    # База confidence от качества ноги
    conf = max(4, min(9, leg.quality // 12))
    struct = (structure_label or "").lower()
    pb = leg.pullback_frac

    if leg.direction == "up":
        if current >= leg.end_price * 0.998:
            phase, bias = "late_impulse", "neutral"
            conf = min(conf, 5)
        elif f382 and current >= f382 and pb < 0.40:
            phase, bias = "shallow_pullback", "long"
            conf = min(9, conf + 1)
        elif f618 and f500 and f618 <= current <= (f382 or current):
            # Золотая зона 0.5–0.618
            phase, bias = "wave_2_4_zone", "long"
            conf = min(9, conf + 2)
        elif f786 and f618 and current < f618 and current >= f786:
            phase, bias = "deep_pullback", "long"
            conf = max(5, conf - 1)
            notes.append("глубокий Fib — риск слома импульса")
        elif current < leg.start_price * 1.001:
            phase, bias = "impulse_invalidated", "short"
            conf = 7
        else:
            phase, bias = "mid_correction", "long"
        if "lh" in struct and "ll" in struct and phase != "impulse_invalidated":
            conf = max(4, conf - 1)
    else:
        if current <= leg.end_price * 1.002:
            phase, bias = "late_impulse", "neutral"
            conf = min(conf, 5)
        elif f382 and current <= f382 and pb < 0.40:
            phase, bias = "shallow_pullback", "short"
            conf = min(9, conf + 1)
        elif f618 and f500 and (f382 or 0) <= current <= f618:
            phase, bias = "wave_2_4_zone", "short"
            conf = min(9, conf + 2)
        elif f786 and f618 and current > f618 and current <= f786:
            phase, bias = "deep_pullback", "short"
            conf = max(5, conf - 1)
            notes.append("глубокий Fib — риск слома импульса")
        elif current > leg.start_price * 0.999:
            phase, bias = "impulse_invalidated", "long"
            conf = 7
        else:
            phase, bias = "mid_correction", "short"
        if "hh" in struct and "hl" in struct and phase != "impulse_invalidated":
            conf = max(4, conf - 1)

    return phase, bias, conf, notes


def _near_pct(a: float, b: float, *, tol_pct: float = 0.35) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) * 100.0 <= tol_pct


def _round_levels_near(price: float) -> list[float]:
    """Психологические / круглые уровни вокруг цены."""
    if price <= 0:
        return []
    candidates: list[float] = []
    if price >= 1000:
        steps = (50.0, 100.0, 250.0)
    elif price >= 100:
        steps = (1.0, 5.0, 10.0)
    elif price >= 10:
        steps = (0.5, 1.0, 2.5)
    elif price >= 1:
        steps = (0.05, 0.1, 0.25, 0.5, 1.0)
    elif price >= 0.1:
        steps = (0.005, 0.01, 0.025, 0.05)
    elif price >= 0.01:
        steps = (0.0005, 0.001, 0.0025, 0.005)
    else:
        steps = (0.00005, 0.0001, 0.00025, 0.0005)

    for step in steps:
        base = round(price / step) * step
        for k in (-2, -1, 0, 1, 2):
            lvl = base + k * step
            if lvl > 0:
                candidates.append(lvl)
    # уникальные
    out: list[float] = []
    seen: set[float] = set()
    for c in candidates:
        key = round(c, 10)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def score_fib_confluence(
    fib_levels: list[FibLevel],
    *,
    current: float,
    sr_prices: list[float] | None = None,
    breakout: float | None = None,
    breakdown: float | None = None,
    direction: str = "up",
    tol_pct: float = 0.40,
) -> tuple[bool, bool, bool, int]:
    """Три главных сочетания Fib.

    1) с уровнями поддержки/сопротивления
    2) с круглыми уровнями
    3) с ретестом уровня пробоя после слома структуры

    Fib сам по себе — не сигнал; возвращает флаги confluence.
    """
    if not fib_levels or current <= 0:
        return False, False, False, 0

    # Рабочие Fib для confluence — откатные 0.382/0.5/0.618 (+ чуть 0.786)
    work = [lv for lv in fib_levels if lv.kind == "retracement" and lv.ratio in {0.382, 0.5, 0.618, 0.786}]
    if not work:
        work = [lv for lv in fib_levels if lv.kind == "retracement"]

    conf_sr = False
    conf_round = False
    conf_retest = False

    sr = [p for p in (sr_prices or []) if p and p > 0]
    rounds = _round_levels_near(current)

    for fl in work:
        for sp in sr:
            if _near_pct(fl.price, sp, tol_pct=tol_pct):
                conf_sr = True
                break
        if conf_sr:
            break

    for fl in work:
        for rp in rounds:
            if _near_pct(fl.price, rp, tol_pct=tol_pct * 0.9):
                conf_round = True
                break
        if conf_round:
            break

    # Ретест пробоя: Fib совпадает с бывшим сопротивлением (теперь поддержка) или наоборот
    if direction == "up" and breakout and breakout > 0:
        # после пробоя вверх цена ниже/около breakout = ретест; Fib у этого уровня
        if current <= breakout * 1.008:
            for fl in work:
                if _near_pct(fl.price, breakout, tol_pct=tol_pct):
                    conf_retest = True
                    break
    elif direction == "down" and breakdown and breakdown > 0:
        if current >= breakdown * 0.992:
            for fl in work:
                if _near_pct(fl.price, breakdown, tol_pct=tol_pct):
                    conf_retest = True
                    break

    count = int(conf_sr) + int(conf_round) + int(conf_retest)
    return conf_sr, conf_round, conf_retest, count


def _confluence_labels_ru(
    *,
    sr: bool,
    rnd: bool,
    retest: bool,
) -> list[str]:
    out: list[str] = []
    if sr:
        out.append("Fib + П/С")
    if rnd:
        out.append("Fib + круглый уровень")
    if retest:
        out.append("Fib + ретест пробоя")
    return out


@dataclass(frozen=True)
class AbcPattern:
    """Коррекция ABC после импульса (Эллиотт Lite)."""
    phase: str  # A | B | C | complete | forming
    label_ru: str
    a_price: float
    b_price: float
    c_price: float | None = None
    b_retrace_pct: float = 0.0  # откат B относительно ноги A


def detect_abc_pattern(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    leg: ImpulseLeg,
) -> AbcPattern | None:
    """3-волновая коррекция ABC после импульса A→B."""
    if not swings or not bars:
        return None
    alt = _alternate_swings(swings)
    post = [s for s in alt if s.index >= leg.end_idx]
    current = bars[-1].close
    if current <= 0:
        return None

    if leg.direction == "up":
        # Коррекция вниз: low (конец A), high (конец B), low (C)
        lows = [s for s in post if s.kind == "low"]
        highs = [s for s in post if s.kind == "high"]
        if not lows:
            if current < leg.end_price * 0.995:
                return AbcPattern(
                    phase="forming",
                    label_ru="формируется волна A коррекции",
                    a_price=leg.end_price,
                    b_price=current,
                )
            return None
        a_end = lows[0].price
        a_leg = leg.end_price - a_end
        if a_leg <= 0:
            return None
        if not highs:
            phase = "A" if current <= a_end * 1.003 else "forming"
            return AbcPattern(
                phase=phase,
                label_ru="волна A коррекции (откат от импульса)",
                a_price=leg.end_price,
                b_price=a_end,
                c_price=current if phase == "A" else None,
            )
        b_end = highs[0].price
        b_ret = (b_end - a_end) / a_leg if a_leg > 0 else 0.0
        c_lows = [s for s in lows[1:] if s.index > highs[0].index]
        if c_lows:
            c_end = c_lows[0].price
            return AbcPattern(
                phase="complete",
                label_ru=f"ABC завершена · B откатила {b_ret:.0%} ноги A",
                a_price=leg.end_price,
                b_price=b_end,
                c_price=c_end,
                b_retrace_pct=b_ret,
            )
        if current < b_end * 0.998:
            return AbcPattern(
                phase="C",
                label_ru=f"волна C коррекции · B @ {b_ret:.0%} A",
                a_price=leg.end_price,
                b_price=b_end,
                c_price=current,
                b_retrace_pct=b_ret,
            )
        return AbcPattern(
            phase="B",
            label_ru=f"волна B коррекции · откат {b_ret:.0%} ноги A",
            a_price=leg.end_price,
            b_price=b_end,
            c_price=None,
            b_retrace_pct=b_ret,
        )
    else:
        # Импульс вниз → коррекция вверх: high (A), low (B), high (C)
        highs = [s for s in post if s.kind == "high"]
        lows = [s for s in post if s.kind == "low"]
        if not highs:
            if current > leg.end_price * 1.005:
                return AbcPattern(
                    phase="forming",
                    label_ru="формируется волна A коррекции",
                    a_price=leg.end_price,
                    b_price=current,
                )
            return None
        a_end = highs[0].price
        a_leg = a_end - leg.end_price
        if a_leg <= 0:
            return None
        if not lows:
            phase = "A" if current >= a_end * 0.997 else "forming"
            return AbcPattern(
                phase=phase,
                label_ru="волна A коррекции (откат от импульса)",
                a_price=leg.end_price,
                b_price=a_end,
                c_price=current if phase == "A" else None,
            )
        b_end = lows[0].price
        b_ret = (a_end - b_end) / a_leg if a_leg > 0 else 0.0
        c_highs = [s for s in highs[1:] if s.index > lows[0].index]
        if c_highs:
            c_end = c_highs[0].price
            return AbcPattern(
                phase="complete",
                label_ru=f"ABC завершена · B откатила {b_ret:.0%} ноги A",
                a_price=leg.end_price,
                b_price=b_end,
                c_price=c_end,
                b_retrace_pct=b_ret,
            )
        if current > b_end * 1.002:
            return AbcPattern(
                phase="C",
                label_ru=f"волна C коррекции · B @ {b_ret:.0%} A",
                a_price=leg.end_price,
                b_price=b_end,
                c_price=current,
                b_retrace_pct=b_ret,
            )
        return AbcPattern(
            phase="B",
            label_ru=f"волна B коррекции · откат {b_ret:.0%} ноги A",
            a_price=leg.end_price,
            b_price=b_end,
            c_price=None,
            b_retrace_pct=b_ret,
        )


def _elliott_label_ru(
    leg: ImpulseLeg,
    phase: str,
    abc: AbcPattern | None,
) -> str:
    if abc and abc.phase in {"B", "C", "complete"}:
        if abc.phase == "B":
            return f"импульс → коррекция ABC (волна B)"
        if abc.phase == "C":
            return f"импульс → коррекция ABC (волна C — зона входа)"
        return "импульс → ABC завершена → ждать новый импульс"
    mapping = {
        "shallow_pullback": "импульс 1–3 · мелкий откат (волна 2/4)",
        "wave_2_4_zone": "импульс · золотая зона Fib 0.5–0.618 (волна 2/4)",
        "deep_pullback": "глубокий откат · риск слома структуры",
        "mid_correction": "коррекция внутри импульса",
        "late_impulse": "финал импульса (волна 5) — не входить вдогонку",
        "impulse_invalidated": "импульс сломан — смена bias",
    }
    base = mapping.get(phase, "структура волны")
    if leg.direction == "up":
        return f"бычий {base}"
    return f"медв. {base}"


def analyze_wave_structure(
    bars: list["KlineBar"],
    swings: list["SwingPoint"],
    *,
    structure_label: str = "",
    sr_prices: list[float] | None = None,
    breakout: float | None = None,
    breakdown: float | None = None,
) -> WaveStructureResult:
    """
    Fib — вспомогательный инструмент.
    Сначала ищем сильный импульс, потом проверяем 3 сочетания;
    без confluence Fib не двигает вход (только может быть на графике).
    """
    empty = WaveStructureResult()
    if not bars or len(bars) < 12:
        return empty

    leg = detect_impulse_leg(swings, bars)
    if leg is None:
        return empty

    fib_levels = build_fib_levels(leg)
    current = bars[-1].close
    phase, bias, conf, notes = classify_wave_phase(
        leg, current, fib_levels, structure_label=structure_label,
    )

    conf_sr, conf_round, conf_retest, conf_count = score_fib_confluence(
        fib_levels,
        current=current,
        sr_prices=sr_prices,
        breakout=breakout,
        breakdown=breakdown,
        direction=leg.direction,
    )
    abc = detect_abc_pattern(swings, bars, leg)
    elliott = _elliott_label_ru(leg, phase, abc)
    conf_labels = _confluence_labels_ru(sr=conf_sr, rnd=conf_round, retest=conf_retest)
    if conf_labels:
        notes = [f"confluence: {', '.join(conf_labels)}"] + notes
    if abc:
        notes = [abc.label_ru] + notes[:2]

    entry_hint: float | None = None
    stop_hint: float | None = None
    targets: list[float] = []

    f500 = _fib_price(fib_levels, 0.5)
    f618 = _fib_price(fib_levels, 0.618)
    f127 = _fib_price(fib_levels, 1.272)
    f161 = _fib_price(fib_levels, 1.618)

    # Подсказки цены — только если есть сочетание с сильным фактором
    if conf_count >= 1:
        if leg.direction == "up":
            if phase in {"shallow_pullback", "wave_2_4_zone"}:
                entry_hint = f618 if phase == "wave_2_4_zone" else (f500 or f618)
            elif phase == "deep_pullback":
                entry_hint = _fib_price(fib_levels, 0.786) or f618
            stop_hint = min(leg.start_price, _fib_price(fib_levels, 0.786) or leg.start_price) * 0.998
            if phase in {"shallow_pullback", "wave_2_4_zone", "deep_pullback", "mid_correction"}:
                if leg.end_price > current * 1.002:
                    targets.append(leg.end_price)
                if f127 and f127 > current:
                    targets.append(f127)
                if f161 and f161 > current and leg.quality >= 75 and conf_count >= 2:
                    targets.append(f161)
        else:
            if phase in {"shallow_pullback", "wave_2_4_zone"}:
                entry_hint = f618 if phase == "wave_2_4_zone" else (f500 or f618)
            elif phase == "deep_pullback":
                entry_hint = _fib_price(fib_levels, 0.786) or f618
            stop_hint = max(leg.start_price, _fib_price(fib_levels, 0.786) or leg.start_price) * 1.002
            if phase in {"shallow_pullback", "wave_2_4_zone", "deep_pullback", "mid_correction"}:
                if leg.end_price < current * 0.998:
                    targets.append(leg.end_price)
                if f127 and f127 < current:
                    targets.append(f127)
                if f161 and f161 < current and leg.quality >= 75 and conf_count >= 2:
                    targets.append(f161)

    uniq: list[float] = []
    seen: set[float] = set()
    for t in targets:
        key = round(t, 8)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)

    valid = leg.quality >= MIN_QUALITY_TO_USE and phase not in {"unknown", "late_impulse"}
    if phase == "late_impulse":
        valid = False

    # На график Fib можно при valid импульсе; на вход — только с confluence
    show_fib = valid or (leg.quality >= MIN_QUALITY_TO_USE and phase == "late_impulse")

    return WaveStructureResult(
        leg=leg,
        fib_levels=fib_levels if show_fib else [],
        wave_phase=phase,
        wave_bias=bias,
        confidence=conf if (valid and conf_count >= 1) else min(conf, 4),
        entry_hint_price=entry_hint if (valid and conf_count >= 1) else None,
        stop_hint_price=stop_hint if (valid and conf_count >= 1) else None,
        target_hint_prices=uniq[:3] if (valid and conf_count >= 1) else [],
        notes=notes[:3],
        valid=valid,
        confluence_sr=conf_sr,
        confluence_round=conf_round,
        confluence_retest=conf_retest,
        confluence_count=conf_count,
        elliott_label=elliott,
        abc_phase=abc.phase if abc else "",
        abc_label_ru=abc.label_ru if abc else "",
    )


def apply_wave_to_trade_plan(
    *,
    verdict: str,
    action_priority: str,
    current: float,
    inv: float | None,
    targets: list[float],
    breakout: float | None,
    breakdown: float | None,
    wave: WaveStructureResult,
) -> tuple[float | None, list[float], int, int]:
    """Fib не решает вход. Трогаем план только при valid + confluence."""
    if not wave.valid or not wave.has_confluence or wave.leg is None or current <= 0:
        return inv, targets, 0, 0

    new_inv = inv
    new_targets = list(targets)
    phase = wave.wave_phase

    if wave.stop_hint_price and new_inv:
        if verdict == "LONG" or action_priority == "long":
            if wave.stop_hint_price < current * 0.998:
                if wave.stop_hint_price > new_inv:
                    new_inv = wave.stop_hint_price
                elif abs(wave.stop_hint_price - new_inv) / current < 0.015:
                    new_inv = min(new_inv, wave.stop_hint_price)
        elif verdict == "SHORT" or action_priority == "short":
            if wave.stop_hint_price > current * 1.002:
                if wave.stop_hint_price < new_inv:
                    new_inv = wave.stop_hint_price
                elif abs(wave.stop_hint_price - new_inv) / current < 0.015:
                    new_inv = max(new_inv, wave.stop_hint_price)
    elif wave.stop_hint_price and new_inv is None:
        new_inv = wave.stop_hint_price

    side_long = verdict == "LONG" or (verdict == "WAIT" and action_priority == "long")
    side_short = verdict == "SHORT" or (verdict == "WAIT" and action_priority == "short")

    if phase in {"shallow_pullback", "wave_2_4_zone", "deep_pullback"}:
        for tp in wave.target_hint_prices:
            if side_long and tp > current * 1.003:
                new_targets.append(tp)
            elif side_short and tp < current * 0.997:
                new_targets.append(tp)

    uniq: list[float] = []
    seen: set[float] = set()
    for t in new_targets:
        key = round(t, 8)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    if side_long:
        uniq = sorted(uniq)
    elif side_short:
        uniq = sorted(uniq, reverse=True)

    _ = (breakout, breakdown)
    return new_inv, uniq[:4], 0, 0


def wave_flow_adjustments(
    wave: WaveStructureResult,
    *,
    action_priority: str,
) -> tuple[int, int]:
    """Мягкий bias только при confluence. Fib без сочетаний = 0."""
    if not wave.valid or not wave.has_confluence or wave.leg is None:
        return 0, 0
    phase = wave.wave_phase
    bias = wave.wave_bias
    # сила пропорциональна числу сочетаний (1→база, 2–3→чуть сильнее)
    mult = 1.0 + 0.35 * max(0, wave.confluence_count - 1)
    cont = 0
    corr = 0

    if bias == "long" and action_priority == "long":
        if phase == "wave_2_4_zone":
            cont += int(8 * mult)
        elif phase == "shallow_pullback":
            cont += int(5 * mult)
        elif phase == "deep_pullback":
            cont += 2
            corr += 3
        elif phase == "impulse_invalidated":
            corr += 8
    elif bias == "short" and action_priority == "short":
        if phase == "wave_2_4_zone":
            cont += int(8 * mult)
        elif phase == "shallow_pullback":
            cont += int(5 * mult)
        elif phase == "deep_pullback":
            cont += 2
            corr += 3
        elif phase == "impulse_invalidated":
            corr += 8
    elif bias == "long" and action_priority == "short":
        if phase in {"shallow_pullback", "wave_2_4_zone"}:
            corr += int(5 * mult)
    elif bias == "short" and action_priority == "long":
        if phase in {"shallow_pullback", "wave_2_4_zone"}:
            corr += int(5 * mult)

    return cont, corr
