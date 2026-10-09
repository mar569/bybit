"""Quiver Quant alt-data (congress, insider) — stub for INTEL enrichment."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class QuiverSnippet:
    ticker: str
    headline_ru: str
    source: str = "quiver"


class QuiverClient(Protocol):
    def congress_activity(self, ticker: str) -> QuiverSnippet | None: ...


class MockQuiverClient:
    def congress_activity(self, ticker: str) -> QuiverSnippet | None:
        return None


def quiver_intel_line(ticker: str, *, client: QuiverClient | None = None) -> str:
    """Optional alt-data row for playbook INTEL (equity-tagged tickers)."""
    base = ticker.strip().upper().removesuffix("USDT")
    if not base or len(base) > 6:
        return ""
    c = client or MockQuiverClient()
    snip = c.congress_activity(base)
    if not snip or not snip.headline_ru:
        return ""
    return snip.headline_ru[:120]
