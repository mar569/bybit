from __future__ import annotations

from bot.core.playbook.brief import format_intel_matrix_html_from_rows
from bot.core.playbook.signal_dispatch import evaluate_signal_playbook, stamp_playbook_on_signal
from bot.core.playbook.states import PlaybookState
from bot.models import Signal
from bot.ta_analysis import TAAnalysisResult


def test_intel_matrix_always_four_rows() -> None:
    html = format_intel_matrix_html_from_rows([("Цена", "импульс")], mandatory_four=True)
    assert "INTEL" in html
    assert html.count("▫️") == 4
    assert "—" in html


def test_stamp_playbook_on_signal() -> None:
    ta = TAAnalysisResult(current_price=1.0, verdict="WAIT")
    sig = Signal(
        exchange="bybit",
        symbol="BTCUSDT",
        signal_type="vertical_dump",
        oi_period_minutes=5,
        oi_change_percent=0.0,
        oi_change_value=0.0,
        oi_change_usd=None,
        oi_direction="flat",
        signals_today=1,
        price_change_percent=-1.0,
        price_change_value=None,
        price_direction="down",
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
        side="short",
        current_price=None,
        current_open_interest=None,
        link="",
        details={},
    )
    ctx = stamp_playbook_on_signal(sig, ta)
    assert ctx is not None
    assert sig.details.get("playbook_state") == PlaybookState.OBSERVE.value
    again = evaluate_signal_playbook(ta, symbol="BTCUSDT")
    assert again is not None
    assert again.result.state == PlaybookState.OBSERVE
