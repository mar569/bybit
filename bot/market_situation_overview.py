"""Сводка situation_kind по монетам из кэша TA (глобальный «пульс» рынка)."""
from __future__ import annotations

from collections import defaultdict
from html import escape

from .market_state_store import CachedMarketSnapshot, MarketStateStore, get_market_state_store
from .ta_analysis import fmt_price

SITUATION_LABEL_RU: dict[str, str] = {
    "htf_resistance_short": "Сопротивление · шорт (HTF)",
    "impulse_wait": "Импульс — не догонять",
    "inside_consolidation": "В консолидации",
    "below_consolidation": "Под диапазоном",
    "above_consolidation": "Над диапазоном",
    "range_middle": "Середина range",
    "trend_continuation": "По тренду",
    "reversal_setup": "Разворот",
    "mixed": "Смешанная картина",
}

# Приоритет в дайджесте: сначала «actionable» контексты
_KIND_ORDER: tuple[str, ...] = (
    "htf_resistance_short",
    "reversal_setup",
    "inside_consolidation",
    "above_consolidation",
    "below_consolidation",
    "impulse_wait",
    "trend_continuation",
    "range_middle",
    "mixed",
)


def _label(kind: str) -> str:
    return SITUATION_LABEL_RU.get(kind, kind.replace("_", " "))


def group_snapshots_by_situation(
    snapshots: list[CachedMarketSnapshot],
) -> dict[str, list[CachedMarketSnapshot]]:
    buckets: dict[str, list[CachedMarketSnapshot]] = defaultdict(list)
    for snap in snapshots:
        kind = (snap.situation_kind or "mixed").strip() or "mixed"
        buckets[kind].append(snap)
    for items in buckets.values():
        items.sort(key=lambda s: s.updated_at, reverse=True)
    return dict(buckets)


def build_situation_overview_html(
    snapshots: list[CachedMarketSnapshot],
    *,
    max_per_kind: int = 5,
    title: str = "Обзор ситуаций",
) -> str:
    if not snapshots:
        return (
            "🌐 <b>Обзор ситуаций</b>\n"
            "Пока нет свежих разборов — появятся после сигналов, manual TA или /chart."
        )
    buckets = group_snapshots_by_situation(snapshots)
    total = len(snapshots)
    lines: list[str] = [
        f"🌐 <b>{escape(title)}</b> · <b>{total}</b> монет (кэш TA, не все рынки)",
        "<i>Бот группирует по situation_kind — у каждой монеты свой сценарий, не один шаблон.</i>",
    ]
    kinds = sorted(
        buckets.keys(),
        key=lambda k: (
            _KIND_ORDER.index(k) if k in _KIND_ORDER else 99,
            -len(buckets[k]),
        ),
    )
    for kind in kinds:
        items = buckets[kind]
        lines.append(f"\n<b>{escape(_label(kind))}</b> — {len(items)}")
        for snap in items[: max(1, max_per_kind)]:
            sym = escape(snap.symbol)
            verdict = escape(snap.verdict or "WAIT")
            age_m = max(0, int(snap.age_seconds // 60))
            note = (snap.situational_snippet or snap.human_brief or "").strip()
            if len(note) > 72:
                note = note[:69] + "…"
            note = escape(note) if note else "—"
            px = ""
            if snap.current_price and snap.current_price > 0:
                px = f" · {fmt_price(snap.current_price)}"
            lines.append(f"• <b>{sym}</b> {verdict}{px} · {note} <i>({age_m}м)</i>")
        extra = len(items) - max_per_kind
        if extra > 0:
            lines.append(f"<i>… ещё {extra}</i>")
    return "\n".join(lines)


def build_situation_overview_from_store(
    store: MarketStateStore | None = None,
    *,
    max_symbols: int = 80,
    max_per_kind: int = 5,
) -> str:
    store = store or get_market_state_store()
    snaps = store.list_active(limit=max_symbols)
    return build_situation_overview_html(snaps, max_per_kind=max_per_kind)
