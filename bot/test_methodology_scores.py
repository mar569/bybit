from __future__ import annotations

from bot.exhaustion_detector import ExhaustionSignal
from bot.market_state import MarketState
from bot.methodology_scores import score_methodology
from bot.smc_pdf_checklist import SmcPdfChecklist
from bot.asset_passport import AssetPassport


def test_methodology_grade_b_with_votes() -> None:
    state = MarketState(
        passport=AssetPassport("ETHUSDT", "major", "test", "", "", "neutral"),
        htf_structure="bullish",
        smc_checklist=SmcPdfChecklist(
            True, True, "bos", True, True, 4, True, "ok"
        ),
        entry_quality="good",
        exhaustion=ExhaustionSignal(False, "none", "", False),
    )
    m = score_methodology(state, flow_continuation=65, flow_correction=48)
    assert m.grade in {"A", "B", "C"}
    assert m.total > 0.3
