"""Модели графических фигур (без зависимостей от ta_analysis)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatternPoint:
    index: int
    price: float
    role: str


@dataclass(frozen=True)
class PatternLine:
    start_idx: int
    start_price: float
    end_idx: int
    end_price: float
    role: str


@dataclass(frozen=True)
class ChartPattern:
    kind: str
    subtype: str
    status: str
    points: tuple[PatternPoint, ...]
    lines: tuple[PatternLine, ...]
    zone_top: float | None
    zone_bottom: float | None
    neckline: PatternLine | None
    pole_height: float | None
    target_price: float | None
    stop_price: float | None
    confidence: float
    score_breakdown: dict[str, float]
    source_rule: str
    label_ru: str
    direction: str = "neutral"
