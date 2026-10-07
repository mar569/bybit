"""SMC чеклист PDF: свип → BOS/MSS → Fib ~0.71 → OB в зоне."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .fib_entry_rules import FibEntryPlan
from .zone_model import TradingZone, zones_near_price


@dataclass(frozen=True)
class SmcPdfChecklist:
    sweep: bool
    structure_break: bool
    break_kind: str  # bos | mss | none
    fib_in_zone: bool
    ob_in_zone: bool
    score: int
    ready: bool
    label_ru: str

    def to_present_lines(self) -> list[str]:
        lines: list[str] = []
        if self.sweep:
            lines.append("свип ликвидности")
        if self.structure_break:
            lines.append(f"{self.break_kind.upper()} структуры")
        if self.fib_in_zone:
            lines.append("Fib в рабочей зоне")
        if self.ob_in_zone:
            lines.append("OB/breaker в confluence")
        if self.ready:
            lines.append("SMC-чеклист PDF ✓")
        return lines

    def to_absent_lines(self) -> list[str]:
        missing: list[str] = []
        if not self.sweep:
            missing.append("нет свипа")
        if not self.structure_break:
            missing.append("нет BOS/MSS")
        if not self.fib_in_zone:
            missing.append("Fib вне зоны входа")
        if not self.ob_in_zone:
            missing.append("нет OB в зоне")
        return missing


def _infer_break_kind(smc: object, htf_structure: str) -> str:
    if smc is None or not getattr(smc, "structure_break", False):
        return "none"
    direction = getattr(smc, "structure_break_direction", "none")
    htf = (htf_structure or getattr(smc, "htf_structure", "") or "").lower()
    if direction == "long" and htf == "bearish":
        return "mss"
    if direction == "short" and htf == "bullish":
        return "mss"
    if getattr(smc, "liquidity_sweep", False):
        return "mss"
    return "bos"


def evaluate_smc_pdf_checklist(
    *,
    smc: object | None,
    fib: FibEntryPlan,
    zones: Sequence[TradingZone],
    current: float,
    htf_structure: str = "",
) -> SmcPdfChecklist:
    sweep = bool(smc and getattr(smc, "liquidity_sweep", False))
    brk = bool(smc and getattr(smc, "structure_break", False))
    kind = _infer_break_kind(smc, htf_structure) if brk else "none"
    fib_ok = fib.in_preferred_zone
    ob_ok = fib.near_ob or bool(
        zones_near_price(
            [z for z in zones if z.kind.startswith("ob_") or z.kind.startswith("breaker_")],
            current,
            pct=2.5,
        )
    )
    score = sum((sweep, brk, fib_ok, ob_ok))
    ready = score >= 3 and fib_ok and (ob_ok or sweep)
    parts = []
    if ready:
        parts.append("готов к лимиту по PDF")
    else:
        parts.append(f"чеклист {score}/4")
    return SmcPdfChecklist(
        sweep=sweep,
        structure_break=brk,
        break_kind=kind,
        fib_in_zone=fib_ok,
        ob_in_zone=ob_ok,
        score=score,
        ready=ready,
        label_ru=" · ".join(parts),
    )
