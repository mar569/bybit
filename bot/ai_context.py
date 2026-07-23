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
    }


def serialize_ta(ta: TAAnalysisResult) -> dict[str, Any]:
    fib = [
        {"ratio": _round(getattr(fl, "ratio", 0), 3), "price": _round(getattr(fl, "price", None)), "kind": getattr(fl, "kind", "")}
        for fl in (ta.fib_levels or [])[:12]
    ]
    patterns = [_pattern_brief(p) for p in (ta.chart_patterns or [])[:8]]
    primary = _pattern_brief(ta.primary_chart_pattern) if ta.primary_chart_pattern else None
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
            "global_label": ta.elliott_global_label,
            "local_label": ta.elliott_local_label,
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


def format_context_text(pack: dict[str, Any]) -> str:
    """Human+JSON hybrid for the model (compact)."""
    sym = pack.get("symbol", "?")
    ex = pack.get("exchange", "?")
    hours = pack.get("hours", 24)
    ta = pack.get("ta") or {}
    lines = [
        f"SYMBOL={sym} EXCHANGE={ex} WINDOW={hours}h TF={pack.get('interval_minutes', 5)}m",
        f"PRICE={ta.get('price')} VERDICT={ta.get('verdict')} CONF={ta.get('confidence')}/10",
        f"PHASE={ta.get('phase')} ({ta.get('phase_label')}) BIAS={ta.get('market_bias')} OI={ta.get('oi_narrative')}",
        f"STRUCTURE={ta.get('structure')} PRIORITY={ta.get('action_priority')}",
        f"ENTRY_ZONE={ta.get('entry_zone')} INV={ta.get('invalidation')} S={ta.get('support')} R={ta.get('resistance')}",
        f"PRIMARY_PATTERN={ta.get('primary_pattern')}",
        f"ELLIOTT={ta.get('elliott')}",
        f"WAVE={ta.get('wave')} ABC={ta.get('abc')}",
        f"SETUP={ta.get('setup_confluence')}",
        f"SMC={ta.get('smc_summary')} score={ta.get('smc_score')}",
        f"FORECAST={ta.get('forecast_summary')}",
        f"CVD={ta.get('cvd_source')} delta={ta.get('cvd_delta')} LIQ_CASCADE={ta.get('liq_cascade')}",
        f"LIQ_MAGNET={ta.get('liq_magnet')}",
        f"GATES={pack.get('decision_gate')}",
        f"PLAYBOOK={pack.get('playbook')}",
        f"LINKS chart={pack.get('chart_url')} liq_map={pack.get('liq_map_url')}",
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

    liq_png: bytes | None = None
    if include_liq_map:
        try:
            liq_png = await chart_capture_service.capture_coinglass(pack_dict["liq_map_url"])
        except Exception:
            logger.debug("Liq map capture failed", exc_info=True)

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
