"""Срез свечей «как на скрине» — анализ до выбранной свечи, не до последней."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, TypeVar

from .bybit_klines import KlineBar

T = TypeVar("T")


@dataclass(frozen=True)
class BarReplaySelection:
    end_index: int
    open_time_ms: float
    label_ru: str


def resolve_as_of_index(
    bars: Sequence[KlineBar],
    *,
    bar_index: int | None = None,
    open_time_ms: int | float | None = None,
    price: float | None = None,
) -> BarReplaySelection | None:
    if not bars:
        return None
    n = len(bars)

    if bar_index is not None:
        idx = int(bar_index)
        if idx < 0:
            idx = n + idx
        idx = max(0, min(n - 1, idx))
        ts = float(getattr(bars[idx], "open_time", 0) or 0)
        return BarReplaySelection(
            idx,
            ts,
            f"срез до свечи {idx + 1}/{n} (индекс {bar_index})",
        )

    if open_time_ms is not None:
        target = float(open_time_ms)
        best_i = 0
        best_dt = 10**18
        for i, b in enumerate(bars):
            t = float(getattr(b, "open_time", 0) or 0)
            if t <= target and target - t < best_dt:
                best_dt = target - t
                best_i = i
        ts = float(getattr(bars[best_i], "open_time", 0) or 0)
        return BarReplaySelection(
            best_i,
            ts,
            f"срез до {int(ts)} ms",
        )

    if price is not None and float(price) > 0:
        target = float(price)
        best_i = 0
        best_err = 10**18
        for i, b in enumerate(bars):
            c = float(getattr(b, "close", 0) or 0)
            err = abs(c - target)
            if err < best_err:
                best_err = err
                best_i = i
        tol_pct = abs(float(bars[best_i].close) - target) / target * 100.0
        if tol_pct > 2.5:
            return None
        ts = float(getattr(bars[best_i], "open_time", 0) or 0)
        return BarReplaySelection(
            best_i,
            ts,
            f"срез по цене ~{target:g} (close {float(bars[best_i].close):g}, свеча {best_i + 1}/{n})",
        )
    return None


def trim_bars(bars: list[KlineBar], end_index: int) -> list[KlineBar]:
    if not bars:
        return []
    end_index = max(0, min(len(bars) - 1, end_index))
    return list(bars[: end_index + 1])


def trim_bars_to_time(bars: list[KlineBar] | None, as_of_open_time_ms: float) -> list[KlineBar] | None:
    if not bars:
        return None
    cutoff = float(as_of_open_time_ms)
    out = [b for b in bars if float(getattr(b, "open_time", 0) or 0) <= cutoff]
    return out if len(out) >= 8 else None


def parse_replay_from_text(text: str) -> tuple[str, int | None, float | None, int | None]:
    """
    Вырезает из строки manual TA маркеры replay.
    Возвращает (очищенный текст, bar_index, price, open_time_ms).
    """
    raw = (text or "").strip()
    bar_idx: int | None = None
    price: float | None = None
    open_ms: int | None = None

    m = re.search(
        r"(?:\b(?:бар|bar|свеча)\s*([+-]?\d+)\b|#([+-]?\d+)\b)",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        bar_idx = int(m.group(1) or m.group(2))
        raw = (raw[: m.start()] + raw[m.end() :]).strip(" ,.;:")

    m = re.search(r"@([0-9]+(?:\.[0-9]+)?)", raw)
    if m:
        try:
            price = float(m.group(1))
        except ValueError:
            price = None
        raw = (raw[: m.start()] + raw[m.end() :]).strip(" ,.;:")

    m = re.search(r"\b(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2})\b", raw)
    if m:
        from datetime import datetime, timezone

        try:
            dt = datetime.fromisoformat(m.group(1).replace(" ", "T"))
            open_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            open_ms = None
        raw = (raw[: m.start()] + raw[m.end() :]).strip(" ,.;:")

    return raw, bar_idx, price, open_ms
