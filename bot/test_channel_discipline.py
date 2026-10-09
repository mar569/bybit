from __future__ import annotations

import os
import time

from bot.channel_discipline import (
    ed_channel_discipline_enabled,
    ed_entries_only_enabled,
    manual_ta_blocks_signal_noise,
    signal_extra_messages_enabled,
)


def test_discipline_defaults() -> None:
    os.environ.pop("ED_CHANNEL_DISCIPLINE", None)
    assert ed_channel_discipline_enabled()
    assert not signal_extra_messages_enabled()


def test_entries_only_default_on() -> None:
    os.environ.pop("ED_ENTRIES_ONLY", None)
    assert ed_entries_only_enabled()


def test_entries_only_env_off() -> None:
    os.environ["ED_ENTRIES_ONLY"] = "0"
    try:
        class S:
            signal_telegram_entry_only = False
            signal_watch_mode_enabled = True

        assert not ed_entries_only_enabled(S())
    finally:
        os.environ.pop("ED_ENTRIES_ONLY", None)


def test_manual_ta_blocks_intel_window() -> None:
    sym = "BTCUSDT"
    last = {sym: time.time()}
    assert manual_ta_blocks_signal_noise(sym, last_manual=last)
    last[sym] = time.time() - 4000
    assert not manual_ta_blocks_signal_noise(sym, last_manual=last)
