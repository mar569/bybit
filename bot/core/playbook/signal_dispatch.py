"""Playbook-centric signal delivery (one evaluation, gates read result)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .cache import get_or_run_playbook
from .states import PlaybookState
from ...chart_display_policy import ed_playbook_v3_enabled

if TYPE_CHECKING:
    from ...models import Signal
    from ...ta_analysis import TAAnalysisResult
    from ...trade_decision_gate import TradeDecision
    from .engine import PlaybookResult


@dataclass(frozen=True)
class SignalPlaybookContext:
    result: PlaybookResult
    rbr_watch: bool
    block_stale_alert: bool
    intel_html: str


@dataclass(frozen=True)
class PlaybookDeliveryDecision:
    """Unified gates for telegram dispatch (read once per signal+TA)."""
    context: SignalPlaybookContext
    rbr_watch: bool
    allow_rbr_watch_alert: bool
    force_watch_tier: bool
    skip_trade_gate_return: bool
    caption_html: str


def evaluate_signal_playbook(
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> SignalPlaybookContext | None:
    if not ed_playbook_v3_enabled():
        return None
    from ...chart_display_policy import ed_playbook_intel_panel_enabled
    from .brief import format_intel_matrix_html

    pb = get_or_run_playbook(ta, symbol=symbol)
    stale_block = pb.state == PlaybookState.NO_TRADE and bool(pb.block_reason)
    intel = format_intel_matrix_html(pb) if ed_playbook_intel_panel_enabled() else ""
    return SignalPlaybookContext(
        result=pb,
        rbr_watch=bool(pb.alert_eligible),
        block_stale_alert=stale_block,
        intel_html=intel,
    )


def delivery_from_context(ctx: SignalPlaybookContext) -> PlaybookDeliveryDecision:
    pb = ctx.result
    rbr = ctx.rbr_watch and not ctx.block_stale_alert
    return PlaybookDeliveryDecision(
        context=ctx,
        rbr_watch=rbr,
        allow_rbr_watch_alert=rbr,
        force_watch_tier=rbr,
        skip_trade_gate_return=bool(ctx.block_stale_alert and not rbr),
        caption_html=pb.body_html,
    )


def resolve_playbook_delivery(
    ta: TAAnalysisResult,
    signal: "Signal",
) -> PlaybookDeliveryDecision | None:
    ctx = evaluate_signal_playbook(ta, symbol=signal.symbol)
    if ctx is None:
        return None
    return delivery_from_context(ctx)


def stamp_playbook_on_signal(
    signal: "Signal",
    ta: TAAnalysisResult,
    *,
    symbol: str = "",
) -> SignalPlaybookContext | None:
    ctx = evaluate_signal_playbook(ta, symbol=symbol or signal.symbol)
    if ctx is None:
        return None
    details: dict[str, Any] = dict(signal.details or {})
    pb = ctx.result
    details["playbook_state"] = pb.state.value
    details["playbook_alert_eligible"] = pb.alert_eligible
    details["playbook_rbr_watch"] = ctx.rbr_watch
    if pb.block_reason:
        details["playbook_block"] = pb.block_reason
    details["playbook_headline"] = pb.headline_ru
    signal.details = details
    return ctx


def playbook_rbr_watch(ta: "TAAnalysisResult", delivery: PlaybookDeliveryDecision | None) -> bool:
    if delivery is not None:
        return delivery.rbr_watch
    from ...range_breakdown_retest import rbr_alert_eligible

    return bool(rbr_alert_eligible(ta))


def merge_playbook_trade_decision(
    delivery: PlaybookDeliveryDecision | None,
    trade_decision: "TradeDecision | None",
    *,
    default_reason: str = "",
) -> "TradeDecision | None":
    from ...channel_discipline import ed_entries_only_enabled

    if ed_entries_only_enabled():
        return trade_decision
    if delivery is None or not delivery.force_watch_tier or trade_decision is None:
        return trade_decision
    if (trade_decision.action or "").lower() != "skip":
        return trade_decision
    from ...trade_decision_gate import TradeDecision

    return TradeDecision(
        "watch",
        default_reason or trade_decision.reason or "RBR — ждём реакцию",
        location=trade_decision.location,
        setup_score=max(int(trade_decision.setup_score or 0), 32),
    )


def playbook_caption_supplement(ctx: SignalPlaybookContext | None) -> str:
    if ctx is None or not ctx.intel_html:
        return ""
    return ctx.intel_html
