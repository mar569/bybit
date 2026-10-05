"""Confluence from higher-timeframe context, price patterns, Fib, and SMC."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bybit_klines import KlineBar
    from .chart_pattern_models import ChartPattern
    from .smc_analysis import SmcContext
    from .ta_analysis import SwingPoint
    from .wave_structure import WaveStructureResult

GRADE_A = 72
GRADE_B = 58
GRADE_C = 45


@dataclass
class ForecastWaypoint:
    price: float
    label: str


@dataclass
class SetupConfluence:
    score: int = 0
    grade: str = "D"
    side: str = "neutral"
    label_ru: str = ""
    factors: list[str] = field(default_factory=list)
    htf_phase: str = ""
    htf_label_ru: str = ""
    htf_bias: str = "neutral"
    htf_quality: int = 0
    ltf_phase: str = ""
    pattern_kind: str = ""
    ideal_ready: bool = False
    entry_price: float | None = None
    stop_price: float | None = None
    tp_prices: list[float] = field(default_factory=list)
    trigger: str = ""
    forecast_path: list[ForecastWaypoint] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.grade in {"A", "B"} and self.side in {"long", "short"}


def _grade(score: int) -> str:
    if score >= GRADE_A:
        return "A"
    if score >= GRADE_B:
        return "B"
    if score >= GRADE_C:
        return "C"
    return "D"


def analyze_setup_confluence(
    bars: list["KlineBar"],
    swings: list["SwingPoint"],
    *,
    htf_bars: list["KlineBar"] | None = None,
    htf_swings: list["SwingPoint"] | None = None,
    wave: "WaveStructureResult | None" = None,
    pattern: "ChartPattern | None" = None,
    htf_pattern: "ChartPattern | None" = None,
    smc: "SmcContext | None" = None,
    current: float | None = None,
) -> SetupConfluence:
    """Score aligned evidence; no single factor creates an unconditional entry."""
    del swings, htf_swings
    price = float(current or (bars[-1].close if bars else 0) or 0)
    result = SetupConfluence()
    if price <= 0 or not bars:
        return result

    score = 0
    factors: list[str] = []
    votes = {"long": 0, "short": 0}

    if htf_pattern is not None and htf_pattern.direction in {"bullish", "bearish"}:
        result.htf_bias = "long" if htf_pattern.direction == "bullish" else "short"
    elif htf_bars and len(htf_bars) >= 12:
        reference = htf_bars[-12].close
        change = (htf_bars[-1].close - reference) / reference * 100 if reference > 0 else 0
        if change >= 1.0:
            result.htf_bias = "long"
        elif change <= -1.0:
            result.htf_bias = "short"
    if result.htf_bias in votes:
        result.htf_label_ru = f"старший контекст {result.htf_bias.upper()}"
        result.htf_quality = 5
        score += 8
        votes[result.htf_bias] += 2
        factors.append(result.htf_label_ru)

    for candidate, higher in ((pattern, False), (htf_pattern, True)):
        if candidate is None or candidate.direction not in {"bullish", "bearish"}:
            continue
        confidence = float(getattr(candidate, "confidence", 0) or 0)
        if confidence < (0.62 if higher else 0.68):
            continue
        side = "long" if candidate.direction == "bullish" else "short"
        weight = 2 if higher else 2
        if candidate.status == "confirmed":
            weight += 1
        votes[side] += weight
        points = int(confidence * (10 if higher else 18))
        if candidate.status == "confirmed":
            points += 6
        if getattr(candidate, "volume_contracted", False):
            points += 4
            factors.append("объём сжимается в фигуре")
        if getattr(candidate, "volume_breakout", False):
            points += 6
            factors.append("пробой подтверждён объёмом")
        score += points
        kind = "HTF фигура" if higher else "фигура"
        factors.append(f"{kind}: {candidate.label_ru} ({confidence:.0%}, {candidate.status})")
        if not higher:
            result.pattern_kind = candidate.kind

    if (
        pattern is not None
        and htf_pattern is not None
        and pattern.direction in {"bullish", "bearish"}
        and htf_pattern.direction in {"bullish", "bearish"}
        and pattern.direction != htf_pattern.direction
    ):
        score -= 12
        factors.append("конфликт фигур LTF/HTF")

    if wave is not None and getattr(wave, "has_confluence", False):
        count = max(1, int(getattr(wave, "confluence_count", 0) or 0))
        score += min(18, 6 * count)
        factors.append(f"Fib confluence×{count}")

    if smc is not None:
        smc_score = int(getattr(smc, "smc_score", 0) or 0)
        if smc_score >= 4:
            score += min(16, smc_score * 3)
            factors.append(f"SMC {smc_score}")
        summary = (getattr(smc, "summary", "") or "").lower()
        if "long" in summary or "быч" in summary:
            votes["long"] += 1
        if "short" in summary or "медв" in summary:
            votes["short"] += 1

    if result.htf_bias in votes:
        other = "short" if result.htf_bias == "long" else "long"
        if votes[other] > votes[result.htf_bias]:
            score -= 12
            factors.append("LTF против старшего контекста")
        else:
            score += 4
            factors.append("LTF согласован со старшим контекстом")

    score = max(0, min(100, score))
    result.score = score
    result.grade = _grade(score)
    result.factors = factors[:8]
    if votes["long"] > votes["short"]:
        result.side = "long"
    elif votes["short"] > votes["long"]:
        result.side = "short"
    else:
        result.side = result.htf_bias

    entry = stop = None
    targets: list[float] = []
    trigger = ""
    if pattern and result.side in {"long", "short"}:
        expected = "bullish" if result.side == "long" else "bearish"
        if pattern.direction == expected:
            if pattern.target_price:
                targets.append(pattern.target_price)
            stop = pattern.stop_price
            if pattern.neckline:
                entry = pattern.neckline.end_price
                trigger = f"пробой/ретест {pattern.label_ru}"
            result.ideal_ready = (
                result.grade in {"A", "B"}
                and pattern.status == "confirmed"
                and entry is not None
                and abs(price - entry) / price * 100 <= 0.8
            )

    result.entry_price = entry
    result.stop_price = stop
    result.tp_prices = targets[:3]
    result.trigger = trigger
    path: list[ForecastWaypoint] = []
    if entry:
        path.append(ForecastWaypoint(entry, "entry"))
    for i, target in enumerate(targets[:2]):
        path.append(ForecastWaypoint(target, f"tp{i + 1}"))
    if stop:
        path.append(ForecastWaypoint(stop, "invalidation"))
    if entry and targets:
        path.insert(1, ForecastWaypoint(entry + (targets[0] - entry) * 0.45, "path"))
    result.forecast_path = path

    side_label = {"long": "LONG", "short": "SHORT", "neutral": "WAIT"}[result.side]
    result.label_ru = f"сетап {result.grade} · {side_label} · {score}/100"
    if result.htf_label_ru:
        result.label_ru += f" · {result.htf_label_ru}"
    if result.side == "neutral" or result.grade == "D":
        result.ideal_ready = False
    return result


def confluence_boosts_gate(setup: SetupConfluence, side: str) -> tuple[int, list[str]]:
    if setup.score <= 0:
        return 0, []
    notes: list[str] = []
    points = 0
    if setup.side == side and setup.grade == "A":
        points += 18
        notes.append(f"confluence A ({setup.score})")
    elif setup.side == side and setup.grade == "B":
        points += 12
        notes.append(f"confluence B ({setup.score})")
    elif setup.side == side and setup.grade == "C":
        points += 5
    elif setup.side in {"long", "short"} and setup.side != side and setup.grade in {"A", "B"}:
        points -= 14
        notes.append("confluence против стороны")
    if setup.ideal_ready and setup.side == side:
        points += 8
        notes.append("идеальный вход готов")
    if setup.htf_bias == side:
        points += 4
    return points, notes
