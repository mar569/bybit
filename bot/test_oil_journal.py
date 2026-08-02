"""Tests for oil entry checklist, pre-open window, outcome journal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bot.oil_journal import (
    OilSetupJournal,
    OilSetupWatch,
    format_outcome_message,
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


def test_journal_registers_and_expires():
    j = OilSetupJournal()
    w = j.register(
        side="LONG",
        entry=80.0,
        stop=79.0,
        tp1=82.0,
        tp2=None,
        price=80.0,
        catalyst="geo",
    )
    assert w is not None
    assert len(j.active()) == 1
    # Force old
    w.created_ts = 1.0
    done = j.check_price(80.5, min_age_sec=0, max_age_sec=10)
    assert done and done[0].outcome == "expired"
    msg = format_outcome_message(done[0], price_now=80.5)
    assert "время вышло" in msg.lower() or "Время вышло" in msg


def test_preopen_window_logic():
    from datetime import datetime, timezone, timedelta
    from bot.oil_session import should_send_preopen_alert

    msk = timezone(timedelta(hours=3))
    # Понедельник день — рынок уже открыт / не в окне pre-open выходных
    monday = datetime(2026, 8, 3, 15, 0, tzinfo=msk)
    assert should_send_preopen_alert(now=monday) is False
    # За 45 мин до пн 01:00
    pre = datetime(2026, 8, 3, 0, 15, tzinfo=msk)
    assert should_send_preopen_alert(now=pre) is True
