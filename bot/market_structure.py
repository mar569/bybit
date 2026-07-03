from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .bybit_klines import KlineBar


@dataclass
class FiveMinOiBar:
    open_time: float
    oi_open: float
    oi_high: float
    oi_low: float
    oi_close: float
    price_close: float
    samples: int = 0


@dataclass
class MarketStructureContext:
    phase: str
    phase_label: str
    phase_detail: str
    phase_strength: float
    oi_narrative: str
    oi_narrative_label: str
    oi_context_strength: float
    price_changes: dict[int, float] = field(default_factory=dict)
    oi_changes: dict[int, float] = field(default_factory=dict)
    hours_analyzed: int = 5
    bar_count: int = 0
    oi_bar_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "phase_label": self.phase_label,
            "phase_detail": self.phase_detail,
            "phase_strength": round(self.phase_strength, 3),
            "oi_narrative": self.oi_narrative,
            "oi_narrative_label": self.oi_narrative_label,
            "oi_context_strength": round(self.oi_context_strength, 3),
            "price_changes": {str(k): round(v, 2) for k, v in self.price_changes.items()},
            "oi_changes": {str(k): round(v, 2) for k, v in self.oi_changes.items()},
            "hours_analyzed": self.hours_analyzed,
            "bar_count": self.bar_count,
            "oi_bar_count": self.oi_bar_count,
        }


PHASE_LABELS: dict[str, str] = {
    "impulse_up": "Импульс вверх",
    "impulse_down": "Импульс вниз",
    "consolidation": "Боковик после движения",
    "correction_down": "Коррекция вниз",
    "correction_up": "Коррекция вверх",
    "breakout_setup": "Сжатие → пробой",
    "neutral": "Без явной фазы",
}

NARRATIVE_LABELS: dict[str, str] = {
    "accumulation": "Накопление (OI↑, цена flat)",
    "aligned_long": "Лонги набирают (OI↑ + цена↑)",
    "aligned_short": "Шорты набирают (OI↑ + цена↓)",
    "squeeze_risk": "Шорты закрывают (цена↑, OI↓)",
    "long_unwind": "Лонги закрывают (цена↓, OI↓)",
    "shorts_building": "Шорты в росте (цена↓, OI↑)",
    "capitulation": "Капитуляция (цена↓, OI↓↓)",
    "mixed": "Смешанный поток",
    "insufficient_oi": "Мало данных OI",
}

BAR_MINUTES = 5


def bar_open_time(timestamp: float, interval_seconds: int = 300) -> float:
    return math.floor(timestamp / interval_seconds) * interval_seconds


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _price_change_bars(bars: list[KlineBar], n_bars: int) -> float | None:
    if len(bars) < n_bars + 1:
        return None
    old_close = bars[-n_bars - 1].close
    new_close = bars[-1].close
    if old_close <= 0:
        return None
    return (new_close - old_close) / old_close * 100.0


def _range_percent(bars: list[KlineBar], n_bars: int) -> float | None:
    if len(bars) < n_bars:
        return None
    segment = bars[-n_bars:]
    high = max(bar.high for bar in segment)
    low = min(bar.low for bar in segment)
    mid = (high + low) / 2.0
    if mid <= 0:
        return None
    return (high - low) / mid * 100.0


def _oi_change_bars(oi_bars: list[FiveMinOiBar], n_bars: int) -> float | None:
    if len(oi_bars) < n_bars + 1:
        return None
    old_oi = oi_bars[-n_bars - 1].oi_close
    new_oi = oi_bars[-1].oi_close
    if old_oi <= 0:
        return None
    return (new_oi - old_oi) / old_oi * 100.0


def _detect_phase(chg_1h: float | None, chg_2h: float | None, chg_5h: float | None,
                  range_1h: float | None, range_2h: float | None) -> tuple[str, str]:
    c1 = chg_1h or 0.0
    c2 = chg_2h or 0.0
    c5 = chg_5h or 0.0
    r1 = range_1h if range_1h is not None else 99.0
    r2 = range_2h if range_2h is not None else 99.0

    if r2 < 2.5 and abs(c5) > 2.0:
        return "consolidation", "узкий диапазон 2ч при заметном движении за 5ч"
    if c5 > 2.5 and c1 < -1.2:
        return "correction_down", "откат после роста"
    if c5 < -2.5 and c1 > 1.2:
        return "correction_up", "откат после падения"
    if r1 < 2.2 and abs(c1) > 1.8:
        return "breakout_setup", "сжатие волатильности + импульс"
    if c1 > 3.5:
        return "impulse_up", f"рост {c1:+.1f}% за 1ч"
    if c1 < -3.5:
        return "impulse_down", f"падение {c1:+.1f}% за 1ч"
    if abs(c2) < 1.0 and r2 < 3.0:
        return "consolidation", "боковик 2ч"
    return "neutral", "нет доминирующей фазы"


def _detect_oi_narrative(price_2h: float | None, oi_2h: float | None,
                         price_5h: float | None, oi_5h: float | None) -> str:
    p2 = price_2h or 0.0
    o2 = oi_2h
    if o2 is None:
        return "insufficient_oi"
    if abs(p2) < 1.0 and o2 > 2.0:
        return "accumulation"
    if p2 > 1.5 and o2 > 1.5:
        return "aligned_long"
    if p2 < -1.5 and o2 > 1.5:
        return "aligned_short"
    if p2 > 1.5 and o2 < -1.0:
        return "squeeze_risk"
    if p2 < -1.5 and o2 < -1.5:
        return "long_unwind"
    if p2 < -1.0 and o2 > 2.0:
        return "shorts_building"
    if (price_5h or 0) < -3 and o2 < -3:
        return "capitulation"
    return "mixed"


def _phase_strength(phase: str, is_long: bool) -> tuple[float, str]:
    if is_long:
        table: dict[str, tuple[float, str]] = {
            "correction_down": (0.88, "откат в растущем контексте — хороший long"),
            "consolidation": (0.76, "боковик: позиции копятся перед пробоем"),
            "breakout_setup": (0.84, "сжатие перед разрывом вверх"),
            "impulse_up": (0.42, "уже в пампе — риск опоздания"),
            "impulse_down": (0.18, "даун-импульс против long"),
            "correction_up": (0.32, "отскок в падающем контексте"),
            "neutral": (0.50, "нейтральный фон"),
        }
    else:
        table = {
            "correction_up": (0.88, "откат в падающем контексте — хороший short"),
            "consolidation": (0.76, "боковик: накопление перед дампом"),
            "breakout_setup": (0.84, "сжатие перед разрывом вниз"),
            "impulse_down": (0.42, "уже в дампе — риск опоздания"),
            "impulse_up": (0.18, "ап-импульс против short"),
            "correction_down": (0.32, "откат в растущем контексте"),
            "neutral": (0.50, "нейтральный фон"),
        }
    return table.get(phase, (0.50, ""))


def _oi_context_strength(narrative: str, is_long: bool) -> float:
    if is_long:
        scores = {
            "accumulation": 0.92,
            "aligned_long": 0.86,
            "squeeze_risk": 0.72,
            "shorts_building": 0.22,
            "aligned_short": 0.18,
            "long_unwind": 0.28,
            "capitulation": 0.35,
            "mixed": 0.50,
            "insufficient_oi": 0.45,
        }
    else:
        scores = {
            "accumulation": 0.55,
            "aligned_short": 0.86,
            "shorts_building": 0.82,
            "aligned_long": 0.18,
            "squeeze_risk": 0.38,
            "long_unwind": 0.70,
            "capitulation": 0.78,
            "mixed": 0.50,
            "insufficient_oi": 0.45,
        }
    return scores.get(narrative, 0.50)


def analyze_market_structure(
    klines: list[KlineBar],
    oi_bars: list[FiveMinOiBar],
    *,
    is_long: bool,
    hours: int = 5,
) -> MarketStructureContext:
    hours = max(1, min(hours, 6))
    max_bars = hours * (60 // BAR_MINUTES)

    if len(klines) < 12:
        return MarketStructureContext(
            phase="neutral",
            phase_label=PHASE_LABELS["neutral"],
            phase_detail="мало свечей",
            phase_strength=0.45,
            oi_narrative="insufficient_oi",
            oi_narrative_label=NARRATIVE_LABELS["insufficient_oi"],
            oi_context_strength=0.45,
            hours_analyzed=hours,
            bar_count=len(klines),
            oi_bar_count=len(oi_bars),
        )

    klines = klines[-max_bars:]
    oi_bars = oi_bars[-max_bars:]

    price_changes: dict[int, float] = {}
    oi_changes: dict[int, float] = {}
    for h in range(1, hours + 1):
        n = h * (60 // BAR_MINUTES)
        pc = _price_change_bars(klines, n)
        if pc is not None:
            price_changes[h] = pc
        oc = _oi_change_bars(oi_bars, n)
        if oc is not None:
            oi_changes[h] = oc

    chg_1h = price_changes.get(1)
    chg_2h = price_changes.get(2)
    chg_5h = price_changes.get(hours) or price_changes.get(max(price_changes))
    range_1h = _range_percent(klines, 12)
    range_2h = _range_percent(klines, 24)

    phase, phase_detail = _detect_phase(chg_1h, chg_2h, chg_5h, range_1h, range_2h)
    phase_str, phase_hint = _phase_strength(phase, is_long)

    oi_2h = oi_changes.get(2)
    oi_5h = oi_changes.get(hours) or oi_changes.get(max(oi_changes) if oi_changes else 0)
    narrative = _detect_oi_narrative(chg_2h, oi_2h, chg_5h, oi_5h)
    oi_str = _oi_context_strength(narrative, is_long)

    return MarketStructureContext(
        phase=phase,
        phase_label=PHASE_LABELS.get(phase, phase),
        phase_detail=phase_hint or phase_detail,
        phase_strength=phase_str,
        oi_narrative=narrative,
        oi_narrative_label=NARRATIVE_LABELS.get(narrative, narrative),
        oi_context_strength=oi_str,
        price_changes=price_changes,
        oi_changes=oi_changes,
        hours_analyzed=hours,
        bar_count=len(klines),
        oi_bar_count=len(oi_bars),
    )


def format_market_structure_block(data: dict[str, Any] | None) -> str:
    if not data:
        return ""

    hours = int(data.get("hours_analyzed", 5))
    lines = [f"📐 <b>Контекст {hours}ч (Bybit)</b>"]
    lines.append(f"Фаза: <b>{data.get('phase_label', '—')}</b>")
    if data.get("phase_detail"):
        lines.append(f"<i>{data['phase_detail']}</i>")

    price_parts = []
    for h in (1, 2, 3, 5):
        if h > hours:
            continue
        val = data.get("price_changes", {}).get(str(h))
        if val is not None:
            price_parts.append(f"{h}ч {float(val):+.1f}%")
    if price_parts:
        lines.append("Цена: " + " | ".join(price_parts))

    oi_parts = []
    for h in (2, 3, 5):
        if h > hours:
            continue
        val = data.get("oi_changes", {}).get(str(h))
        if val is not None:
            oi_parts.append(f"{h}ч {float(val):+.1f}%")
    narrative = data.get("oi_narrative_label", "")
    if oi_parts:
        lines.append("OI: " + " | ".join(oi_parts))
    if narrative and narrative != NARRATIVE_LABELS["insufficient_oi"]:
        lines.append(f"Позиции: <i>{narrative}</i>")

    return "\n".join(lines)
