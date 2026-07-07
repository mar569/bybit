"""Smart Money + паттерн разворота (Shevelev): структура, BOS, дисконт, FVG, ликвидность."""
from __future__ import annotations

from dataclasses import dataclass, field

from .bybit_klines import KlineBar

if False:  # TYPE_CHECKING
    from .ta_analysis import SwingPoint


def _swing_points(bars: list[KlineBar], *, window: int = 2) -> list:
    from .ta_analysis import find_swing_points
    return find_swing_points(bars, window=window)


@dataclass(frozen=True)
class FairValueGap:
    start_idx: int
    end_idx: int
    top: float
    bottom: float
    direction: str  # bullish / bearish
    label: str


@dataclass(frozen=True)
class LiquidityLevel:
    price: float
    kind: str  # daily_high, daily_low, equal_highs, equal_lows, nearest_high, nearest_low
    label: str


@dataclass(frozen=True)
class SmcMarker:
    index: int
    price: float
    kind: str  # bos, sweep, expansion, fvg
    label: str
    direction: str  # long / short


@dataclass
class SmcContext:
    htf_structure: str = "unknown"
    htf_structure_label: str = ""
    ltf_structure: str = "unknown"
    ltf_structure_label: str = ""
    structure_break: bool = False
    structure_break_level: float | None = None
    structure_break_direction: str = "none"
    equilibrium_50: float | None = None
    discount_zone: tuple[float, float] | None = None
    premium_zone: tuple[float, float] | None = None
    discount_retrace: bool = False
    structure_expansion: bool = False
    expansion_level: float | None = None
    reversal_stage: str = "none"
    reversal_ready: bool = False
    reversal_direction: str = "none"
    liquidity_sweep: bool = False
    sweep_direction: str = "none"
    aligned_with_htf: bool = False
    fvgs: list[FairValueGap] = field(default_factory=list)
    liquidity_levels: list[LiquidityLevel] = field(default_factory=list)
    markers: list[SmcMarker] = field(default_factory=list)
    smc_score: int = 0
    checklist: list[tuple[str, bool]] = field(default_factory=list)
    stop_optimal: float | None = None
    stop_conservative: float | None = None
    summary: str = ""


def _structure_from_swings(swings: list) -> tuple[str, str]:
    if len(swings) < 4:
        return "unknown", "мало swing"
    highs = [s for s in swings[-6:] if s.kind == "high"]
    lows = [s for s in swings[-6:] if s.kind == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            return "bullish", "восходящая (HH+HL)"
        if lh and ll:
            return "bearish", "нисходящая (LH+LL)"
        if lh and hl:
            return "sideways", "сужение / флэт"
    return "sideways", "боковая"


def detect_fair_value_gaps(bars: list[KlineBar], *, lookback: int = 80) -> list[FairValueGap]:
    if len(bars) < 3:
        return []
    gaps: list[FairValueGap] = []
    start = max(2, len(bars) - lookback)
    for i in range(start, len(bars)):
        c0, c1, c2 = bars[i - 2], bars[i - 1], bars[i]
        if c0.high < c2.low:
            gaps.append(FairValueGap(
                start_idx=i - 2,
                end_idx=i,
                top=c2.low,
                bottom=c0.high,
                direction="bullish",
                label="FVG↑",
            ))
        elif c0.low > c2.high:
            gaps.append(FairValueGap(
                start_idx=i - 2,
                end_idx=i,
                top=c0.low,
                bottom=c2.high,
                direction="bearish",
                label="FVG↓",
            ))
    return gaps[-6:]


def detect_liquidity_levels(
    bars: list[KlineBar],
    swings: list,
    *,
    interval_minutes: int = 5,
) -> list[LiquidityLevel]:
    if not bars:
        return []
    levels: list[LiquidityLevel] = []
    bars_per_day = max(12, int(24 * 60 / max(interval_minutes, 1)))
    day_seg = bars[-min(bars_per_day, len(bars)):]
    day_high = max(b.high for b in day_seg)
    day_low = min(b.low for b in day_seg)
    levels.append(LiquidityLevel(day_high, "daily_high", "макс. дня"))
    levels.append(LiquidityLevel(day_low, "daily_low", "мин. дня"))

    week_seg = bars[-min(bars_per_day * 7, len(bars)):]
    if len(week_seg) > bars_per_day:
        levels.append(LiquidityLevel(max(b.high for b in week_seg), "weekly_high", "макс. недели"))
        levels.append(LiquidityLevel(min(b.low for b in week_seg), "weekly_low", "мин. недели"))

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if highs:
        levels.append(LiquidityLevel(highs[-1].price, "nearest_high", "ближ. макс."))
    if lows:
        levels.append(LiquidityLevel(lows[-1].price, "nearest_low", "ближ. мин."))

    ref = bars[-1].close
    tol = ref * 0.0015 if ref > 0 else 0.01
    for kind, points in (("equal_highs", highs), ("equal_lows", lows)):
        if len(points) < 2:
            continue
        cluster = [points[-1]]
        for p in reversed(points[:-1]):
            if abs(p.price - cluster[0].price) <= tol:
                cluster.append(p)
            else:
                break
        if len(cluster) >= 2:
            avg = sum(p.price for p in cluster) / len(cluster)
            label = "каскад макс." if kind == "equal_highs" else "каскад мин."
            levels.append(LiquidityLevel(avg, kind, label))
    return levels


def _detect_liquidity_sweep(
    bars: list[KlineBar],
    swings: list,
    levels: list[LiquidityLevel],
) -> tuple[bool, str, SmcMarker | None]:
    if len(bars) < 5 or not swings:
        return False, "none", None
    current = bars[-1]
    lookback = bars[-8:]
    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]
    if lows:
        ref_low = lows[-1].price
        swept = any(b.low < ref_low * 0.9995 for b in lookback[:-1])
        recovered = current.close > ref_low
        if swept and recovered:
            idx = len(bars) - 1
            return True, "long", SmcMarker(
                index=idx, price=ref_low, kind="sweep",
                label="свип↓", direction="long",
            )
    if highs:
        ref_high = highs[-1].price
        swept = any(b.high > ref_high * 1.0005 for b in lookback[:-1])
        recovered = current.close < ref_high
        if swept and recovered:
            idx = len(bars) - 1
            return True, "short", SmcMarker(
                index=idx, price=ref_high, kind="sweep",
                label="свип↑", direction="short",
            )
    for lv in levels:
        if lv.kind in {"daily_low", "weekly_low", "equal_lows", "nearest_low"}:
            swept = any(b.low < lv.price * 0.999 for b in lookback[:-1])
            if swept and current.close > lv.price:
                return True, "long", SmcMarker(
                    index=len(bars) - 1, price=lv.price, kind="sweep",
                    label="свип ликв.", direction="long",
                )
        if lv.kind in {"daily_high", "weekly_high", "equal_highs", "nearest_high"}:
            swept = any(b.high > lv.price * 1.001 for b in lookback[:-1])
            if swept and current.close < lv.price:
                return True, "short", SmcMarker(
                    index=len(bars) - 1, price=lv.price, kind="sweep",
                    label="свип ликв.", direction="short",
                )
    return False, "none", None


def _detect_reversal_pattern(
    bars: list[KlineBar],
    swings: list,
) -> dict:
    """Паттерн разворота: BOS → откат в дисконт → расширение структуры."""
    result: dict = {
        "direction": "none",
        "stage": "none",
        "ready": False,
        "bos_level": None,
        "impulse_high": None,
        "impulse_low": None,
        "equilibrium": None,
        "discount_zone": None,
        "premium_zone": None,
        "discount_retrace": False,
        "expansion": False,
        "expansion_level": None,
        "stop_optimal": None,
        "stop_conservative": None,
        "markers": [],
    }
    if len(bars) < 12 or len(swings) < 4:
        return result

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return result

    current = bars[-1].close

    # Bullish reversal: downtrend (LH+LL) → BOS above last LH before final LL
    last_low = lows[-1]
    prior_highs = [h for h in highs if h.index < last_low.index]
    if prior_highs:
        bos_level = prior_highs[-1].price
        abs_low = last_low.price
        if current > bos_level and abs_low < lows[-2].price if len(lows) >= 2 else True:
            impulse_high = max(b.high for b in bars[last_low.index:])
            rng = impulse_high - abs_low
            if rng > 0:
                eq = abs_low + rng * 0.5
                discount = (abs_low, eq)
                retrace_low = min(b.low for b in bars[last_low.index:])
                in_discount = retrace_low <= eq and retrace_low >= abs_low * 0.998
                expansion = current > impulse_high * 0.9995
                stage = "bos"
                if in_discount:
                    stage = "discount"
                if expansion:
                    stage = "expansion"
                ready = expansion and in_discount
                markers = []
                markers.append(SmcMarker(
                    index=prior_highs[-1].index, price=bos_level,
                    kind="bos", label="BOS↑", direction="long",
                ))
                if expansion:
                    markers.append(SmcMarker(
                        index=len(bars) - 1, price=impulse_high,
                        kind="expansion", label="расшир.", direction="long",
                    ))
                result.update({
                    "direction": "long",
                    "stage": stage,
                    "ready": ready,
                    "bos_level": bos_level,
                    "impulse_high": impulse_high,
                    "impulse_low": abs_low,
                    "equilibrium": eq,
                    "discount_zone": discount,
                    "discount_retrace": in_discount,
                    "expansion": expansion,
                    "expansion_level": impulse_high,
                    "stop_optimal": retrace_low,
                    "stop_conservative": abs_low,
                    "markers": markers,
                })
                return result

    # Bearish reversal: uptrend (HH+HL) → BOS below last HL before final HH
    last_high = highs[-1]
    prior_lows = [lo for lo in lows if lo.index < last_high.index]
    if prior_lows:
        bos_level = prior_lows[-1].price
        abs_high = last_high.price
        if current < bos_level:
            impulse_low = min(b.low for b in bars[last_high.index:])
            rng = abs_high - impulse_low
            if rng > 0:
                eq = impulse_low + rng * 0.5
                premium = (eq, abs_high)
                retrace_high = max(b.high for b in bars[last_high.index:])
                in_premium = retrace_high >= eq and retrace_high <= abs_high * 1.002
                expansion = current < impulse_low * 1.0005
                stage = "bos"
                if in_premium:
                    stage = "discount"
                if expansion:
                    stage = "expansion"
                ready = expansion and in_premium
                markers = [SmcMarker(
                    index=prior_lows[-1].index, price=bos_level,
                    kind="bos", label="BOS↓", direction="short",
                )]
                if expansion:
                    markers.append(SmcMarker(
                        index=len(bars) - 1, price=impulse_low,
                        kind="expansion", label="расшир.", direction="short",
                    ))
                result.update({
                    "direction": "short",
                    "stage": stage,
                    "ready": ready,
                    "bos_level": bos_level,
                    "impulse_high": abs_high,
                    "impulse_low": impulse_low,
                    "equilibrium": eq,
                    "premium_zone": premium,
                    "discount_retrace": in_premium,
                    "expansion": expansion,
                    "expansion_level": impulse_low,
                    "stop_optimal": retrace_high,
                    "stop_conservative": abs_high,
                    "markers": markers,
                })
    return result


def analyze_smc(
    bars: list[KlineBar],
    *,
    htf_bars: list[KlineBar] | None = None,
    swings: list | None = None,
    interval_minutes: int = 5,
) -> SmcContext:
    if not bars:
        return SmcContext()

    swings = swings or _swing_points(bars)
    ltf_struct, ltf_label = _structure_from_swings(swings)
    htf_struct, htf_label = ("unknown", "")
    if htf_bars:
        htf_swings = _swing_points(htf_bars, window=2)
        htf_struct, htf_label = _structure_from_swings(htf_swings)

    fvgs = detect_fair_value_gaps(bars)
    liq_levels = detect_liquidity_levels(bars, swings, interval_minutes=interval_minutes)
    sweep, sweep_dir, sweep_marker = _detect_liquidity_sweep(bars, swings, liq_levels)
    reversal = _detect_reversal_pattern(bars, swings)

    markers = list(reversal.get("markers", []))
    if sweep_marker:
        markers.append(sweep_marker)

    direction = reversal.get("direction", "none")
    bos = reversal.get("bos_level") is not None and reversal.get("stage") != "none"
    discount = bool(reversal.get("discount_retrace"))
    expansion = bool(reversal.get("expansion"))
    ready = bool(reversal.get("ready"))

    aligned = False
    if direction == "long" and htf_struct in {"bullish", "sideways"}:
        aligned = True
    elif direction == "short" and htf_struct in {"bearish", "sideways"}:
        aligned = True
    elif direction == "long" and htf_struct == "bearish":
        aligned = False
    elif direction == "short" and htf_struct == "bullish":
        aligned = False

    active_fvg = False
    current = bars[-1].close
    for gap in fvgs[-3:]:
        if gap.bottom <= current <= gap.top:
            active_fvg = True
            break

    checklist: list[tuple[str, bool]] = [
        ("Слом структуры (BOS)", bos),
        ("Откат в зону дисконта", discount),
        ("Расширение структуры", expansion),
        ("Свип ликвидности", sweep),
        ("Имбаланс (FVG) рядом", active_fvg or len(fvgs) > 0),
        ("HTF не против", aligned or htf_struct == "unknown"),
    ]

    score = 0
    if bos:
        score += 2
    if discount:
        score += 2
    if expansion:
        score += 3
    if sweep:
        score += 1
    if active_fvg:
        score += 1
    if aligned:
        score += 1
    if ready:
        score += 2
    score = min(score, 10)

    parts: list[str] = []
    if ready:
        parts.append(f"паттерн разворота {direction.upper()} готов")
    elif reversal.get("stage") != "none":
        parts.append(f"разворот {direction.upper()} · этап {reversal['stage']}")
    if sweep:
        parts.append(f"свип ликвидности ({sweep_dir})")
    if htf_struct != "unknown":
        parts.append(f"HTF: {htf_label}")

    return SmcContext(
        htf_structure=htf_struct,
        htf_structure_label=htf_label,
        ltf_structure=ltf_struct,
        ltf_structure_label=ltf_label,
        structure_break=bos,
        structure_break_level=reversal.get("bos_level"),
        structure_break_direction=direction if bos else "none",
        equilibrium_50=reversal.get("equilibrium"),
        discount_zone=reversal.get("discount_zone"),
        premium_zone=reversal.get("premium_zone"),
        discount_retrace=discount,
        structure_expansion=expansion,
        expansion_level=reversal.get("expansion_level"),
        reversal_stage=reversal.get("stage", "none"),
        reversal_ready=ready,
        reversal_direction=direction,
        liquidity_sweep=sweep,
        sweep_direction=sweep_dir,
        aligned_with_htf=aligned,
        fvgs=fvgs,
        liquidity_levels=liq_levels,
        markers=markers,
        smc_score=score,
        checklist=checklist,
        stop_optimal=reversal.get("stop_optimal"),
        stop_conservative=reversal.get("stop_conservative"),
        summary=" · ".join(parts) if parts else "SMC: без явного паттерна",
    )


def smc_checklist_yes_no(checklist: list[tuple[str, bool]]) -> list[str]:
    lines: list[str] = []
    for label, ok in checklist:
        mark = "да" if ok else "нет"
        lines.append(f"{'✅' if ok else '❌'} {label}: <b>{mark}</b>")
    return lines


def format_smc_compact_html(smc: SmcContext) -> str:
    if not smc.checklist:
        return ""
    lines = ["🧠 <b>SMC / разворот</b>"]
    lines.extend(smc_checklist_yes_no(smc.checklist))
    if smc.smc_score >= 7:
        lines.append(f"⚡ <b>Сетап сильный</b> {smc.smc_score}/10")
    elif smc.smc_score >= 4:
        lines.append(f"📊 Потенциал {smc.smc_score}/10")
    if smc.summary:
        lines.append(f"<i>{smc.summary}</i>")
    return "\n".join(lines)


def smc_verdict_boost(smc: SmcContext, *, is_long: bool) -> int:
    """Бонус к confidence вердикта (-3..+3)."""
    boost = 0
    if smc.reversal_ready:
        if (is_long and smc.reversal_direction == "long") or (
            not is_long and smc.reversal_direction == "short"
        ):
            boost += 3
        else:
            boost -= 2
    elif smc.structure_break:
        if (is_long and smc.structure_break_direction == "long") or (
            not is_long and smc.structure_break_direction == "short"
        ):
            boost += 1
        elif smc.structure_break_direction not in {"none", ""}:
            boost -= 1
    if smc.liquidity_sweep:
        if (is_long and smc.sweep_direction == "long") or (
            not is_long and smc.sweep_direction == "short"
        ):
            boost += 1
    if smc.aligned_with_htf:
        boost += 1
    elif smc.htf_structure not in {"unknown", "sideways"}:
        if (is_long and smc.htf_structure == "bearish") or (
            not is_long and smc.htf_structure == "bullish"
        ):
            boost -= 2
    return max(-3, min(3, boost))


def smc_to_dict(smc: SmcContext) -> dict:
    return {
        "smc_score": smc.smc_score,
        "reversal_ready": smc.reversal_ready,
        "reversal_direction": smc.reversal_direction,
        "reversal_stage": smc.reversal_stage,
        "structure_break": smc.structure_break,
        "structure_break_direction": smc.structure_break_direction,
        "discount_retrace": smc.discount_retrace,
        "structure_expansion": smc.structure_expansion,
        "liquidity_sweep": smc.liquidity_sweep,
        "sweep_direction": smc.sweep_direction,
        "aligned_with_htf": smc.aligned_with_htf,
        "htf_structure": smc.htf_structure,
        "summary": smc.summary,
    }


def smc_strength_from_dict(smc: dict | None, *, is_long: bool) -> float:
    if not smc:
        return 0.48
    score = float(smc.get("smc_score", 0)) / 10.0
    if smc.get("reversal_ready"):
        direction = smc.get("reversal_direction", "none")
        if (is_long and direction == "long") or (not is_long and direction == "short"):
            return min(1.0, 0.65 + score * 0.35)
        return max(0.0, 0.35 - score * 0.2)
    if smc.get("structure_break"):
        direction = smc.get("structure_break_direction", "none")
        if (is_long and direction == "long") or (not is_long and direction == "short"):
            return min(0.85, 0.5 + score * 0.3)
    return 0.45 + score * 0.25
