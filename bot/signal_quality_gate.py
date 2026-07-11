"""Фильтры качества сигналов: CVD, sweep, OI-flow, BTC regime, WATCH vs ENTRY."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Signal
    from .settings import ScannerSettings
    from .ta_analysis import TAAnalysisResult

_POST_DUMP_SHORT_TYPES = frozenset({
    "vertical_pump", "trend_dump", "reversal_dump", "impulse_dump", "mega_dump",
    "trend_pump", "reversal_pump", "impulse_pump", "mega_pump",
})
_FADE_TYPES = frozenset({
    "reversal_dump", "impulse_dump", "trend_dump", "vertical_dump",
    "reversal_pump", "impulse_pump", "trend_pump", "vertical_pump",
})


@dataclass(frozen=True)
class SignalQualityResult:
    tier: str  # entry | watch | skip
    block_reason: str = ""
    warnings: tuple[str, ...] = ()
    flow_label: str = ""
    cvd_ratio: float | None = None
    cvd_detail: str = ""


def classify_oi_price_flow(oi_pct: float, price_pct: float) -> tuple[str, str]:
    """Матрица OI+цена → нарратив и риск разворота."""
    oi = float(oi_pct or 0)
    px = float(price_pct or 0)
    if oi < -0.4 and px > 0.35:
        return "squeeze_risk", "OI↓ цена↑ — риск short squeeze"
    if oi > 0.4 and px < -0.35:
        return "long_unwind", "OI↑ цена↓ — новые шорты, но возможен отскок"
    if oi < -0.4 and px < -0.35:
        return "capitulation", "OI↓ цена↓ — закрытие лонгов, часто отскок"
    if oi > 0.4 and px > 0.35:
        return "aligned_long", "OI↑ цена↑ — aligned long"
    if oi < -0.4 and px < -0.15:
        return "aligned_short", "OI↓ цена↓ — aligned short"
    if abs(oi) < 0.3 and abs(px) >= 0.8:
        return "price_only", "движение без OI — слабее edge"
    return "mixed", "смешанный поток"


def _cvd_opposes(side: str, ratio: float | None, *, short_max: float, long_min: float) -> bool:
    if ratio is None:
        return False
    if side == "short":
        return ratio >= long_min
    if side == "long":
        return ratio <= short_max
    return False


def _sweep_opposes(side: str, smc: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(smc, dict):
        return False, ""
    if not smc.get("liquidity_sweep"):
        return False, ""
    sweep_dir = str(smc.get("sweep_direction", "none"))
    rev_ready = bool(smc.get("reversal_ready"))
    rev_dir = str(smc.get("reversal_direction", "none"))
    if side == "short" and sweep_dir == "long":
        msg = "sweep лоев → риск отскока вверх"
        if rev_ready and rev_dir == "long":
            msg = "sweep + reversal ready LONG"
        return True, msg
    if side == "long" and sweep_dir == "short":
        msg = "sweep хаев → риск отката вниз"
        if rev_ready and rev_dir == "short":
            msg = "sweep + reversal ready SHORT"
        return True, msg
    return False, ""


def _phase_opposes(side: str, ms: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(ms, dict):
        return False, ""
    phase = str(ms.get("phase", ""))
    dead_cat = bool(ms.get("dead_cat_bounce"))
    post_crash = bool(ms.get("post_crash"))
    if side == "short" and dead_cat and post_crash:
        return True, "dead-cat bounce — short рано"
    if side == "short" and phase in {"correction_up", "impulse_up"}:
        return True, f"фаза {phase} — short против импульса"
    if side == "long" and phase in {"correction_down", "impulse_down"}:
        return True, f"фаза {phase} — long против импульса"
    if side == "long" and post_crash and dead_cat:
        return True, "после обвала — long без подтверждения рискован"
    return False, ""


def _flow_matrix_blocks(side: str, flow_key: str) -> tuple[bool, str]:
    if side == "short" and flow_key == "squeeze_risk":
        return True, "OI↓ цена↑ — short squeeze"
    if side == "short" and flow_key == "capitulation":
        return True, "капитуляция лонгов — частый V-отскок"
    if side == "long" and flow_key == "long_unwind":
        return True, "OI↑ цена↓ — давление шортов, long рано"
    return False, ""


def _btc_opposes(side: str, btc_pct: float | None, *, block_pct: float) -> tuple[bool, str]:
    if btc_pct is None or block_pct <= 0:
        return False, ""
    if side == "short" and btc_pct >= block_pct:
        return True, f"BTC +{btc_pct:.2f}% — short альта рискован"
    if side == "long" and btc_pct <= -block_pct:
        return True, f"BTC {btc_pct:.2f}% — long альта рискован"
    return False, ""


def _htf_opposes(side: str, smc: dict[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(smc, dict):
        return False, ""
    htf = str(smc.get("htf_structure", "unknown"))
    if side == "short" and htf == "bullish":
        return True, "HTF bullish — short 5m против 1h"
    if side == "long" and htf == "bearish":
        return True, "HTF bearish — long 5m против 1h"
    return False, ""


def _funding_squeeze_blocks(
    side: str,
    funding: float | None,
    flow_key: str,
) -> tuple[bool, str]:
    if funding is None:
        return False, ""
    if side == "short" and flow_key == "squeeze_risk" and funding <= -0.0006:
        return True, "funding− + OI squeeze — short squeeze зона"
    if side == "long" and flow_key == "long_unwind" and funding >= 0.0010:
        return True, "funding+ + OI↑ цена↓ — long против потока"
    if side == "short" and funding <= -0.0012:
        return True, "экстремальный negative funding — squeeze риск"
    return False, ""


def _outcome_blocks_fade(
    signal_type: str,
    outcome_stats: tuple[int, float] | None,
    *,
    min_samples: int,
    min_winrate: float,
) -> tuple[bool, str]:
    if not outcome_stats or signal_type not in _FADE_TYPES:
        return False, ""
    samples, winrate = outcome_stats
    if samples < min_samples or winrate >= min_winrate:
        return False, ""
    return True, f"тип {signal_type} winrate {winrate:.0f}% ({samples} исх.) — слабый edge"


def assess_signal_quality(
    signal: Signal,
    *,
    ta: TAAnalysisResult | None = None,
    cvd_ratio: float | None = None,
    cvd_detail: str = "",
    btc_change_pct: float | None = None,
    settings: ScannerSettings,
    readiness: tuple[bool, str] | None = None,
    outcome_stats: tuple[int, float] | None = None,
) -> SignalQualityResult:
    """Классификация: skip (не слать), watch (ждать уровень), entry (готов)."""
    side = (signal.side or "long").lower()
    warnings: list[str] = []
    hard_blocks: list[str] = []

    if cvd_ratio is None:
        raw = signal.details.get("cvd_ratio")
        try:
            cvd_ratio = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            cvd_ratio = None
    if not cvd_detail:
        cvd_detail = str(signal.details.get("cvd_detail", "") or "")

    ms = signal.details.get("market_structure")
    smc = signal.details.get("smc")
    oi = float(signal.oi_change_percent or 0)
    px = float(signal.price_change_percent or 0)
    flow_key, flow_label = classify_oi_price_flow(oi, px)

    if not getattr(settings, "signal_quality_gate_enabled", True):
        ready = bool(readiness and readiness[0])
        return SignalQualityResult(
            tier="entry" if ready else "watch",
            flow_label=flow_label,
            cvd_ratio=cvd_ratio,
            cvd_detail=cvd_detail,
        )

    short_max = float(getattr(settings, "signal_cvd_short_max_ratio", 0.42))
    long_min = float(getattr(settings, "signal_cvd_long_min_ratio", 0.58))

    if getattr(settings, "signal_cvd_gate_enabled", True) and _cvd_opposes(
        side, cvd_ratio, short_max=short_max, long_min=long_min,
    ):
        pct = f"{cvd_ratio:.0%}" if cvd_ratio is not None else "?"
        hard_blocks.append(f"CVD {pct} buy против {side.upper()}")

    if getattr(settings, "signal_sweep_guard_enabled", True):
        sweep_block, sweep_msg = _sweep_opposes(side, smc if isinstance(smc, dict) else None)
        if sweep_block:
            is_fade = signal.signal_type in _FADE_TYPES
            if is_fade or (ta is not None and ta.verdict == "WAIT"):
                hard_blocks.append(sweep_msg)
            else:
                warnings.append(sweep_msg)

    phase_block, phase_msg = _phase_opposes(side, ms if isinstance(ms, dict) else None)
    if phase_block:
        if signal.signal_type in _FADE_TYPES:
            hard_blocks.append(phase_msg)
        else:
            warnings.append(phase_msg)

    if getattr(settings, "signal_flow_matrix_enabled", True):
        fm_block, fm_msg = _flow_matrix_blocks(side, flow_key)
        if fm_block and signal.signal_type in _FADE_TYPES:
            hard_blocks.append(fm_msg)
        elif fm_block:
            warnings.append(fm_msg)

    if getattr(settings, "signal_funding_squeeze_enabled", True):
        fund_block, fund_msg = _funding_squeeze_blocks(
            side, signal.funding_rate, flow_key,
        )
        if fund_block and signal.signal_type in _FADE_TYPES:
            hard_blocks.append(fund_msg)
        elif fund_block:
            warnings.append(fund_msg)

    if getattr(settings, "signal_htf_gate_enabled", True):
        htf_block, htf_msg = _htf_opposes(side, smc if isinstance(smc, dict) else None)
        if htf_block:
            cascade = signal.signal_type in {"liq_cascade_pump", "liq_cascade_dump"}
            if cascade:
                warnings.append(htf_msg)
            elif signal.signal_type in _FADE_TYPES:
                hard_blocks.append(htf_msg)
            else:
                warnings.append(htf_msg)

    if getattr(settings, "signal_btc_regime_filter_enabled", True):
        btc_block, btc_msg = _btc_opposes(
            side,
            btc_change_pct,
            block_pct=float(getattr(settings, "signal_btc_block_pct", 0.35)),
        )
        if btc_block and signal.signal_type not in {"liq_cascade_pump", "liq_cascade_dump"}:
            warnings.append(btc_msg)

    if ta is not None:
        from .ta_analysis import ta_conflicts_with_signal, ta_opposes_signal_direction

        if ta_conflicts_with_signal(ta, signal.side):
            hard_blocks.append("сканер vs TA — не входить по алерту")
        elif ta_opposes_signal_direction(ta, signal.side):
            warnings.append("TA priority против стороны сканера")

        if side == "short" and ta.momentum_pct >= 0.8 and ta.verdict != "SHORT":
            warnings.append(f"импульс вверх +{ta.momentum_pct:.1f}%")
        if side == "long" and ta.momentum_pct <= -0.8 and ta.verdict != "LONG":
            warnings.append(f"импульс вниз {ta.momentum_pct:.1f}%")

    if getattr(settings, "signal_outcome_feedback_enabled", True):
        ob, om = _outcome_blocks_fade(
            signal.signal_type,
            outcome_stats,
            min_samples=int(getattr(settings, "signal_outcome_min_samples", 12)),
            min_winrate=float(getattr(settings, "signal_outcome_min_winrate", 35.0)),
        )
        if ob:
            hard_blocks.append(om)

    if hard_blocks:
        return SignalQualityResult(
            tier="skip",
            block_reason=hard_blocks[0],
            warnings=tuple(dict.fromkeys(hard_blocks[1:] + warnings)),
            flow_label=flow_label,
            cvd_ratio=cvd_ratio,
            cvd_detail=cvd_detail,
        )

    ready = bool(readiness and readiness[0])
    if ready and not warnings:
        return SignalQualityResult(
            tier="entry",
            warnings=tuple(warnings),
            flow_label=flow_label,
            cvd_ratio=cvd_ratio,
            cvd_detail=cvd_detail,
        )

    if ready and len(warnings) <= 1:
        return SignalQualityResult(
            tier="entry",
            warnings=tuple(warnings),
            flow_label=flow_label,
            cvd_ratio=cvd_ratio,
            cvd_detail=cvd_detail,
        )

    if getattr(settings, "signal_watch_mode_enabled", True):
        return SignalQualityResult(
            tier="watch",
            block_reason=readiness[1] if readiness and not readiness[0] else "ждать подтверждение",
            warnings=tuple(warnings),
            flow_label=flow_label,
            cvd_ratio=cvd_ratio,
            cvd_detail=cvd_detail,
        )

    return SignalQualityResult(
        tier="skip",
        block_reason=readiness[1] if readiness else "не готов к входу",
        warnings=tuple(warnings),
        flow_label=flow_label,
        cvd_ratio=cvd_ratio,
        cvd_detail=cvd_detail,
    )


def format_quality_warnings_html(result: SignalQualityResult) -> str:
    lines: list[str] = []
    if result.flow_label:
        icon = "✅" if "aligned" in result.flow_label.lower() else "💧"
        lines.append(f"{icon} <b>Поток OI+цена:</b> {result.flow_label}")
    if result.cvd_detail:
        lines.append(f"📈 {result.cvd_detail}")
    elif result.cvd_ratio is not None:
        lines.append(f"📈 CVD: {result.cvd_ratio:.0%} buy")
    for w in result.warnings[:3]:
        lines.append(f"⚠️ {w}")
    if result.tier == "skip" and result.block_reason:
        lines.insert(0, f"🚫 <b>Блок:</b> {result.block_reason}")
    elif result.tier == "watch" and result.block_reason:
        lines.insert(0, f"👀 <b>WATCH</b> · {result.block_reason}")
    elif result.tier == "entry":
        lines.insert(0, "🎯 <b>ENTRY</b> · триггер по плану")
    return "\n".join(lines)


def attach_cvd_to_signal_details(details: dict[str, Any], snap: Any) -> None:
    if snap is None:
        return
    details["cvd_ratio"] = round(float(snap.ratio), 4)
    details["cvd_delta"] = round(float(snap.delta), 4)
    details["cvd_detail"] = str(snap.detail or "")
    details["cvd_source"] = str(getattr(snap, "source", "taker") or "taker")
    details["cvd_trade_count"] = int(snap.trade_count)
    details.update(snap.to_dict() if hasattr(snap, "to_dict") else {})


def format_manual_ta_flow_html(
    ta: TAAnalysisResult,
    *,
    cvd_snap: Any = None,
    cvd_short_max: float = 0.42,
    cvd_long_min: float = 0.58,
) -> str:
    """Блок потока для ручного TA — те же правила, что у сигналов."""
    lines: list[str] = []
    side = ta.verdict.lower() if ta.verdict in {"LONG", "SHORT"} else ta.action_priority
    if side not in {"long", "short"}:
        side = "long" if ta.action_priority == "long" else "short" if ta.action_priority == "short" else ""

    if cvd_snap is not None:
        ratio = float(cvd_snap.ratio)
        lines.append(f"📈 {cvd_snap.detail}")
        if side == "short" and ratio >= cvd_long_min:
            lines.append(f"⚠️ CVD {ratio:.0%} buy — SHORT против потока")
        elif side == "long" and ratio <= cvd_short_max:
            lines.append(f"⚠️ CVD {ratio:.0%} buy — LONG против потока")

    if ta.smc:
        if ta.smc.liquidity_sweep:
            lines.append(f"🧲 Sweep ликвидности · {ta.smc.sweep_direction}")
            if side == "short" and ta.smc.sweep_direction == "long":
                lines.append("⚠️ Sweep лоев — частый отскок, SHORT только по триггеру")
            if side == "long" and ta.smc.sweep_direction == "short":
                lines.append("⚠️ Sweep хаев — LONG только по триггеру")
        if ta.smc.reversal_ready:
            lines.append(
                f"♻️ Reversal ready · {ta.smc.reversal_direction} · {ta.smc.reversal_stage or 'setup'}"
            )
            if side and ta.smc.reversal_direction and side != ta.smc.reversal_direction:
                lines.append(f"⚠️ SMC готовит {ta.smc.reversal_direction.upper()} — не {side.upper()}")

    if ta.momentum_pct >= 0.9 and side == "short":
        lines.append(f"⚠️ Импульс вверх +{ta.momentum_pct:.1f}% — short без пробоя рискован")
    if ta.momentum_pct <= -0.9 and side == "long":
        lines.append(f"⚠️ Импульс вниз {ta.momentum_pct:.1f}% — long без пробоя рискован")

    return "\n".join(lines)
