from bot.settings import ScannerSettings
from bot.signal_chart_policy import effective_signal_chart_interval_minutes


def test_chart_interval_syncs_with_long_period() -> None:
    s = ScannerSettings(
        signal_chart_interval_minutes=5,
        long_period_minutes=15,
        bybit_long_period_minutes=15,
    )
    assert effective_signal_chart_interval_minutes(s, exchange="bybit") == 15


def test_chart_interval_respects_explicit_higher_chart_tf() -> None:
    s = ScannerSettings(
        signal_chart_interval_minutes=30,
        long_period_minutes=15,
    )
    assert effective_signal_chart_interval_minutes(s) == 30
