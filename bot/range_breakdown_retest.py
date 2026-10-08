"""Playbook: боковик → закреп под полом → retest → цель у swing low (AAVE-логика)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .bybit_klines import KlineBar
from .ta_analysis import fmt_price


@dataclass
class RangeBreakdownRetestSetup:
    direction: str
    range_top: float
    range_bottom: float
    phase: str
    entry_lo: float
    entry_hi: float
    stop: float
    targets: list[float]
    swing_low_target: float
    label_ru: str
    story_ru: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["targets"] = list(self.targets)
        return d


def _swing_low_target(bars: list[KlineBar], below: float, *, lookback: int = 140) -> float:
    if not bars or below <= 0:
        return below * 0.9
    seg = bars[-min(lookback, len(bars)) :]
    lows = sorted({float(b.low) for b in seg if float(b.low) < below * 0.997})
    if not lows:
        return below * (1.0 - 0.08)
    # ближайший значимый минимум ниже пола, но не микро-шум
    for lv in lows:
        if (below - lv) / below >= 0.012:
            return lv
    return lows[0]


def _recent_close_below(bars: list[KlineBar], level: float, *, n: int = 10) -> bool:
    if not bars or level <= 0:
        return False
    tail = bars[-min(n, len(bars)) :]
    return any(float(b.close) < level * 0.9985 for b in tail)


def _retest_short_stop(
    bars: list[KlineBar],
    *,
    floor: float,
    ceil: float,
    current: float,
) -> float:
    """Стоп над retest / локальным хаем — не у далёкого потолка H4-боковика."""
    _ = ceil
    seg = bars[-min(24, len(bars)) :] if bars else []
    local_hi = max((float(b.high) for b in seg), default=current)
    stop = max(floor * 1.008, local_hi * 1.0012, current * 1.004)
    return min(stop, floor * 1.026)


def _setup_plan_viable(setup: RangeBreakdownRetestSetup, current: float) -> bool:
    ref = (setup.entry_lo + setup.entry_hi) / 2.0
    if ref <= 0 or not setup.targets:
        return False
    tp = float(setup.targets[0])
    risk = abs(float(setup.stop) - ref) / ref
    reward = abs(ref - tp) / ref if setup.direction == "short" else abs(tp - ref) / ref
    if risk <= 0.0001:
        return False
    if reward < 0.0075:
        return False
    min_rr = 0.55 if setup.phase == "fade_top" else 0.75
    return reward / risk >= min_rr


def evaluate_range_breakdown_retest(
    bars: list[KlineBar],
    *,
    consolidation: object | None,
    breakdown: float | None,
    breakout: float | None,
    post_pump: bool,
    current: float,
    repeat_spike_dump_risk: bool = False,
) -> RangeBreakdownRetestSetup | None:
    if not bars or current <= 0:
        return None
    if consolidation is None:
        return None
    top = float(getattr(consolidation, "top", 0))
    bot = float(getattr(consolidation, "bottom", 0))
    if top <= bot or (top - bot) / current < 0.004:
        return None
    floor = float(breakdown) if breakdown and breakdown > 0 else bot
    ceil = float(breakout) if breakout and breakout > top * 0.998 else top
    width = ceil - floor
    if width <= 0:
        return None
    pos = (current - floor) / width
    swing_tp = _swing_low_target(bars, floor)
    broke = _recent_close_below(bars, floor)
    near_retest = broke and floor * 0.992 <= current <= floor * 1.018

    if repeat_spike_dump_risk and pos <= 0.25:
        return None

    if near_retest:
        phase = "retest"
        entry_lo, entry_hi = floor * 0.996, floor * 1.01
        stop = _retest_short_stop(bars, floor=floor, ceil=ceil, current=current)
        targets = [swing_tp]
        if swing_tp > floor * 0.95:
            targets.append(floor * (1.0 - max(0.03, (floor - swing_tp) / floor)))
        label = "пробой пола → retest → шорт"
        story = (
            f"Закрепились под {fmt_price(floor)} — retest пола как сопр.; "
            f"цель у минимума ≈ {fmt_price(swing_tp)}."
        )
    elif pos >= 0.62 and not broke:
        phase = "fade_top"
        entry_lo, entry_hi = ceil * 0.982, ceil * 1.006
        stop = max(ceil * 1.012, current * 1.015)
        targets = [floor, swing_tp]
        label = "шорт от верха боковика"
        story = (
            f"Консолидация {fmt_price(floor)}–{fmt_price(ceil)}; "
            f"аккуратный шорт у {fmt_price(ceil)} — цели {fmt_price(floor)} и {fmt_price(swing_tp)} "
            f"после закрепа под {fmt_price(floor)}."
        )
    elif current < floor * 0.998:
        phase = "broken"
        entry_lo, entry_hi = current * 0.997, min(current * 1.006, floor * 1.008)
        stop = _retest_short_stop(bars, floor=floor, ceil=ceil, current=current)
        targets = [swing_tp]
        label = "пробой состоялся — шорт по откату"
        story = (
            f"Цена под полом {fmt_price(floor)}; цель — зона {fmt_price(swing_tp)}."
        )
    else:
        phase = "await_break"
        entry_lo, entry_hi = ceil * 0.985, ceil * 1.008
        stop = ceil * 1.022
        targets = [floor, swing_tp]
        label = "ждём закреп под полом"
        story = (
            f"Боковик {fmt_price(floor)}–{fmt_price(ceil)}; "
            f"шорт после закрепа под {fmt_price(floor)}, цель ≈ {fmt_price(swing_tp)}."
        )

    if post_pump and "памп" not in story:
        story = f"После импульса: {story}"

    ref_px = (entry_lo + entry_hi) / 2.0
    if targets and ref_px > 0:
        tp0 = float(targets[0])
        if tp0 > 0 and abs(ref_px - tp0) / ref_px < 0.012:
            targets[0] = ref_px * 0.988

    setup = RangeBreakdownRetestSetup(
        direction="short",
        range_top=ceil,
        range_bottom=floor,
        phase=phase,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        targets=[t for t in targets if t > 0][:3],
        swing_low_target=swing_tp,
        label_ru=label,
        story_ru=story,
    )
    if not _setup_plan_viable(setup, current):
        return None
    return setup


def get_rbr_from_ta(ta: object) -> dict[str, Any] | None:
    metrics = getattr(ta, "market_metrics", None) or {}
    if not isinstance(metrics, dict):
        return None
    raw = metrics.get("range_breakdown_retest")
    return raw if isinstance(raw, dict) else None


def rbr_alert_eligible(ta: object) -> bool:
    """Сетап RBR с планом — в алерт-канал WATCH (не только deep в анализах)."""
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return False
    return str(rbr.get("phase") or "") in {"fade_top", "await_break", "retest"}


def should_push_trader_deep_analysis(ta: object) -> bool:
    rbr = get_rbr_from_ta(ta)
    if rbr:
        return True
    if bool(getattr(ta, "repeat_spike_dump_risk", False)):
        return bool(getattr(ta, "consolidation", None) is not None)
    if bool(getattr(ta, "post_pump", False)) and getattr(ta, "consolidation", None) is not None:
        return True
    return False
