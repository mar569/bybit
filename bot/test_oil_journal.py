"""Tests for oil entry checklist, pre-open window, outcome journal + learning."""
from __future__ import annotations

from pathlib import Path

from bot.oil_journal import (
    OilSetupJournal,
    OilSetupWatch,
    adaptive_min_quality,
    compute_outcome_stats,
    extract_catalyst_tags,
    format_outcome_message,
    gemini_memory_block,
    resolve_watch_against_price,
    risk_checklist_lines,
)
from bot.oil_session import should_send_preopen_alert


def test_risk_checklist_has_core_fields():
    lines = risk_checklist_lines(
        side="SHORT",
        price=84.0,
        entry_lo=84.2,
        entry_hi=84.8,
        stop=86.0,
        tp1=82.0,
        tp2=80.0,
        invalidation=86.0,
        catalyst="Trump TACOs on Iran attacks",
    )
    text = "\n".join(lines)
    assert "Чеклист" in text
    assert "SHORT" in text or "продажа" in text
    assert "Стоп" in text
    assert "80" in text or "82" in text
    assert "депозита" in text
    assert "Иран" in text or "TACO" in text


def test_resolve_short_hit_tp_and_sl():
    w = OilSetupWatch(
        side="SHORT",
        entry=84.5,
        stop=86.0,
        tp1=82.0,
        tp2=80.0,
        price_at_signal=84.5,
        catalyst="test",
        created_ts=1.0,
    )
    assert resolve_watch_against_price(w, 81.5) == "hit_tp"
    assert resolve_watch_against_price(w, 86.5) == "hit_sl"
    assert resolve_watch_against_price(w, 84.0) is None


def test_journal_registers_and_expires(tmp_path: Path):
    path = tmp_path / "oil_outcomes.json"
    j = OilSetupJournal(path=path)
    w = j.register(
        side="LONG",
        entry=80.0,
        stop=79.0,
        tp1=82.0,
        tp2=None,
        price=80.0,
        catalyst="geo hormuz iran",
    )
    assert w is not None
    assert "hormuz" in w.tags
    assert len(j.active()) == 1
    # Force old
    w.created_ts = 1.0
    done = j.check_price(80.5, min_age_sec=0, max_age_sec=10)
    assert done and done[0].outcome == "expired"
    msg = format_outcome_message(done[0], price_now=80.5, stats=j.stats())
    assert "время вышло" in msg.lower() or "Время вышло" in msg
    assert path.exists()
    # reload
    j2 = OilSetupJournal(path=path)
    assert j2.stats().expired >= 1


def test_expire_without_price_when_session_closed(tmp_path: Path):
    path = tmp_path / "oil_outcomes.json"
    j = OilSetupJournal(path=path)
    w = j.register(
        side="SHORT",
        entry=84.0,
        stop=85.0,
        tp1=82.0,
        tp2=None,
        price=84.0,
        catalyst="eia stocks",
        source="micro",
    )
    assert w is not None
    w.created_ts = 1.0
    done = j.check_price(0.0, min_age_sec=0, max_age_sec=10, allow_price_resolve=False)
    assert done and done[0].outcome == "expired"


def test_adaptive_quality_and_memory():
    hist = []
    for i in range(10):
        hist.append(
            OilSetupWatch(
                side="LONG",
                entry=80,
                stop=79,
                tp1=82,
                price_at_signal=80,
                catalyst="hormuz closed",
                created_ts=1.0 + i,
                resolved=True,
                outcome="hit_sl" if i < 7 else "hit_tp",
            )
        )
    st = compute_outcome_stats(hist)
    assert st.losses == 7
    assert st.wins == 3
    assert adaptive_min_quality(7, st) == 8  # stricter
    mem = gemini_memory_block(st)
    assert "Опыт" in mem
    assert "hormuz" in mem.lower() or "Ормуз" in st.recent_lesson_ru or "Урок" in mem


def test_extract_tags():
    assert "iran" in extract_catalyst_tags("Iran threatens Hormuz")
    assert "hormuz" in extract_catalyst_tags("Iran threatens Hormuz")
    assert "eia" in extract_catalyst_tags("EIA crude inventory draw")


def test_preopen_window_logic():
    from datetime import datetime, timezone, timedelta

    msk = timezone(timedelta(hours=3))
    monday = datetime(2026, 8, 3, 15, 0, tzinfo=msk)
    assert should_send_preopen_alert(now=monday) is False
    pre = datetime(2026, 8, 3, 0, 15, tzinfo=msk)
    assert should_send_preopen_alert(now=pre) is True
