"""Quiver Quant alt-data (congress, insider) for playbook INTEL."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


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


_quiver_client: QuiverClient | None = None
_warned_missing_key = False


def get_quiver_client() -> QuiverClient:
    global _quiver_client, _warned_missing_key
    if _quiver_client is not None:
        return _quiver_client
    from .http_client import HttpQuiverClient

    http = HttpQuiverClient.from_env()
    if http is not None:
        _quiver_client = http
        return _quiver_client
    if not _warned_missing_key:
        logger.debug("Quiver intel disabled: QUIVER_API_KEY not set")
        _warned_missing_key = True
    _quiver_client = MockQuiverClient()
    return _quiver_client


def quiver_intel_line(ticker: str, *, client: QuiverClient | None = None) -> str:
    """Optional alt-data row for playbook INTEL (equity-tagged tickers)."""
    base = ticker.strip().upper().removesuffix("USDT")
    if not base or len(base) > 6:
        return ""
    c = client or get_quiver_client()
    snip = c.congress_activity(base)
    if not snip or not snip.headline_ru:
        return ""
    return snip.headline_ru[:120]
