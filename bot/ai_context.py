"""Build a compact structured context pack from bot TA / gates for Gemini."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .liquidation_alerts import coinglass_liq_map_url, coinglass_url
from .models import Signal
from .smc_analysis import smc_to_dict
from .ta_analysis import TAAnalysisResult, fmt_price

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 24
ALLOWED_HOURS = {12, 24, 48}


@dataclass
class AiContextPack:
    symbol: str
    exchange: str
    hours: int
    interval_minutes: int
    context_text: str
    context_dict: dict[str, Any] = field(default_factory=dict)
    chart_png: bytes | None = None
    liq_map_png: bytes | None = None
    chart_url: str = ""
    liq_map_url: str = ""
    ta: TAAnalysisResult | None = None


ALLOWED_INTERVALS = (1, 3, 5, 10, 15, 30, 60, 240)


def parse_hours_from_text(text: str, *, default: int = DEFAULT_HOURS) -> int:
    raw = (text or "").lower()
    if re.search(r"\b(48|двое|двух|2\s*сут|двое\s*суток)\b", raw):
        return 48
    if re.search(r"\b(24|сутки|суток|день|дня)\b", raw):
        return 24
    if re.search(r"\b(12)\b", raw):
        return 12
    m = re.search(r"(\d+)\s*h", raw)
    if m:
        val = int(m.group(1))
        if val in ALLOWED_HOURS:
            return val
        if val >= 40:
            return 48
        if val >= 18:
            return 24
        return 12
    return default if default in ALLOWED_HOURS else DEFAULT_HOURS


def parse_interval_from_text(text: str, *, default: int = 5) -> int:
    """Parse working TF from user text: 15s → 1m pack, 5m/15m/1h etc."""
    raw = (text or "").lower().replace(" ", "")
    # micro seconds hint → nearest pack TF 1m (15s candles not always available)
    if "15s" in raw or "15сек" in raw or "15sec" in raw:
        return 1 if 1 in ALLOWED_INTERVALS else default
    m = re.search(r"(\d+)s(ec|ек)?", raw)
    if m and "m" not in raw[m.start():m.end() + 1]:
        return 1 if 1 in ALLOWED_INTERVALS else default
    m = re.search(r"(\d+)m(in|ин)?", raw)
    if m:
        val = int(m.group(1))
        if val in ALLOWED_INTERVALS:
            return val
    m = re.search(r"(\d+)h(our|ас)?", raw)
    if m:
        mins = int(m.group(1)) * 60
        if mins in ALLOWED_INTERVALS:
            return mins
    if "1h" in raw or "час" in (text or "").lower():
        return 60
    return default if default in ALLOWED_INTERVALS else 5


def build_multi_tf_map(interval_minutes: int) -> dict[str, Any]:
    """How the model should stack timeframes for this request."""
    working = int(interval_minutes) if interval_minutes in ALLOWED_INTERVALS else 5
    return {
        "working_tf": f"{working}m",
        "working_minutes": working,
        "htf": "1h",
        "htf_role": "bias / крупные фигуры / конфликт → приоритет HTF",
        "working_role": "основная структура, триггер close, стоп/TP зоны",
        "micro_role": (
            "1m/15s или импульс свечей на скрине — только тайминг входа; "
            "не подменяет HTF bias"
        ),
        "synthesis": "HTF bias → WORKING levels/trigger → micro timing → конкретика",
    }

def _round(v: Any, nd: int = 6) -> Any:
    if v is None:
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return v


def _pattern_brief(p: Any) -> dict[str, Any]:
    return {
        "kind": getattr(p, "kind", ""),
        "label_ru": getattr(p, "label_ru", ""),
        "direction": getattr(p, "direction", ""),
        "status": getattr(p, "status", ""),
        "confidence": _round(getattr(p, "confidence", 0), 3),
        "target": _round(getattr(p, "target_price", None)),
        "stop": _round(getattr(p, "stop_price", None)),
        "entry_mode": getattr(p, "entry_mode", ""),
        "psychology": getattr(p, "psychology_note", "")[:160],
        "subtype": getattr(p, "subtype", ""),
        "timeframe": getattr(p, "timeframe", "ltf"),
        "volume_contracted": bool(getattr(p, "volume_contracted", False)),
        "volume_breakout": bool(getattr(p, "volume_breakout", False)),
    }


def serialize_ta(ta: TAAnalysisResult) -> dict[str, Any]:
    fib = [
        {"ratio": _round(getattr(fl, "ratio", 0), 3), "price": _round(getattr(fl, "price", None)), "kind": getattr(fl, "kind", "")}
        for fl in (ta.fib_levels or [])[:12]
    ]
    patterns = [_pattern_brief(p) for p in (ta.chart_patterns or [])[:8]]
    primary = _pattern_brief(ta.primary_chart_pattern) if ta.primary_chart_pattern else None
    htf_patterns = [_pattern_brief(p) for p in (getattr(ta, "htf_chart_patterns", None) or [])[:4]]
    htf_primary = (
        _pattern_brief(ta.primary_htf_chart_pattern)
        if getattr(ta, "primary_htf_chart_pattern", None)
        else None
    )
    pattern_foresight = None
    if getattr(ta, "pattern_foresight_summary", ""):
        from .pattern_foresight import format_horizon_label

        _hz = float(getattr(ta, "pattern_foresight_horizon", 0) or 0)
        pattern_foresight = {
            "horizon_hours": _hz or None,
            "horizon_label": format_horizon_label(_hz) if _hz else None,
            "bias": getattr(ta, "pattern_foresight_bias", "") or None,
            "status": getattr(ta, "pattern_foresight_status", "") or None,
            "summary": (ta.pattern_foresight_summary or "")[:200],
            "path": (getattr(ta, "pattern_foresight_path", "") or "")[:240],
            "trigger": (getattr(ta, "pattern_foresight_trigger", "") or "")[:160],
            "htf_conflict": bool(getattr(ta, "pattern_foresight_htf_conflict", False)),
            "watch_only": bool(getattr(ta, "pattern_foresight_watch_only", False)),
        }
    key_levels = [
        {"price": _round(getattr(k, "price", None)), "label": getattr(k, "label", "")}
        for k in (ta.key_levels or [])[:10]
    ]
    candle_patterns = [
        {
            "name": getattr(p, "name", ""),
            "label_ru": getattr(p, "label_ru", ""),
            "bullish": getattr(p, "bullish", None),
        }
        for p in (ta.patterns or [])[:6]
    ]
    smc = smc_to_dict(ta.smc) if ta.smc is not None else None
    if smc and ta.smc is not None:
        smc["fvgs"] = [
            {
                "direction": f.direction,
                "top": _round(f.top),
                "bottom": _round(f.bottom),
                "label": f.label,
            }
            for f in (ta.smc.fvgs or [])[:6]
        ]
        smc["liquidity"] = [
            {"price": _round(lv.price), "kind": lv.kind, "label": lv.label}
            for lv in (ta.smc.liquidity_levels or [])[:8]
        ]
        smc["checklist"] = [{"item": a, "yes": b} for a, b in (ta.smc.checklist or [])[:10]]

    return {
        "price": _round(ta.current_price),
        "verdict": ta.verdict,
        "confidence": ta.verdict_confidence,
        "reason": (ta.verdict_reason or "")[:280],
        "phase": ta.phase,
        "phase_label": ta.phase_label,
        "structure": ta.structure_label,
        "market_bias": ta.market_bias,
        "oi_narrative": ta.oi_narrative_label,
        "action_priority": ta.action_priority,
        "post_pump": bool(getattr(ta, "post_pump", False)),
        "candle_compression": bool(getattr(ta, "candle_compression", False)),
        "range_position": _round(ta.range_position, 3),
        "drawdown_from_high_pct": _round(ta.drawdown_from_high_pct, 2),
        "momentum": ta.momentum_label,
        "momentum_pct": _round(ta.momentum_pct, 3),
        "entry_zone": [_round(x) for x in ta.entry_zone] if ta.entry_zone else None,
        "targets": [_round(x) for x in (ta.target_prices or [])[:4]],
        "invalidation": _round(ta.invalidation_price),
        "support": _round(ta.nearest_support),
        "resistance": _round(ta.nearest_resistance),
        "key_levels": key_levels,
        "candle_patterns": candle_patterns,
        "chart_patterns": patterns,
        "primary_pattern": primary,
        "htf_chart_patterns": htf_patterns,
        "primary_htf_pattern": htf_primary,
        "pattern_foresight": pattern_foresight,
        "fib": fib,
        "wave": {
            "phase": ta.wave_phase,
            "bias": ta.wave_bias,
            "confidence": ta.wave_confidence,
            "has_confluence": ta.wave_has_confluence,
            "confluence_count": ta.wave_confluence_count,
            "leg_start": _round(ta.wave_leg_start),
            "leg_end": _round(ta.wave_leg_end),
            "fib_status": ta.fib_status,
            "fib_reject": ta.fib_reject_reason,
        },
        "elliott": {
            "label": ta.elliott_label,
            "phase": ta.elliott_phase,
            "confidence": ta.elliott_confidence,
            "entry_mode": ta.elliott_entry_mode,
            "entry_ready": ta.elliott_entry_ready,
            "entry": _round(ta.elliott_entry_price),
            "stop": _round(ta.elliott_stop_price),
            "tps": [_round(x) for x in (ta.elliott_tp_prices or [])[:3]],
            "fib_classic_ok": ta.elliott_fib_classic_ok,
            "extension": ta.elliott_extension,
            "truncated": ta.elliott_truncated,
            "diagonal": ta.elliott_diagonal,
            "corr_type": ta.elliott_corr_type,
            "triangle": ta.elliott_triangle_kind,
            "triangle_bias": ta.elliott_triangle_bias,
            "complex": ta.elliott_complex_kind,
            "structure_note": (ta.elliott_structure_note or "")[:200],
            "path_bias": ta.elliott_path_bias,
            "path_reason": (ta.elliott_path_reason or "")[:200],
            "path_horizon_hours": getattr(ta, "elliott_path_horizon_hours", 0) or None,
            "path_scenario": getattr(ta, "elliott_path_scenario", "") or "",
            "path_invalidation": _round(getattr(ta, "elliott_path_invalidation", None)),
            "path_prices": [_round(x) for x in (getattr(ta, "elliott_path_prices", None) or [])[:6]],
            "path_labels": list(getattr(ta, "elliott_path_labels", None) or [])[:6],
            "global_label": ta.elliott_global_label,
            "local_label": ta.elliott_local_label,
            "fib_clusters": getattr(ta, "elliott_fib_clusters", []) or [],
        },
        "abc": {"phase": ta.abc_phase, "label_ru": ta.abc_label_ru},
        "setup_confluence": {
            "score": ta.setup_score,
            "grade": ta.setup_grade,
            "side": ta.setup_side,
            "label_ru": ta.setup_label_ru,
            "factors": (ta.setup_factors or [])[:10],
            "ideal_ready": ta.setup_ideal_ready,
            "entry": _round(ta.setup_entry),
            "stop": _round(ta.setup_stop),
            "tps": [_round(x) for x in (ta.setup_tps or [])[:3]],
            "trigger": ta.setup_trigger,
            "ending_diagonal": ta.is_ending_diagonal,
            "abcde": ta.is_abcde,
        },
        "smc": smc,
        "smc_score": ta.smc_score,
        "smc_summary": (ta.smc_summary or "")[:240],
        "forecast_summary": (ta.forecast_summary or "")[:280],
        "flow_continuation": ta.flow_continuation,
        "flow_correction": ta.flow_correction,
        "flow_notes": (ta.flow_notes or [])[:6],
        "narrative_plan": (ta.narrative_plan or "")[:280],
        "narrative_basis": (ta.narrative_basis or "")[:280],
        "cvd_source": ta.cvd_source,
        "cvd_delta": _round(ta.cvd_delta, 4),
        "rsi": {
            "last": _round(getattr(ta, "rsi_last", None), 2),
            "bias": getattr(ta, "rsi_divergence_bias", "neutral") or "neutral",
            "summary": (getattr(ta, "rsi_divergence_summary", "") or "")[:200],
            "divergences": [
                {
                    "kind": getattr(d, "kind", ""),
                    "label": getattr(d, "label", ""),
                    "strength": _round(getattr(d, "strength", 0), 3),
                    "rsi_a": _round(getattr(d, "rsi_a", None), 2),
                    "rsi_b": _round(getattr(d, "rsi_b", None), 2),
                }
                for d in (getattr(ta, "rsi_divergences", None) or [])[-4:]
            ],
        },
        "liq_cascade": ta.liq_cascade_active,
        "liq_cascade_note": (ta.liq_cascade_note or "")[:200],
        "liq_magnet": {
            "bias": ta.liq_magnet_bias,
            "label": ta.liq_magnet_label,
            "above": _round(ta.liq_magnet_above),
            "below": _round(ta.liq_magnet_below),
            "strength": _round(ta.liq_magnet_strength, 3),
            "note": (ta.liq_magnet_note or "")[:240],
            "hint": (ta.liq_magnet_hint or "")[:280],
        },
        "risk_notes": (ta.risk_notes or [])[:6],
        "factor_lines": (ta.factor_lines or [])[:10],
        "trader_plan": (ta.trader_plan or [])[:8],
        "professional_summary": (ta.professional_summary or "")[:320],
        "htf_elliott": {
            "label": ta.htf_elliott_label,
            "phase": ta.htf_elliott_phase,
            "bias": ta.htf_elliott_bias,
        },
        "range_trade": {
            "label": ta.range_trade_label,
            "direction": ta.range_trade_direction,
            "entry_mode": ta.entry_mode,
        },
    }


def _synthetic_signal(symbol: str, exchange: str, ta: TAAnalysisResult) -> Signal:
    side = "long" if ta.verdict == "LONG" else "short" if ta.verdict == "SHORT" else "long"
    return Signal(
        exchange=exchange,
        symbol=symbol.upper(),
        signal_type="ai_review",
        oi_period_minutes=15,
        oi_change_percent=0.0,
        oi_change_value=0.0,
        oi_change_usd=None,
        oi_direction="flat",
        signals_today=0,
        price_change_percent=None,
        price_change_value=None,
        price_direction=None,
        volume_change_percent=None,
        trade_count=None,
        spread=None,
        funding_rate=None,
        liquidation_estimate=None,
        vwap=None,
        atr=None,
        rsi=None,
        ema_short=None,
        ema_long=None,
        volume_24h=None,
        volume_speed=None,
        signal_score=5,
        side=side,
        current_price=ta.current_price,
        current_open_interest=None,
        link=coinglass_url(symbol, exchange),
        details={},
    )


def attach_gates(pack: dict[str, Any], ta: TAAnalysisResult, symbol: str, exchange: str) -> None:
    try:
        from .trade_decision_gate import decide_trade_action, score_trade_setup

        signal = _synthetic_signal(symbol, exchange, ta)
        setup = score_trade_setup(signal, ta, side=signal.side)
        decision = decide_trade_action(signal, ta, watch_allowed=True)
        pack["decision_gate"] = {
            "action": decision.action,
            "reason": decision.reason,
            "location": decision.location,
            "chase": decision.chase,
            "setup_score": decision.setup_score,
            "score_total": setup.total,
            "location_kind": setup.location_kind,
            "factors": list(setup.factors)[:10],
            "parts": {
                "structure": setup.structure,
                "location": setup.location,
                "wave": setup.wave,
                "flow": setup.flow,
                "penalties": setup.penalties,
            },
        }
    except Exception:
        logger.debug("decision_gate attach failed", exc_info=True)

    try:
        from .trade_playbook import resolve_trade_playbook

        signal = _synthetic_signal(symbol, exchange, ta)
        pb = resolve_trade_playbook(signal, ta)
        if pb is not None:
            tps = list(getattr(pb, "target_prices", None) or [])
            pack["playbook"] = {
                "side": getattr(pb, "side", ""),
                "aligned": bool(getattr(pb, "aligned", False)),
                "logic": (getattr(pb, "logic", "") or "")[:240],
                "entry": _round(getattr(pb, "entry_price", None)),
                "entry_op": getattr(pb, "entry_op", ""),
                "stop": _round(getattr(pb, "stop_price", None)),
                "tp1": _round(tps[0]) if tps else None,
                "tp2": _round(tps[1]) if len(tps) > 1 else None,
            }
    except Exception:
        logger.debug("playbook attach failed", exc_info=True)


def _dist_pct(price: float, level: float | None) -> float | None:
    if level is None or price <= 0:
        return None
    try:
        lv = float(level)
    except (TypeError, ValueError):
        return None
    if lv <= 0:
        return None
    return abs(lv - price) / price * 100.0


def build_bot_position_call(pack: dict[str, Any]) -> dict[str, Any]:
    """Сводка «какую позицию» строго из алгоритмов бота (не выдумка модели)."""
    ta = pack.get("ta") or {}
    gate = pack.get("decision_gate") or {}
    pb = pack.get("playbook") or {}
    vol = pack.get("volatility_regime") or {}
    foresight = ta.get("pattern_foresight") or {}
    setup = ta.get("setup_confluence") or {}
    elliott = ta.get("elliott") or {}
    rsi = ta.get("rsi") or {}
    primary = ta.get("primary_pattern") or {}
    htf_pat = ta.get("primary_htf_pattern") or {}

    verdict = str(ta.get("verdict") or "WAIT").upper()
    priority = str(ta.get("action_priority") or "neutral").lower()
    gate_action = str(gate.get("action") or "").upper()  # ENTRY/WATCH/SKIP
    pb_side = str(pb.get("side") or "").lower()
    foresight_bias = str(foresight.get("bias") or "neutral").lower()
    setup_side = str(setup.get("side") or "neutral").lower()
    rsi_bias = str(rsi.get("bias") or "neutral").lower()
    ew_bias = str(elliott.get("path_bias") or elliott.get("bias") or "").lower()
    if ew_bias in {"up", "bull", "bullish"}:
        ew_bias = "long"
    elif ew_bias in {"down", "bear", "bearish"}:
        ew_bias = "short"

    votes: dict[str, int] = {"long": 0, "short": 0}
    reasons: list[str] = []

    def _vote(side: str, w: int, why: str) -> None:
        if side not in {"long", "short"} or w <= 0:
            return
        votes[side] += w
        reasons.append(f"{why}→{side.upper()}(+{w})")

    if verdict == "LONG":
        _vote("long", 3, "TA_verdict")
    elif verdict == "SHORT":
        _vote("short", 3, "TA_verdict")
    if priority in {"long", "short"}:
        _vote(priority, 2, "action_priority")
    if gate_action == "ENTRY" and pb_side in {"long", "short"}:
        _vote(pb_side, 3, "decision_gate_ENTRY")
    elif gate_action == "WATCH" and pb_side in {"long", "short"}:
        _vote(pb_side, 1, "decision_gate_WATCH")
    if pb.get("aligned") and pb_side in {"long", "short"}:
        _vote(pb_side, 2, "playbook_aligned")
    if foresight_bias in {"long", "short"} and not foresight.get("watch_only"):
        _vote(foresight_bias, 2, f"pattern_foresight:{foresight.get('status')}")
    elif foresight_bias in {"long", "short"}:
        _vote(foresight_bias, 1, "pattern_forming_WATCH")
    if setup_side in {"long", "short"} and str(setup.get("grade") or "") in {"A", "B"}:
        _vote(setup_side, 2, f"setup_{setup.get('grade')}")
    elif setup_side in {"long", "short"}:
        _vote(setup_side, 1, f"setup_{setup.get('grade') or 'C'}")
    if ew_bias in {"long", "short"}:
        _vote(ew_bias, 2 if elliott.get("entry_ready") else 1, "elliott")
    if rsi_bias in {"long", "short"}:
        _vote(rsi_bias, 1, "rsi_divergence")

    cont = int(ta.get("flow_continuation") or 0)
    corr = int(ta.get("flow_correction") or 0)
    if cont >= corr + 15:
        _vote("long", 1, f"flow_cont{cont}")
    elif corr >= cont + 15:
        _vote("short", 1, f"flow_corr{corr}")

    pat_dir = str(primary.get("direction") or "").lower()
    if pat_dir in {"bullish", "long"}:
        _vote("long", 1, f"LTF_pattern:{primary.get('kind')}")
    elif pat_dir in {"bearish", "short"}:
        _vote("short", 1, f"LTF_pattern:{primary.get('kind')}")
    htf_dir = str(htf_pat.get("direction") or "").lower()
    if htf_dir in {"bullish", "long"}:
        _vote("long", 2, f"HTF_pattern:{htf_pat.get('kind')}")
    elif htf_dir in {"bearish", "short"}:
        _vote("short", 2, f"HTF_pattern:{htf_pat.get('kind')}")

    # Compression after pump → dual, no forced side
    if ta.get("candle_compression") and ta.get("post_pump"):
        votes["long"] = max(0, votes["long"] - 1)
        votes["short"] = max(0, votes["short"] - 1)
        reasons.append("compression_post_pump→dual_breakout(-1 each)")

    long_v, short_v = votes["long"], votes["short"]
    spread = abs(long_v - short_v)
    lean = "NONE"
    if long_v == 0 and short_v == 0:
        position = "NO_TRADE"
        mode = "wait"
    elif spread <= 1 and max(long_v, short_v) < 4:
        position = "WAIT"
        mode = "watch_both"
        lean = "LONG" if long_v > short_v else ("SHORT" if short_v > long_v else "NONE")
    elif long_v > short_v:
        position = "LONG"
        mode = "entry" if gate_action == "ENTRY" or (verdict == "LONG" and spread >= 3) else "watch"
        lean = "LONG"
    else:
        position = "SHORT"
        mode = "entry" if gate_action == "ENTRY" or (verdict == "SHORT" and spread >= 3) else "watch"
        lean = "SHORT"

    # Levels from playbook / ta
    price = ta.get("price")
    entry = pb.get("entry")
    zone = ta.get("entry_zone")
    if entry is None and isinstance(zone, list) and zone:
        entry = zone[0]
    stop = pb.get("stop") or ta.get("invalidation")
    tp1 = pb.get("tp1")
    tp2 = pb.get("tp2")
    targets = ta.get("targets") or []
    if tp1 is None and targets:
        tp1 = targets[0]
    if tp2 is None and len(targets) > 1:
        tp2 = targets[1]
    breakout = ta.get("resistance") if (position == "LONG" or lean == "LONG") else None
    breakdown = ta.get("support") if (position == "SHORT" or lean == "SHORT") else None

    conf = min(10, max(1, 4 + spread + (1 if gate_action == "ENTRY" else 0)))
    if position in {"WAIT", "NO_TRADE"}:
        conf = min(conf, 6)

    thesis_bits = reasons[:8]
    if foresight.get("summary"):
        thesis_bits.append(f"foresight:{str(foresight.get('summary'))[:80]}")
    if setup.get("label_ru"):
        thesis_bits.append(f"setup:{setup.get('label_ru')}")
    if elliott.get("label"):
        thesis_bits.append(f"EW:{elliott.get('label')}")

    how = "market_now"
    if mode == "watch" or position == "WAIT":
        how = "trigger_close"
    if gate_action == "WATCH" or foresight.get("watch_only"):
        how = "trigger_close"
    if ta.get("candle_compression") and ta.get("post_pump"):
        how = "dual_breakout_close"
        position = "WAIT" if position != "NO_TRADE" else position
        mode = "watch_both"

    return {
        "position": position,  # LONG|SHORT|WAIT|NO_TRADE
        "mode": mode,  # entry|watch|watch_both|wait
        "lean": lean if position in {"WAIT", "NO_TRADE"} else position,
        "how": how,
        "confidence": conf,
        "votes": {"long": long_v, "short": short_v, "spread": spread},
        "gate_action": gate_action or None,
        "horizon": vol.get("horizon"),
        "tp1_min_pct": vol.get("tp1_min_pct"),
        "levels": {
            "price": price,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "breakout_hint": breakout,
            "breakdown_hint": breakdown,
            "entry_op": pb.get("entry_op") or (">=" if lean == "LONG" else "<="),
        },
        "why": thesis_bits,
        "instruction": (
            "Используй POSITION_CALL как базу своего мнения о позиции. "
            "Не противоречь сильному перевесу голосов алгоритмов без явной причины с графика. "
            "Если mode=watch/watch_both — НЕ советуй market сейчас, только триггер close."
        ),
    }


def build_volatility_regime(ta: dict[str, Any]) -> dict[str, Any]:
    """Pulse regime for AI: horizon + min TP by recent heat (not fixed 1–3h / 1–2%)."""
    price = float(ta.get("price") or 0)
    mom = abs(float(ta.get("momentum_pct") or 0))
    dd = abs(float(ta.get("drawdown_from_high_pct") or 0))
    # heat proxy: recent move + how violent the day swing is
    heat = max(mom, dd / 8.0)
    if heat >= 4.0 or dd >= 25:
        regime = "extreme"
        horizon = "15–45м"
        tp1_min, tp1_pref, tp2 = 3.0, 4.5, 7.0
        note = "Альт/памп-дамп: цели 1–2% и горизонт 1–3ч ЗАПРЕЩЕНЫ."
    elif heat >= 2.0 or dd >= 12:
        regime = "hot"
        horizon = "20–60м"
        tp1_min, tp1_pref, tp2 = 2.5, 3.5, 5.5
        note = "Горячая монета: TP1 ≥2.5–3.5%, горизонт десятки минут."
    elif heat >= 0.8:
        regime = "normal"
        horizon = "30–90м"
        tp1_min, tp1_pref, tp2 = 1.5, 2.5, 4.0
        note = "Обычный intraday: не микро-TP."
    else:
        regime = "calm"
        horizon = "1–3ч"
        tp1_min, tp1_pref, tp2 = 1.0, 1.5, 3.0
        note = "Спокойный актив: можно длиннее горизонт."
    return {
        "regime": regime,
        "horizon": horizon,
        "heat": round(heat, 2),
        "momentum_pct": round(mom, 2),
        "drawdown_from_high_pct": round(dd, 2),
        "tp1_min_pct": tp1_min,
        "tp1_prefer_pct": tp1_pref,
        "tp2_hint_pct": tp2,
        "price": price if price > 0 else None,
        "note": note,
    }


def build_meaningful_levels(
    ta: dict[str, Any],
    *,
    min_pct: float | None = None,
) -> dict[str, Any]:
    """Levels far enough for a trade path sized to volatility (skip micro-noise)."""
    vol = build_volatility_regime(ta)
    if min_pct is None:
        min_pct = float(vol["tp1_min_pct"])
    price = float(ta.get("price") or 0)
    if price <= 0:
        return {
            "min_pct": min_pct,
            "above": [],
            "below": [],
            "tp1_min_pct": vol["tp1_min_pct"],
            "tp1_prefer_pct": vol["tp1_prefer_pct"],
            "tp2_hint_pct": vol["tp2_hint_pct"],
            "volatility_regime": vol["regime"],
        }

    candidates: list[tuple[str, float]] = []
    for key, label in (
        ("support", "support"),
        ("resistance", "resistance"),
        ("invalidation", "invalidation"),
    ):
        v = ta.get(key)
        if v is not None:
            candidates.append((label, float(v)))
    for t in ta.get("targets") or []:
        try:
            candidates.append(("target", float(t)))
        except (TypeError, ValueError):
            continue
    magnet = ta.get("liq_magnet") or {}
    if magnet.get("above") is not None:
        candidates.append(("magnet_above", float(magnet["above"])))
    if magnet.get("below") is not None:
        candidates.append(("magnet_below", float(magnet["below"])))
    for kl in ta.get("key_levels") or []:
        try:
            candidates.append((str(kl.get("label") or "key"), float(kl["price"])))
        except (TypeError, ValueError, KeyError):
            continue
    setup = ta.get("setup_confluence") or {}
    for t in setup.get("tps") or []:
        try:
            candidates.append(("setup_tp", float(t)))
        except (TypeError, ValueError):
            continue
    if setup.get("entry") is not None:
        try:
            candidates.append(("setup_entry", float(setup["entry"])))
        except (TypeError, ValueError):
            pass
    if setup.get("stop") is not None:
        try:
            candidates.append(("setup_stop", float(setup["stop"])))
        except (TypeError, ValueError):
            pass
    ew = ta.get("elliott") or {}
    for t in ew.get("tps") or []:
        try:
            candidates.append(("ew_tp", float(t)))
        except (TypeError, ValueError):
            continue
    if ew.get("entry") is not None:
        try:
            candidates.append(("ew_entry", float(ew["entry"])))
        except (TypeError, ValueError):
            pass
    if ew.get("stop") is not None:
        try:
            candidates.append(("ew_stop", float(ew["stop"])))
        except (TypeError, ValueError):
            pass
    # Fib retracements / extensions from wave pack
    for fl in ta.get("fib") or []:
        try:
            ratio = float(fl.get("ratio") or 0)
            lvl = float(fl.get("price"))
            kind = str(fl.get("kind") or "fib")
            candidates.append((f"fib_{kind}_{ratio:g}", lvl))
        except (TypeError, ValueError, KeyError):
            continue
    wave = ta.get("wave") or {}
    for key, label in (("leg_start", "wave_leg_start"), ("leg_end", "wave_leg_end")):
        if wave.get(key) is not None:
            try:
                candidates.append((label, float(wave[key])))
            except (TypeError, ValueError):
                pass
    primary = ta.get("primary_pattern") or {}
    for key, label in (("target", "pattern_tp"), ("stop", "pattern_stop")):
        if primary.get(key) is not None:
            try:
                candidates.append((label, float(primary[key])))
            except (TypeError, ValueError):
                pass

    above: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    seen: set[float] = set()
    for label, lvl in candidates:
        dist = _dist_pct(price, lvl)
        if dist is None or dist < min_pct:
            continue
        rounded = round(lvl, 6)
        if rounded in seen:
            continue
        seen.add(rounded)
        item = {"label": label, "price": rounded, "dist_pct": round(dist, 2)}
        if lvl > price:
            above.append(item)
        else:
            below.append(item)
    above.sort(key=lambda x: x["dist_pct"])
    below.sort(key=lambda x: x["dist_pct"])
    return {
        "min_pct": min_pct,
        "guide": (
            f"Первая цель/ход ≥{vol['tp1_min_pct']}% (режим {vol['regime']}, "
            f"горизонт {vol['horizon']}). Fib/EW/ABC/pattern/magnet из списка. "
            "Ближе min_pct — шум, не сценарий."
        ),
        "above": above[:8],
        "below": below[:8],
        "tp1_min_pct": vol["tp1_min_pct"],
        "tp1_prefer_pct": vol["tp1_prefer_pct"],
        "tp2_hint_pct": vol["tp2_hint_pct"],
        "volatility_regime": vol["regime"],
        "horizon": vol["horizon"],
    }


def format_context_text(pack: dict[str, Any]) -> str:
    """Human+JSON hybrid for the model (compact)."""
    sym = pack.get("symbol", "?")
    ex = pack.get("exchange", "?")
    hours = pack.get("hours", 24)
    ta = pack.get("ta") or {}
    vol = pack.get("volatility_regime") or build_volatility_regime(ta)
    meaningful = pack.get("meaningful_levels") or build_meaningful_levels(ta)
    multi = pack.get("multi_tf") or build_multi_tf_map(int(pack.get("interval_minutes") or 5))
    lines = [
        f"SYMBOL={sym} EXCHANGE={ex} WINDOW={hours}h WORKING_TF={multi.get('working_tf')}",
        (
            f"MULTI_TF working={multi.get('working_tf')} ({multi.get('working_role')}) | "
            f"HTF={multi.get('htf')} ({multi.get('htf_role')}) | "
            f"micro={multi.get('micro_role')} | synth={multi.get('synthesis')}"
        ),
        f"PRICE={ta.get('price')} VERDICT={ta.get('verdict')} CONF={ta.get('confidence')}/10",
        (
            f"VOLATILITY_REGIME={vol.get('regime')} horizon={vol.get('horizon')} "
            f"heat={vol.get('heat')} tp1_min={vol.get('tp1_min_pct')}% "
            f"tp1_pref={vol.get('tp1_prefer_pct')}% tp2_hint={vol.get('tp2_hint_pct')}% "
            f"| {vol.get('note')}"
        ),
        f"PHASE={ta.get('phase')} ({ta.get('phase_label')}) BIAS={ta.get('market_bias')} OI={ta.get('oi_narrative')}",
        f"STRUCTURE={ta.get('structure')} PRIORITY={ta.get('action_priority')}",
        f"ENTRY_ZONE={ta.get('entry_zone')} INV={ta.get('invalidation')} S={ta.get('support')} R={ta.get('resistance')}",
        f"PRIMARY_PATTERN={ta.get('primary_pattern')}",
        f"HTF_PATTERNS={ta.get('htf_chart_patterns')} PRIMARY_HTF={ta.get('primary_htf_pattern')}",
        f"PATTERN_FORESIGHT={ta.get('pattern_foresight')}",
        f"ELLIOTT={ta.get('elliott')} HTF_ELLIOTT={ta.get('htf_elliott')}",
        f"WAVE={ta.get('wave')} ABC={ta.get('abc')}",
        f"SETUP={ta.get('setup_confluence')}",
        f"SMC={ta.get('smc_summary')} score={ta.get('smc_score')}",
        f"FORECAST={ta.get('forecast_summary')}",
        f"CVD={ta.get('cvd_source')} delta={ta.get('cvd_delta')} LIQ_CASCADE={ta.get('liq_cascade')}",
        f"RSI_DIVERGENCE={ta.get('rsi')}",
        f"LIQ_MAGNET={ta.get('liq_magnet')}",
        f"MEANINGFUL_LEVELS={meaningful}",
        f"GATES={pack.get('decision_gate')}",
        f"PLAYBOOK={pack.get('playbook')}",
        f"POSITION_CALL={pack.get('position_call')}",
        f"LINKS chart={pack.get('chart_url')} liq_map={pack.get('liq_map_url')}",
        f"LIQ_MAP_SCREENSHOT={pack.get('liq_map_screenshot', 'unknown')} {pack.get('liq_map_note', '')}",
        "JSON:",
        json.dumps(pack, ensure_ascii=False, separators=(",", ":"))[:14000],
    ]
    return "\n".join(str(x) for x in lines if x is not None)


async def build_ai_context_pack(
    symbol: str,
    exchange: str = "bybit",
    *,
    hours: int = DEFAULT_HOURS,
    interval_minutes: int = 5,
    include_chart: bool = True,
    include_liq_map: bool = True,
    chart_source: str = "annotated",
) -> AiContextPack:
    from .chart_renderer import render_signal_chart
    from .chart_screenshot import chart_capture_service

    hours = hours if hours in ALLOWED_HOURS else DEFAULT_HOURS
    sym = symbol.upper().replace("/", "")
    ex = exchange or "bybit"
    chart_png: bytes | None = None
    ta: TAAnalysisResult | None = None

    if include_chart:
        try:
            chart_png, ta = await render_signal_chart(
                sym,
                side="long",
                hours=hours,
                interval_minutes=interval_minutes,
                chart_source=chart_source,
                exchange=ex,
                display_hours=hours,
            )
        except Exception:
            logger.exception("AI context chart failed %s %s", ex, sym)

    if ta is None:
        # Fallback: empty pack with links only
        pack_dict: dict[str, Any] = {
            "symbol": sym,
            "exchange": ex,
            "hours": hours,
            "interval_minutes": interval_minutes,
            "error": "Не удалось построить TA/график (нет свечей?)",
            "chart_url": coinglass_url(sym, ex),
            "liq_map_url": coinglass_liq_map_url(sym, ex),
        }
        return AiContextPack(
            symbol=sym,
            exchange=ex,
            hours=hours,
            interval_minutes=interval_minutes,
            context_text=format_context_text(pack_dict),
            context_dict=pack_dict,
            chart_url=pack_dict["chart_url"],
            liq_map_url=pack_dict["liq_map_url"],
        )

    pack_dict = {
        "symbol": sym,
        "exchange": ex,
        "hours": hours,
        "interval_minutes": interval_minutes,
        "price_fmt": fmt_price(ta.current_price) if ta.current_price else "",
        "chart_url": coinglass_url(sym, ex),
        "liq_map_url": coinglass_liq_map_url(sym, ex),
        "ta": serialize_ta(ta),
    }
    attach_gates(pack_dict, ta, sym, ex)
    pack_dict["multi_tf"] = build_multi_tf_map(interval_minutes)
    pack_dict["volatility_regime"] = build_volatility_regime(pack_dict["ta"])
    pack_dict["meaningful_levels"] = build_meaningful_levels(pack_dict["ta"])
    pack_dict["position_call"] = build_bot_position_call(pack_dict)
    liq_png: bytes | None = None
    liq_capture_ok = False
    if include_liq_map:
        try:
            liq_png = await chart_capture_service.capture_liquidation_heatmap(sym, ex)
            liq_capture_ok = bool(liq_png)
        except Exception:
            logger.debug("Liq map capture failed", exc_info=True)
            liq_png = None
    pack_dict["liq_map_screenshot"] = "ok" if liq_capture_ok else "unavailable"
    if not liq_capture_ok:
        pack_dict["liq_map_note"] = (
            "Скрин heatmap недоступен (CoinGlass блок/таймаут/пусто). "
            "Опирайся на LIQ_MAGNET и ссылку liq_map_url; не выдумывай 404-уровни с картинки."
        )

    return AiContextPack(
        symbol=sym,
        exchange=ex,
        hours=hours,
        interval_minutes=interval_minutes,
        context_text=format_context_text(pack_dict),
        context_dict=pack_dict,
        chart_png=chart_png,
        liq_map_png=liq_png,
        chart_url=pack_dict["chart_url"],
        liq_map_url=pack_dict["liq_map_url"],
        ta=ta,
    )
