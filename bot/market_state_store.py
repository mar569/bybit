"""Кэш последнего MarketState/сценария C по символу — стабильность алертов между прогонами TA."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ta_analysis import TAAnalysisResult


@dataclass(frozen=True)
class CachedMarketSnapshot:
    symbol: str
    updated_at: float
    verdict: str
    scenario_id: str
    scenario_action: str
    scenario_quality: str
    methodology_grade: str
    human_brief: str
    verdict_scenario_note: str
    interval_minutes: int = 5
    situation_kind: str = ""
    situational_snippet: str = ""
    current_price: float = 0.0
    verdict_confidence: int = 0

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.updated_at)


class MarketStateStore:
    def __init__(self, *, default_ttl_seconds: float = 900.0) -> None:
        self._ttl = float(default_ttl_seconds)
        self._data: dict[str, CachedMarketSnapshot] = {}

    def put_from_ta(
        self,
        symbol: str,
        ta: "TAAnalysisResult",
        *,
        interval_minutes: int = 5,
        ttl_seconds: float | None = None,
    ) -> None:
        sym = (symbol or "").upper().strip()
        if not sym:
            return
        metrics = getattr(ta, "market_metrics", None) or {}
        grade = ""
        if isinstance(metrics, dict):
            mw = metrics.get("methodology_weights")
            if isinstance(mw, dict):
                grade = str(mw.get("grade") or "")
        kind = str(getattr(ta, "situation_kind", "") or "").strip()
        if not kind:
            try:
                from .situational_brief import classify_situation

                kind = classify_situation(ta)
            except Exception:
                kind = "mixed"
        sit_plain = str(getattr(ta, "situational_brief_plain", "") or "").strip()
        snap = CachedMarketSnapshot(
            symbol=sym,
            updated_at=time.time(),
            verdict=(getattr(ta, "verdict", "") or "WAIT").upper(),
            scenario_id=str(getattr(ta, "scenario_engine_id", "") or ""),
            scenario_action=str(getattr(ta, "scenario_engine_action", "") or "").upper(),
            scenario_quality=str(getattr(ta, "scenario_engine_quality", "") or "").upper(),
            methodology_grade=grade.upper(),
            human_brief=str(getattr(ta, "human_trade_brief", "") or "")[:400],
            verdict_scenario_note=str(getattr(ta, "verdict_scenario_note", "") or "")[:200],
            interval_minutes=int(interval_minutes or 5),
            situation_kind=kind,
            situational_snippet=sit_plain[:220],
            current_price=float(getattr(ta, "current_price", 0) or 0),
            verdict_confidence=int(getattr(ta, "verdict_confidence", 0) or 0),
        )
        self._data[sym] = snap
        self._ttl = float(ttl_seconds or self._ttl)

    def list_active(self, *, limit: int = 100) -> list[CachedMarketSnapshot]:
        out: list[CachedMarketSnapshot] = []
        for sym, snap in list(self._data.items()):
            if snap.age_seconds > self._ttl:
                self._data.pop(sym, None)
                continue
            out.append(snap)
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out[: max(1, int(limit))]

    def count_active(self) -> int:
        return len(self.list_active(limit=10_000))

    def get(self, symbol: str) -> CachedMarketSnapshot | None:
        sym = (symbol or "").upper().strip()
        snap = self._data.get(sym)
        if snap is None:
            return None
        if snap.age_seconds > self._ttl:
            self._data.pop(sym, None)
            return None
        return snap

    def side_conflict_note(self, symbol: str, side: str) -> str:
        """Если свежий кэш против стороны сканера — короткая причина WATCH."""
        side = (side or "").lower()
        snap = self.get(symbol)
        if snap is None or side not in {"long", "short"}:
            return ""
        act = snap.scenario_action
        if act in {"LONG", "SHORT"}:
            want = "long" if act == "LONG" else "short"
            if side != want:
                return (
                    f"Кэш сценария ({int(snap.age_seconds)}с): {act} — "
                    f"сканер {side.upper()} не в плане."
                )
        if snap.methodology_grade == "F" and side in {"long", "short"}:
            return "Кэш: методология F — только наблюдение."
        if snap.scenario_quality == "F":
            return "Кэш: сценарий F — без ENTRY."
        return ""


_STORE = MarketStateStore()


def get_market_state_store() -> MarketStateStore:
    return _STORE
