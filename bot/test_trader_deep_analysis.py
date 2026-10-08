from types import SimpleNamespace

from bot.trader_deep_analysis import build_trader_deep_analysis_html


def test_compact_rbr_caption_no_wall_of_text():
    signal = SimpleNamespace(
        symbol="FIGHTUSDT",
        exchange="bybit",
        side="short",
        link="https://example.com",
    )
    ta = type(
        "TA",
        (),
        {
            "verdict_confidence": 7,
            "analysis_interval_minutes": 30,
            "market_metrics": {
                "range_breakdown_retest": {
                    "label_ru": "шорт от верха боковика",
                    "phase": "fade_top",
                    "range_bottom": 0.003606196,
                    "range_top": 0.003887943,
                    "entry_lo": 0.00381796,
                    "entry_hi": 0.003911271,
                    "stop": 0.003934598,
                    "targets": [0.003606196, 0.003563],
                    "direction": "short",
                },
                "chart_scanner_interval": 5,
            },
            "reading_tf_stack": "W1 боковик · H4 боковик · H1 боковик · M15 вверх",
            "drawdown_from_high_pct": 12.0,
            "post_pump": True,
        },
    )()
    html = build_trader_deep_analysis_html(signal, ta)  # type: ignore[arg-type]
    assert "Контекст:" not in html
    assert "💬 Разбор" not in html
    assert "0.003606196" not in html
    assert "реакцию" in html.lower()
    assert "не в зелёный импульс" in html.lower()
