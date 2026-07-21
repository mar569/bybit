"""Профессиональный оркестратор сетапа: HTF Elliott + фигуры + Fib + SMC.

Цель — как на «золотом» примере:
  старший ТФ (1h): волны 1–5 / ABC / ABCDE / ending diagonal
  младший ТФ: точка входа только в сторону HTF
  confluence score → идеальный вход или WAIT

Не заменяет сканер: усиливает TA и gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .elliott_wave import (
    ElliottAbc,
    ElliottImpulse,
    ElliottPoint,
    ElliottWaveResult,
    analyze_elliott_waves,
    detect_elliott_abc,
    detect_elliott_impulse,
)

if TYPE_CHECKING:
    from .bybit_klines import KlineBar
    from .chart_pattern_models import ChartPattern
    from .smc_analysis import SmcContext
    from .ta_analysis import SwingPoint
    from .wave_structure import WaveStructureResult

# Пороги «идеального» входа
GRADE_A = 72
GRADE_B = 58
GRADE_C = 45


@dataclass
class ForecastWaypoint:
    price: float
    label: str  # entry | tp1 | tp2 | invalidation | path


@dataclass
class SetupConfluence:
    score: int = 0
    grade: str = "D"  # A|B|C|D
    side: str = "neutral"  # long|short|neutral
    label_ru: str = ""
    factors: list[str] = field(default_factory=list)
    # HTF
    htf_phase: str = ""
    htf_label_ru: str = ""
    htf_bias: str = "neutral"
    htf_quality: int = 0
    # LTF / структура
    ltf_phase: str = ""
    pattern_kind: str = ""
    # Идеальный вход
    ideal_ready: bool = False
    entry_price: float | None = None
    stop_price: float | None = None
    tp_prices: list[float] = field(default_factory=list)
    trigger: str = ""
    # Прогнозный путь (для графика)
    forecast_path: list[ForecastWaypoint] = field(default_factory=list)
    # спец. структуры
    is_ending_diagonal: bool = False
    is_abcde: bool = False
    htf_draw_points: list[ElliottPoint] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.grade in {"A", "B"} and self.side in {"long", "short"}


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


def detect_abcde_correction(
    swings: list["SwingPoint"],
    bars: list["KlineBar"],
    *,
    direction: str | None = None,
) -> ElliottAbc | None:
    """Коррекция A-B-C-D-E — сходящийся/расходящийся треугольник (PPT)."""
    from .elliott_advanced import detect_expanding_triangle, detect_horizontal_triangle

    tri = detect_horizontal_triangle(swings, bars, direction=direction)
    if tri is None:
        tri = detect_expanding_triangle(swings, bars, direction=direction)
    if tri and tri.valid and tri.points:
        return ElliottAbc(
            direction=tri.direction,
            points=list(tri.points),
            phase="complete",
            valid=True,
            corr_type="triangle",
            label_ru=tri.label_ru,
        )

    # fallback: старая эвристика схождения спанов
    if not swings or not bars or len(swings) < 6:
        return None
    alt = _alternate_swings(swings)[-10:]
    if len(alt) < 6:
        return None

    dirs = [direction] if direction in {"up", "down"} else ["down", "up"]
    best: ElliottAbc | None = None
    best_q = 0

    for d in dirs:
        if d == "down":
            expect = ["low", "high", "low", "high", "low"]
            labels = ["A", "B", "C", "D", "E"]
        else:
            expect = ["high", "low", "high", "low", "high"]
            labels = ["A", "B", "C", "D", "E"]

        for start in range(0, len(alt) - 4):
            points: list[ElliottPoint] | None = None
            if start + 6 <= len(alt):
                chunk = alt[start : start + 6]
                if all(chunk[i + 1].kind == expect[i] for i in range(5)):
                    points = [
                        ElliottPoint(labels[i], chunk[i + 1].index, chunk[i + 1].price)
                        for i in range(5)
                    ]
            if points is None and start + 5 <= len(alt):
                pts5 = alt[start : start + 5]
                if all(pts5[i].kind == expect[i] for i in range(5)):
                    points = [
                        ElliottPoint(labels[i], pts5[i].index, pts5[i].price)
                        for i in range(5)
                    ]
            if not points:
                continue

            spans = [abs(points[i + 1].price - points[i].price) for i in range(4)]
            converging = spans[-1] < spans[0] * 0.85 if spans[0] > 0 else False
            if d == "down":
                valid = points[4].price <= points[1].price * 1.02
            else:
                valid = points[4].price >= points[1].price * 0.98

            q = 50 + (20 if converging else 0) + (15 if valid else 0)
            if q > best_q:
                best_q = q
                best = ElliottAbc(
                    direction=d,
                    points=points,
                    phase="complete" if valid else "E",
                    valid=valid and converging,
                    corr_type="triangle",
                    label_ru=(
                        f"ABCDE {'↓' if d == 'down' else '↑'}"
                        + (" · схождение (клин)" if converging else "")
                    ),
                )
    return best if best_q >= 55 else None


def detect_ending_diagonal(
    impulse: ElliottImpulse | None,
    pattern: "ChartPattern | None" = None,
) -> bool:
    """Ending diagonal ≈ 5 волн внутри клина (волна 5 / complete + wedge)."""
    if impulse is None or not impulse.valid:
        return False
    if impulse.current_wave not in {"5", "complete"}:
        return False
    if pattern is None:
        # мягкий признак: волны 1–5 с сокращающимися ногами
        pts = impulse.points
        if len(pts) < 6:
            return False
        spans = [abs(pts[i + 1].price - pts[i].price) for i in range(5)]
        return spans[0] > 0 and spans[-1] < spans[0] * 0.75
    kind = (pattern.kind or "")
    return kind in {"wedge_rising", "wedge_falling", "triangle_symmetric", "triangle_descending", "triangle_ascending"}


def _grade(score: int) -> str:
    if score >= GRADE_A:
        return "A"
    if score >= GRADE_B:
        return "B"
    if score >= GRADE_C:
        return "C"
    return "D"


def _map_htf_points_to_ltf(
    htf_points: list[ElliottPoint],
    htf_bars: list["KlineBar"],
    ltf_bars: list["KlineBar"],
) -> list[ElliottPoint]:
    """Перенос точек HTF на индексы LTF по времени."""
    if not htf_points or not htf_bars or not ltf_bars:
        return []
    out: list[ElliottPoint] = []
    for p in htf_points:
        if p.index < 0 or p.index >= len(htf_bars):
            continue
        t = htf_bars[p.index].open_time
        # ближайший LTF бар
        best_i = 0
        best_d = abs(ltf_bars[0].open_time - t)
        for i, b in enumerate(ltf_bars):
            d = abs(b.open_time - t)
            if d < best_d:
                best_d = d
                best_i = i
        out.append(ElliottPoint(p.label, best_i, p.price))
    return out


def analyze_setup_confluence(
    bars: list["KlineBar"],
    swings: list["SwingPoint"],
    *,
    htf_bars: list["KlineBar"] | None = None,
    htf_swings: list["SwingPoint"] | None = None,
    wave: "WaveStructureResult | None" = None,
    ew_ltf: ElliottWaveResult | None = None,
    pattern: "ChartPattern | None" = None,
    smc: "SmcContext | None" = None,
    current: float | None = None,
) -> SetupConfluence:
    """Собрать HTF+LTF confluence и идеальный вход."""
    px = float(current or (bars[-1].close if bars else 0) or 0)
    result = SetupConfluence()
    if px <= 0 or not bars:
        return result

    score = 0
    factors: list[str] = []
    side_votes: dict[str, int] = {"long": 0, "short": 0}

    # --- LTF Elliott ---
    if ew_ltf is None:
        ew_ltf = analyze_elliott_waves(bars, swings)
    result.ltf_phase = ew_ltf.phase if ew_ltf else ""
    if ew_ltf and ew_ltf.has_structure:
        q = ew_ltf.confidence * 8
        score += min(28, q)
        factors.append(f"LTF EW: {ew_ltf.label_ru[:48]}")
        if ew_ltf.impulse:
            if ew_ltf.impulse.direction == "up":
                if ew_ltf.phase in {"impulse_2", "impulse_4", "abc_c", "abc_complete"}:
                    side_votes["long"] += 2
                elif ew_ltf.phase in {"impulse_5", "impulse_complete"}:
                    side_votes["short"] += 1  # риск разворота / не лонг вдогонку
                    factors.append("LTF волна 5/complete — не лонг вдогонку")
            else:
                if ew_ltf.phase in {"impulse_2", "impulse_4", "abc_c", "abc_complete"}:
                    side_votes["short"] += 2
                elif ew_ltf.phase in {"impulse_5", "impulse_complete"}:
                    side_votes["long"] += 1

        plan = ew_ltf.entry_plan
        if plan and plan.mode != "wait" and plan.entry_price:
            score += 8 if plan.ready else 4
            if plan.side in side_votes:
                side_votes[plan.side] += 2 if plan.ready else 1

    # --- HTF Elliott ---
    ew_htf: ElliottWaveResult | None = None
    if htf_bars and len(htf_bars) >= 24:
        from .ta_analysis import find_swing_points

        hs = htf_swings or find_swing_points(htf_bars, window=2)
        ew_htf = analyze_elliott_waves(htf_bars, hs)
        if ew_htf.has_structure:
            result.htf_phase = ew_htf.phase
            result.htf_label_ru = ew_htf.label_ru
            result.htf_quality = ew_htf.confidence
            result.htf_draw_points = _map_htf_points_to_ltf(
                list(ew_htf.draw_points), htf_bars, bars,
            )
            score += min(32, ew_htf.confidence * 9)
            factors.append(f"HTF EW: {ew_htf.label_ru[:48]}")
            if ew_htf.impulse:
                result.htf_bias = "long" if ew_htf.impulse.direction == "up" else "short"
                # После complete импульса вверх — ищем long от ABC; в конце 5 — осторожно
                if ew_htf.phase in {"abc_c", "abc_complete", "impulse_2", "impulse_4"}:
                    side_votes[result.htf_bias] += 3
                elif ew_htf.phase in {"impulse_5", "impulse_complete"}:
                    # конец импульса HTF → разворотный bias
                    opp = "short" if result.htf_bias == "long" else "long"
                    side_votes[opp] += 2
                    factors.append("HTF конец импульса → разворотный bias")
                    result.htf_bias = opp

        # ABCDE на HTF
        abcde = detect_abcde_correction(hs, htf_bars)
        if abcde and abcde.valid:
            result.is_abcde = True
            score += 14
            factors.append(abcde.label_ru)
            # после ABCDE down → long bias
            if abcde.direction == "down":
                side_votes["long"] += 2
            else:
                side_votes["short"] += 2

    # Ending diagonal (LTF impulse + wedge pattern)
    diag = detect_ending_diagonal(
        ew_ltf.impulse if ew_ltf else None,
        pattern,
    )
    if not diag and ew_htf:
        diag = detect_ending_diagonal(ew_htf.impulse, pattern)
    if diag:
        result.is_ending_diagonal = True
        score += 12
        factors.append("ending diagonal / клин+5 волн")
        # разворот против направления диагонали
        imp = (ew_ltf.impulse if ew_ltf and ew_ltf.impulse else None) or (
            ew_htf.impulse if ew_htf else None
        )
        if imp:
            side_votes["short" if imp.direction == "up" else "long"] += 2

    # --- Pattern ---
    if pattern is not None and getattr(pattern, "confidence", 0) >= 0.68:
        result.pattern_kind = pattern.kind
        conf_pts = int(pattern.confidence * 18)
        # Факты: confirmed + объём важнее «красивой геометрии»
        if pattern.status == "confirmed":
            conf_pts += 6
        if getattr(pattern, "volume_contracted", False):
            conf_pts += 4
            factors.append("объём сжался в фигуре")
        if getattr(pattern, "volume_breakout", False):
            conf_pts += 6
            factors.append("объём на пробое ↑")
        elif pattern.status == "confirmed" and pattern.kind in {
            "flag", "pennant", "triple_bottom", "triple_top", "wedge_rising", "wedge_falling",
        }:
            conf_pts -= 4
            factors.append("пробой без всплеска объёма — осторожно")
        score += conf_pts
        factors.append(f"фигура: {pattern.label_ru} ({pattern.confidence:.0%} · {pattern.status})")
        note = getattr(pattern, "psychology_note", "") or ""
        if note:
            factors.append(note[:48])
        if pattern.direction == "bullish":
            side_votes["long"] += 2 + (1 if pattern.status == "confirmed" else 0)
        elif pattern.direction == "bearish":
            side_votes["short"] += 2 + (1 if pattern.status == "confirmed" else 0)

    # --- Fib confluence ---
    if wave is not None and getattr(wave, "has_confluence", False):
        n = int(getattr(wave, "confluence_count", 0) or 0)
        score += min(18, 6 * max(1, n))
        factors.append(f"Fib confluence×{n}")
        bias = (getattr(wave, "wave_bias", "") or "").lower()
        if bias in side_votes:
            side_votes[bias] += 1
        phase = (getattr(wave, "wave_phase", "") or "")
        if phase in {"wave_2_4_zone", "shallow_pullback"}:
            score += 6

    # --- SMC ---
    if smc is not None:
        smc_score = int(getattr(smc, "smc_score", 0) or 0)
        if smc_score >= 4:
            score += min(16, smc_score * 3)
            factors.append(f"SMC {smc_score}")
            summary = (getattr(smc, "summary", "") or "").lower()
            if "long" in summary or "быч" in summary:
                side_votes["long"] += 1
            if "short" in summary or "медв" in summary:
                side_votes["short"] += 1

    # Align LTF with HTF (штраф за конфликт)
    if result.htf_bias in {"long", "short"}:
        if side_votes["long"] > side_votes["short"] and result.htf_bias == "short":
            score -= 12
            factors.append("штраф: LTF long против HTF")
        elif side_votes["short"] > side_votes["long"] and result.htf_bias == "long":
            score -= 12
            factors.append("штраф: LTF short против HTF")
        else:
            score += 8
            factors.append("HTF↔LTF согласованы")

    score = max(0, min(100, score))
    result.score = score
    result.grade = _grade(score)
    result.factors = factors[:8]

    if side_votes["long"] > side_votes["short"] + 0:
        result.side = "long"
    elif side_votes["short"] > side_votes["long"]:
        result.side = "short"
    else:
        result.side = result.htf_bias if result.htf_bias != "neutral" else "neutral"

    # --- Идеальный вход ---
    entry = stop = None
    tps: list[float] = []
    trigger = ""

    # Приоритет: EW plan в сторону confluence
    if ew_ltf and ew_ltf.entry_plan and ew_ltf.entry_plan.side == result.side:
        plan = ew_ltf.entry_plan
        entry, stop = plan.entry_price, plan.stop_price
        tps = [p for p in (plan.tp1, plan.tp2) if p]
        trigger = plan.trigger
        result.ideal_ready = bool(plan.ready) and result.grade in {"A", "B"}
    elif pattern and pattern.direction == (
        "bullish" if result.side == "long" else "bearish"
    ):
        if pattern.target_price:
            tps.append(pattern.target_price)
        if pattern.stop_price:
            stop = pattern.stop_price
        if pattern.neckline:
            entry = pattern.neckline.end_price
            trigger = f"пробой/ретест {pattern.label_ru}"
        result.ideal_ready = (
            result.grade in {"A", "B"}
            and pattern.status == "confirmed"
            and entry is not None
            and abs(px - entry) / px * 100.0 <= 0.8
        )
    elif wave and getattr(wave, "entry_hint_price", None) and result.side in {"long", "short"}:
        entry = wave.entry_hint_price
        stop = getattr(wave, "stop_hint_price", None)
        tps = list(getattr(wave, "target_hint_prices", None) or [])[:3]
        trigger = "Fib зона + confluence"
        result.ideal_ready = result.grade in {"A", "B"} and bool(
            getattr(wave, "has_confluence", False)
        )

    # Ending diagonal / ABCDE: вход от конца структуры
    if result.is_ending_diagonal or result.is_abcde:
        if result.side == "long" and entry is None:
            entry = px
            stop = px * 0.985
            tps = [px * 1.02, px * 1.035]
            trigger = "разворот от ABCDE/диагонали"
        elif result.side == "short" and entry is None:
            entry = px
            stop = px * 1.015
            tps = [px * 0.98, px * 0.965]
            trigger = "разворот от ABCDE/диагонали"
        if result.grade in {"A", "B"}:
            result.ideal_ready = result.ideal_ready or score >= GRADE_B

    result.entry_price = entry
    result.stop_price = stop
    result.tp_prices = tps[:3]
    result.trigger = trigger

    # Прогнозный путь (стрелка как на золоте)
    path: list[ForecastWaypoint] = []
    if entry:
        path.append(ForecastWaypoint(entry, "entry"))
    for i, tp in enumerate(tps[:2]):
        path.append(ForecastWaypoint(tp, f"tp{i + 1}"))
    if stop:
        path.append(ForecastWaypoint(stop, "invalidation"))
    # промежуточная точка пути
    if entry and tps:
        mid = entry + (tps[0] - entry) * 0.45
        path.insert(1, ForecastWaypoint(mid, "path"))
    result.forecast_path = path

    # Label
    grade = result.grade
    side_ru = {"long": "LONG", "short": "SHORT", "neutral": "WAIT"}.get(result.side, "WAIT")
    bits = [f"сетап {grade}", side_ru, f"{score}/100"]
    if result.htf_label_ru:
        bits.append(result.htf_label_ru[:40])
    if result.is_ending_diagonal:
        bits.append("диагональ")
    if result.is_abcde:
        bits.append("ABCDE")
    result.label_ru = " · ".join(bits)

    # Ideal ready только при согласованной стороне
    if result.side == "neutral" or result.grade == "D":
        result.ideal_ready = False

    return result


def confluence_boosts_gate(setup: SetupConfluence, side: str) -> tuple[int, list[str]]:
    """Бонус/штраф к setup score gate."""
    if setup.score <= 0:
        return 0, []
    notes: list[str] = []
    pts = 0
    if setup.side == side and setup.grade == "A":
        pts += 18
        notes.append(f"confluence A ({setup.score})")
    elif setup.side == side and setup.grade == "B":
        pts += 12
        notes.append(f"confluence B ({setup.score})")
    elif setup.side == side and setup.grade == "C":
        pts += 5
    elif setup.side in {"long", "short"} and setup.side != side and setup.grade in {"A", "B"}:
        pts -= 14
        notes.append("confluence против стороны")
    if setup.ideal_ready and setup.side == side:
        pts += 8
        notes.append("идеальный вход готов")
    if setup.htf_bias == side:
        pts += 4
    return pts, notes
