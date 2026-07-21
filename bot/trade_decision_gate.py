"""Единый алгоритм решения о сделке (ENTRY / WATCH / SKIP).

Сканер находит движения часто. Алгоритм решает:
- ENTRY — открывать позицию (уровень + структура + поток, не погоня)
- WATCH — монета интересна, план есть, ждать цену входа
- SKIP — шум / конфликт / плохой R:R

Скоринг 0–100: структура + локация + волны/Fib + поток − штрафы за погоню.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .chart_patterns import pattern_location_ok

if TYPE_CHECKING:
    from .models import Signal
    from .ta_analysis import TAAnalysisResult

_PULLBACK_PHASES = frozenset({
    "shallow_pullback", "wave_2_4_zone", "deep_pullback", "mid_correction",
})
_CHASE_TYPES = frozenset({
    "mega_pump", "mega_dump", "impulse_pump", "impulse_dump",
    "vertical_pump", "vertical_dump", "pulse_pump", "pulse_dump",
    "trend_pump", "trend_dump", "trend_seed", "price_pump", "price_dump",
})
_REVERSAL_TYPES = frozenset({"reversal_pump", "reversal_dump"})
_URGENT_DUMP_TYPES = frozenset({
    "vertical_dump", "mega_dump", "reversal_dump", "impulse_dump",
    "liq_cascade_dump", "trend_dump", "pulse_dump",
})
_URGENT_PUMP_TYPES = frozenset({
    "vertical_pump", "mega_pump", "reversal_pump", "impulse_pump",
    "liq_cascade_pump", "trend_pump",
})

# Пороги по умолчанию (settings v37)
DEFAULT_MIN_ENTRY_SCORE = 62
DEFAULT_MIN_WATCH_SCORE = 36
DEFAULT_CHASE_RANGE_HIGH_PCT = 82.0
DEFAULT_CHASE_RANGE_MID_PCT = 78.0


@dataclass(frozen=True)
class TradeDecision:
    action: str  # entry | watch | skip
    reason: str
    location: str = ""  # fib | sr | retest | trigger | abc | none
    chase: bool = False
    setup_score: int = 0


@dataclass(frozen=True)
class SetupScore:
    total: int
    structure: int
    location: int
    wave: int
    flow: int
    penalties: int
    location_kind: str
    factors: tuple[str, ...]


def _near_level(price: float, level: float | None, *, tol_pct: float = 0.55) -> bool:
    if not level or price <= 0 or level <= 0:
        return False
    return abs(price - level) / price * 100.0 <= tol_pct


def _is_late_chase(
    ta: TAAnalysisResult,
    side: str,
    *,
    range_high_pct: float = DEFAULT_CHASE_RANGE_HIGH_PCT,
    range_mid_pct: float = DEFAULT_CHASE_RANGE_MID_PCT,
) -> tuple[bool, str]:
    side = (side or "").lower()
    rp = float(getattr(ta, "range_position", 0.5) or 0.5)
    mom = float(getattr(ta, "momentum_pct", 0.0) or 0.0)
    phase = (getattr(ta, "wave_phase", "") or "").lower()
    ew_phase = (getattr(ta, "elliott_phase", "") or "").lower()
    fib_status = (getattr(ta, "fib_status", "") or "").lower()
    hi = max(0.70, min(float(range_high_pct) / 100.0, 0.95))
    mid = max(0.65, min(float(range_mid_pct) / 100.0, hi - 0.02))

    if phase == "late_impulse" or fib_status == "late_impulse":
        return True, "финал импульса — ждать откат"

    # EW 1–5 завершён / волна 5: не входить вдогонку по направлению импульса
    if ew_phase in {"impulse_5", "impulse_complete"}:
        ew_bias = (getattr(ta, "wave_bias", "") or "").lower()
        if side == "long" and ew_bias in {"long", "neutral", ""}:
            return True, "EW волна 5/complete — не лонг вдогонку"
        if side == "short" and ew_bias in {"short", "neutral", ""}:
            return True, "EW волна 5/complete — не шорт вдогонку"

    if side == "long":
        if rp >= hi and mom >= 0.35:
            return True, f"погоня лонга у хая ({rp:.0%})"
        if rp >= mid and mom >= 0.9:
            return True, f"импульс +{mom:.1f}% у вершины"
        if getattr(ta, "post_pump", False) and rp >= mid + 0.04 and mom >= 0.25:
            return True, "post-pump — ждать откат"
        # у хая после дампа/капитуляции — V-bounce ENTRY запрещён без отката
        if rp >= hi and mom > -0.15:
            return True, f"у хая диапазона ({rp:.0%}) — ждать откат"
    elif side == "short":
        lo = 1.0 - hi
        lo_mid = 1.0 - mid
        if rp <= lo and mom <= -0.35:
            return True, f"погоня шорта у лоя ({rp:.0%})"
        if rp <= lo_mid and mom <= -0.9:
            return True, f"импульс {mom:.1f}% у дна"
        if rp <= lo and mom < 0.15:
            return True, f"у лоя диапазона ({rp:.0%}) — ждать откат"
    return False, ""


def _fib_location_ok(ta: TAAnalysisResult, side: str) -> bool:
    if not bool(getattr(ta, "wave_has_confluence", False)):
        return False
    phase = (getattr(ta, "wave_phase", "") or "").lower()
    bias = (getattr(ta, "wave_bias", "") or "neutral").lower()
    if phase not in _PULLBACK_PHASES:
        return False
    if side == "long" and bias == "long":
        return True
    if side == "short" and bias == "short":
        return True
    return False


def _sr_location_ok(ta: TAAnalysisResult, side: str, *, tol_pct: float = 0.70) -> bool:
    px = float(getattr(ta, "current_price", 0) or 0)
    if px <= 0:
        return False
    if side == "long":
        if _near_level(px, getattr(ta, "nearest_support", None), tol_pct=tol_pct):
            return True
        dist = getattr(ta, "dist_to_long_pct", None)
        if dist is not None and 0 <= float(dist) <= tol_pct:
            return True
        ez = getattr(ta, "entry_zone", None)
        if ez and isinstance(ez, tuple) and len(ez) == 2:
            lo, hi = float(ez[0]), float(ez[1])
            if lo <= px <= hi * 1.006:
                return True
    else:
        if _near_level(px, getattr(ta, "nearest_resistance", None), tol_pct=tol_pct):
            return True
        dist = getattr(ta, "dist_to_short_pct", None)
        if dist is not None and 0 <= float(dist) <= tol_pct:
            return True
        ez = getattr(ta, "entry_zone", None)
        if ez and isinstance(ez, tuple) and len(ez) == 2:
            lo, hi = float(ez[0]), float(ez[1])
            if lo * 0.994 <= px <= hi:
                return True
    return False


def _retest_location_ok(ta: TAAnalysisResult, side: str, *, tol_pct: float = 0.75) -> bool:
    px = float(getattr(ta, "current_price", 0) or 0)
    if px <= 0:
        return False
    if side == "long":
        bo = getattr(ta, "breakout_level", None)
        if bo and px >= float(bo) * 0.991 and _near_level(px, float(bo), tol_pct=tol_pct):
            return True
    else:
        bd = getattr(ta, "breakdown_level", None)
        if bd and px <= float(bd) * 1.009 and _near_level(px, float(bd), tol_pct=tol_pct):
            return True
    return False


def _abc_entry_ok(ta: TAAnalysisResult, side: str) -> bool:
    phase = (getattr(ta, "abc_phase", "") or "").upper()
    if phase not in {"C", "COMPLETE"}:
        return False
    bias = (getattr(ta, "wave_bias", "") or "neutral").lower()
    if side == "long" and bias in {"long", "neutral"}:
        return True
    if side == "short" and bias in {"short", "neutral"}:
        return True
    return False


def _elliott_entry_ok(ta: TAAnalysisResult, side: str) -> bool:
    """Готовый вход по EW: чек-лист + классические Fib + ready."""
    if not bool(getattr(ta, "elliott_entry_ready", False)):
        return False
    mode = (getattr(ta, "elliott_entry_mode", "") or "").lower()
    if mode not in {"conservative", "aggressive"}:
        return False
    entry = getattr(ta, "elliott_entry_price", None)
    if entry is None or float(entry) <= 0:
        return False
    # Без классических пропорций Fib — не локация elliott
    if not bool(getattr(ta, "elliott_fib_classic_ok", True)):
        return False
    bias = (getattr(ta, "wave_bias", "") or "neutral").lower()
    if side == "long" and bias in {"long", "neutral"}:
        return True
    if side == "short" and bias in {"short", "neutral"}:
        return True
    if bias == "neutral" and mode in {"conservative", "aggressive"}:
        return True
    return False


def _confluence_location_ok(ta: TAAnalysisResult, side: str) -> bool:
    """Идеальный Pro-сетап (HTF EW + фигура + Fib/SMC) на стороне сигнала."""
    if not bool(getattr(ta, "setup_ideal_ready", False)):
        return False
    grade = (getattr(ta, "setup_grade", "") or "").upper()
    if grade not in {"A", "B"}:
        return False
    setup_side = (getattr(ta, "setup_side", "") or "").lower()
    if setup_side != side:
        return False
    entry = getattr(ta, "setup_entry", None)
    return entry is not None and float(entry) > 0


def detect_location(ta: TAAnalysisResult, side: str) -> str:
    if _fib_location_ok(ta, side):
        return "fib"
    if _elliott_entry_ok(ta, side):
        return "elliott"
    if _confluence_location_ok(ta, side):
        return "confluence"
    if _abc_entry_ok(ta, side):
        return "abc"
    if pattern_location_ok(
        getattr(ta, "primary_chart_pattern", None),
        side=side,
        price=float(getattr(ta, "current_price", 0) or 0),
    ):
        return "pattern"
    if _retest_location_ok(ta, side):
        return "retest"
    if _sr_location_ok(ta, side):
        return "sr"
    return "none"


def _side_aligned(ta: TAAnalysisResult, side: str) -> tuple[bool, str]:
    side = (side or "").lower()
    verdict = (getattr(ta, "verdict", "") or "").upper()
    priority = (getattr(ta, "action_priority", "") or "neutral").lower()

    if side == "long":
        if verdict == "SHORT":
            return False, "TA SHORT против лонга"
        if verdict in {"LONG", "WAIT"} and priority in {"long", "neutral"}:
            if verdict == "LONG" or priority == "long":
                return True, ""
            return True, ""
        if priority == "short":
            return False, "приоритет short"
        return verdict == "LONG", "нет подтверждения лонга"
    if side == "short":
        if verdict == "LONG":
            return False, "TA LONG против шорта"
        if verdict in {"SHORT", "WAIT"} and priority in {"short", "neutral"}:
            if verdict == "SHORT" or priority == "short":
                return True, ""
            return True, ""
        if priority == "long":
            return False, "приоритет long"
        return verdict == "SHORT", "нет подтверждения шорта"
    return False, "нет стороны"


def _flow_score(ta: TAAnalysisResult, side: str) -> tuple[int, list[str]]:
    pts = 0
    notes: list[str] = []
    cont = int(getattr(ta, "flow_continuation", 0) or 0)
    corr = int(getattr(ta, "flow_correction", 0) or 0)
    diff = cont - corr if side == "long" else corr - cont
    if diff >= 12:
        pts += 10
        notes.append("поток за стороной")
    elif diff >= 5:
        pts += 5
    elif diff <= -12:
        pts -= 8
        notes.append("поток против")
    oi = getattr(ta, "oi_narrative_label", "") or ""
    if oi and oi != "Мало данных OI":
        if side == "long" and any(x in oi.lower() for x in ("long", "накоп", "aligned")):
            pts += 5
        elif side == "short" and any(x in oi.lower() for x in ("short", "шорт", "unwind")):
            pts += 5
    return max(-10, min(15, pts)), notes


def score_trade_setup(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    side: str | None = None,
    readiness: tuple[bool, str] | None = None,
) -> SetupScore:
    """Единый скоринг качества сетапа 0–100."""
    side = (side or signal.side or "long").lower()
    ready = bool(readiness and readiness[0])
    location = detect_location(ta, side)
    chase, chase_reason = _is_late_chase(ta, side)
    aligned, _ = _side_aligned(ta, side)

    structure = 0
    loc_pts = 0
    wave = 0
    penalties = 0
    factors: list[str] = []

    verdict = (ta.verdict or "").upper()
    priority = (ta.action_priority or "neutral").lower()

    if side == "long":
        if verdict == "LONG":
            structure += 22
            factors.append("TA LONG")
        elif priority == "long":
            structure += 14
            factors.append("приоритет long")
        elif aligned:
            structure += 8
    else:
        if verdict == "SHORT":
            structure += 22
            factors.append("TA SHORT")
        elif priority == "short":
            structure += 14
            factors.append("приоритет short")
        elif aligned:
            structure += 8

    if not aligned:
        penalties += 18
        factors.append("конфликт TA")

    loc_map = {
        "fib": 28,
        "elliott": 27,
        "confluence": 27,
        "abc": 26,
        "retest": 24,
        "pattern": 22,
        "sr": 16,
        "none": 0,
    }
    loc_pts = loc_map.get(location, 0)
    if location != "none":
        factors.append(f"локация {location}")

    phase = (ta.wave_phase or "").lower()
    if phase == "wave_2_4_zone":
        wave += 16
        factors.append("Fib 0.5–0.618")
    elif phase in _PULLBACK_PHASES:
        wave += 8
    if getattr(ta, "abc_phase", "") in {"C", "complete"}:
        wave += 14
        factors.append("ABC волна C")
    if getattr(ta, "elliott_entry_ready", False):
        wave += 12
        mode = getattr(ta, "elliott_entry_mode", "") or ""
        factors.append(f"EW {mode}" if mode else "EW вход")
    elif getattr(ta, "elliott_phase", ""):
        wave += 6
        factors.append("EW структура")
    if getattr(ta, "wave_has_confluence", False):
        wave += min(12, 4 * int(getattr(ta, "wave_confluence_count", 0) or 0))

    # HTF + фигуры + Fib/SMC (Pro confluence)
    from .setup_confluence import SetupConfluence, confluence_boosts_gate

    conf_setup = SetupConfluence(
        score=int(getattr(ta, "setup_score", 0) or 0),
        grade=(getattr(ta, "setup_grade", "") or "D"),
        side=(getattr(ta, "setup_side", "") or "neutral"),
        ideal_ready=bool(getattr(ta, "setup_ideal_ready", False)),
        htf_bias=(getattr(ta, "htf_elliott_bias", "") or "neutral"),
    )
    conf_pts, conf_notes = confluence_boosts_gate(conf_setup, side)
    if conf_pts >= 0:
        wave += conf_pts
    else:
        penalties += abs(conf_pts)
    factors.extend(conf_notes)
    if getattr(ta, "is_ending_diagonal", False):
        wave += 4
        factors.append("ending diagonal")
    if getattr(ta, "is_abcde", False):
        wave += 4
        factors.append("ABCDE")

    flow, flow_notes = _flow_score(ta, side)
    factors.extend(flow_notes)

    if chase:
        penalties += 22
        factors.append(chase_reason or "погоня")
    if "вход невыгоден" in (getattr(ta, "verdict_reason", "") or "").lower():
        penalties += 30
        factors.append("плохой R:R")

    if ready and location == "none":
        loc_pts = max(loc_pts, 14)
        location = "trigger"
        factors.append("триггер ready")
    elif ready:
        loc_pts = max(loc_pts, 10)

    total = max(0, min(100, structure + loc_pts + wave + flow - penalties))
    return SetupScore(
        total=total,
        structure=structure,
        location=loc_pts,
        wave=wave,
        flow=flow,
        penalties=penalties,
        location_kind=location,
        factors=tuple(factors[:6]),
    )


def _cvd_confirms_side(side: str, cvd_ratio: float | None, *, long_min: float = 0.58, short_max: float = 0.42) -> bool:
    if cvd_ratio is None:
        return False
    if side == "long":
        return float(cvd_ratio) >= long_min
    if side == "short":
        return float(cvd_ratio) <= short_max
    return False


def decide_trade_action(
    signal: Signal,
    ta: TAAnalysisResult,
    *,
    readiness: tuple[bool, str] | None = None,
    quality_tier: str | None = None,
    watch_allowed: bool = True,
    min_entry_score: int = DEFAULT_MIN_ENTRY_SCORE,
    min_watch_score: int = DEFAULT_MIN_WATCH_SCORE,
    chase_range_high_pct: float = DEFAULT_CHASE_RANGE_HIGH_PCT,
    chase_range_mid_pct: float = DEFAULT_CHASE_RANGE_MID_PCT,
    block_chase_watch: bool = True,
) -> TradeDecision:
    """ENTRY = скоринг + локация/триггер; WATCH = интерес + план; SKIP = шум."""
    side = (signal.side or "").lower()
    ready = bool(readiness and readiness[0])
    ready_reason = (readiness[1] if readiness else "") or ""

    if quality_tier == "skip":
        return TradeDecision("skip", "quality skip", setup_score=0)

    if "вход невыгоден" in (getattr(ta, "verdict_reason", "") or "").lower():
        return TradeDecision("skip", "плохой R:R", setup_score=0)

    setup = score_trade_setup(signal, ta, side=side, readiness=readiness)
    chase, chase_reason = _is_late_chase(
        ta, side,
        range_high_pct=chase_range_high_pct,
        range_mid_pct=chase_range_mid_pct,
    )
    location = setup.location_kind
    aligned, align_reason = _side_aligned(ta, side)
    has_location = location in {
        "fib", "abc", "elliott", "confluence", "retest", "sr", "trigger", "pattern",
    }
    st = (signal.signal_type or "").lower()
    details = signal.details or {}
    seed_ext = float(details.get("seed_extension_pct", 99) or 99)
    # Ранний seed по фактам (ещё <8% от mid базы) — не режем как late chase TA
    early_seed = st == "trend_seed" and seed_ext <= 8.0

    # Жёстко: финал импульса / волна 5 → никогда ENTRY вдогонку.
    # После impulse_complete контртренд (fade) можно — но только ниже по коду при ready+локации.
    wave_phase = (getattr(ta, "wave_phase", "") or "").lower()
    fib_status = (getattr(ta, "fib_status", "") or "").lower()
    ew_phase = (getattr(ta, "elliott_phase", "") or "").lower()
    ew_bias = (getattr(ta, "wave_bias", "") or "neutral").lower()
    late_hard = wave_phase == "late_impulse" or fib_status == "late_impulse"
    if ew_phase == "impulse_5":
        late_hard = True
    elif ew_phase == "impulse_complete":
        # продолжение завершённого импульса = погоня; обратная сторона — не late_hard
        if side == "long" and ew_bias in {"long", "neutral"}:
            late_hard = True
        elif side == "short" and ew_bias in {"short", "neutral"}:
            late_hard = True
    if late_hard or (chase and not early_seed):
        reason = chase_reason or "финал импульса / погоня — ждать откат"
        if watch_allowed:
            return TradeDecision(
                "watch",
                reason,
                location=location,
                chase=True,
                setup_score=max(setup.total, min_watch_score),
            )
        return TradeDecision(
            "skip",
            reason,
            location=location,
            chase=True,
            setup_score=setup.total,
        )

    # trend_seed раньше WAIT-правила: ранний потенциал не режем бейджем WAIT
    if st == "trend_seed" and watch_allowed:
        has_loc = location in {"fib", "abc", "elliott", "confluence", "retest", "sr", "pattern"}
        cvd_miss = float(details.get("seed_cvd_missing", 0) or 0)
        if has_loc and setup.total >= min_entry_score and not chase:
            return TradeDecision(
                "entry",
                f"seed у уровня · сетап {setup.total}/100",
                location=location,
                chase=False,
                setup_score=setup.total,
            )
        reason = "потенциал тренда · ждать ретест/Fib"
        if cvd_miss >= 0.5:
            reason = f"{reason} · CVD слабый/нет"
        return TradeDecision(
            "watch",
            reason,
            location=location,
            chase=False,
            setup_score=max(setup.total, min_watch_score),
        )

    # TA на графике WAIT + нет триггера → максимум WATCH (не ENTRY против бейджа)
    if (getattr(ta, "verdict", "") or "").upper() == "WAIT" and not ready:
        if watch_allowed:
            return TradeDecision(
                "watch",
                "график WAIT — ждать подтверждение",
                location=location,
                chase=False,
                setup_score=max(setup.total, min_watch_score),
            )
        return TradeDecision(
            "skip",
            "график WAIT — нет подтверждения",
            location=location,
            setup_score=setup.total,
        )

    if not aligned and setup.total < min_watch_score:
        return TradeDecision("skip", align_reason or "конфликт TA", setup_score=setup.total)

    # Reversal: ENTRY только при CVD confirm + локация; иначе WATCH/skip
    if st in _REVERSAL_TYPES:
        try:
            cvd_raw = details.get("cvd_ratio")
            cvd_f = float(cvd_raw) if cvd_raw is not None else None
        except (TypeError, ValueError):
            cvd_f = None
        cvd_ok = _cvd_confirms_side(side, cvd_f)
        has_loc = location in {"fib", "abc", "elliott", "confluence", "retest", "sr", "pattern"}
        if has_loc and cvd_ok and setup.total >= min_entry_score and not chase:
            return TradeDecision(
                "entry",
                f"разворот + CVD · сетап {setup.total}/100",
                location=location,
                chase=False,
                setup_score=setup.total,
            )
        if not cvd_ok:
            reason = (
                f"разворот без CVD confirm ({cvd_f:.0%} buy)"
                if cvd_f is not None
                else "разворот без CVD — ждать поток"
            )
            if watch_allowed:
                return TradeDecision(
                    "watch", reason, location=location, chase=True, setup_score=setup.total,
                )
            return TradeDecision("skip", reason, location=location, chase=True, setup_score=setup.total)
        if watch_allowed:
            return TradeDecision(
                "watch",
                chase_reason or "разворот — ждать подтверждение у уровня",
                location=location,
                chase=chase,
                setup_score=max(setup.total, min_watch_score),
            )
        return TradeDecision(
            "skip",
            "разворот без готового entry",
            location=location,
            chase=chase,
            setup_score=setup.total,
        )

    # ENTRY: скор + локация + НЕ погоня (исключений по Fib/паттерну больше нет)
    can_entry = (
        setup.total >= min_entry_score
        and has_location
        and not chase
    )
    if can_entry and (ready or location in {"fib", "abc", "elliott", "confluence", "retest", "sr", "pattern"}):
        reason = f"сетап {setup.total}/100"
        if ready_reason:
            reason = f"{reason} · {ready_reason}"
        return TradeDecision(
            "entry", reason, location=location, chase=False, setup_score=setup.total,
        )

    # Ready trigger без погоня — тоже ENTRY при хорошем скоре
    if ready and not chase and setup.total >= min_entry_score - 8:
        return TradeDecision(
            "entry",
            ready_reason or f"триггер · сетап {setup.total}/100",
            location="trigger",
            chase=False,
            setup_score=setup.total,
        )

    # WATCH: движение + план, ждём уровень (чаще сигналы, но не market entry)
    if watch_allowed and setup.total >= min_watch_score:
        reason = chase_reason or ready_reason or "ждать уровень входа"
        if not has_location:
            reason = f"{reason} · план: Fib/ретest/ПС"
        return TradeDecision(
            "watch", reason, location=location, chase=chase, setup_score=setup.total,
        )

    if watch_allowed and aligned and (signal.signal_type or "").lower() in _CHASE_TYPES:
        return TradeDecision(
            "watch",
            chase_reason or "импульс — ждать откат к уровню",
            location=location,
            chase=True,
            setup_score=setup.total,
        )

    return TradeDecision(
        "skip",
        align_reason or f"слабый сетап {setup.total}/100",
        location=location,
        chase=chase,
        setup_score=setup.total,
    )


def apply_decision_to_quality_tier(
    quality_tier: str,
    decision: TradeDecision,
) -> str:
    q = (quality_tier or "watch").lower()
    if decision.action == "skip":
        return "skip"
    if decision.action == "watch":
        return "watch" if q != "skip" else "skip"
    if q == "skip":
        return "skip"
    return "entry"
