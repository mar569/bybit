"""Прогон цепочки: свечи → TA → human brief → WATCH (как в Telegram analysis)."""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# evidence-first как в .env
os.environ.setdefault("EVIDENCE_READING_ENABLED", "true")

from bot.bybit_klines import fetch_bybit_klines_sync
from bot.human_trade_brief import build_human_trade_brief, scanner_side_blocked_by_scenario
from bot.models import Signal
from bot.proactive_intel import build_proactive_intel_html
from bot.settings import SettingsManager
from bot.signal_quality_gate import assess_signal_quality
from bot.ta_analysis import evaluate_entry_readiness, run_ta_analysis


def _load_stack(symbol: str) -> dict:
    sym = symbol.upper()
    return {
        "m5": fetch_bybit_klines_sync(sym, interval="5", limit=300),
        "m15": fetch_bybit_klines_sync(sym, interval="15", limit=200),
        "h1": fetch_bybit_klines_sync(sym, interval="60", limit=200),
        "h4": fetch_bybit_klines_sync(sym, interval="240", limit=120),
        "btc_h1": fetch_bybit_klines_sync("BTCUSDT", interval="60", limit=120),
    }


def run_symbol(symbol: str, *, side: str = "long") -> None:
    print("=" * 72)
    print(f"  {symbol} · симуляция WATCH после сканера ({side.upper()})")
    print("=" * 72)
    stack = _load_stack(symbol)
    m5 = stack["m5"]
    if len(m5) < 30:
        print("  Недостаточно свечей Bybit")
        return

    ta = run_ta_analysis(
        m5,
        symbol=symbol,
        neutral=True,
        htf_bars=stack["h1"],
        mid_bars=stack["m15"],
        macro_bars=stack["h4"],
        btc_bars=stack["btc_h1"],
        interval_minutes=5,
        htf_interval_minutes=60,
        mid_interval_minutes=15,
        macro_interval_minutes=240,
    )

    brief = build_human_trade_brief(ta, symbol=symbol)
    sit = str(getattr(ta, "situational_brief_plain", "") or "")
    print("\n--- Human brief (plain) ---\n")
    print(brief)
    if sit:
        print("\n--- Situational ---\n")
        print(sit)

    print("\n--- Поля решения ---")
    print(f"  verdict={ta.verdict}  conf={getattr(ta, 'verdict_confidence', 0)}")
    print(f"  scenario_action={getattr(ta, 'scenario_engine_action', '')}")
    print(f"  seek={getattr(ta, 'reading_seek_label', '')}")
    present = getattr(ta, "reading_present", []) or []
    absent = getattr(ta, "reading_absent", []) or []
    if present:
        print(f"  есть: {present[:4]}")
    if absent:
        print(f"  нет/ждём: {absent[:4]}")
    mm = getattr(ta, "market_metrics", None) or {}
    if isinstance(mm, dict) and mm.get("btc_regime"):
        print(
            f"  BTC regime={mm.get('btc_regime')} "
            f"({mm.get('btc_regime_chg_pct', 0):+.2f}%)"
        )

    block_long = scanner_side_blocked_by_scenario(ta, "long")
    block_short = scanner_side_blocked_by_scenario(ta, "short")
    print("\n--- Gates (market ENTRY со сканера) ---")
    print(f"  LONG:  {'OK' if not block_long else 'BLOCK — ' + block_long}")
    print(f"  SHORT: {'OK' if not block_short else 'BLOCK — ' + block_short}")

    px = float(m5[-1].close)
    sig = Signal(
        exchange="Binance",
        symbol=symbol,
        signal_type="trend_pump" if side == "long" else "trend_dump",
        oi_period_minutes=10,
        oi_change_percent=5.2,
        oi_change_value=0.0,
        oi_change_usd=80_000.0,
        oi_direction="up" if side == "long" else "down",
        signals_today=1,
        price_change_percent=2.1 if side == "long" else -2.1,
        price_change_value=None,
        price_direction="up" if side == "long" else "down",
        volume_change_percent=12.0,
        trade_count=None,
        spread=None,
        funding_rate=None,
        liquidation_estimate=None,
        vwap=None,
        atr=None,
        rsi=None,
        ema_short=None,
        ema_long=None,
        volume_24h=None,
        volume_speed=None,
        signal_score=4,
        side=side,
        current_price=px,
        current_open_interest=1_000_000.0,
        link="",
        details={"quality_tier": "watch"},
    )
    sm = SettingsManager()
    q = assess_signal_quality(sig, ta=ta, settings=sm.settings)
    print(f"\n--- Signal quality tier: {q.tier} ---")
    if q.block_reason:
        print(f"  reason: {q.block_reason}")
    if q.warnings:
        print(f"  warnings: {q.warnings[:3]}")

    s = sm.settings
    ready, why = evaluate_entry_readiness(
        ta,
        sig.side,
        sig.signal_score,
        min_ta_score=s.actionable_min_ta_score,
        max_trigger_dist_pct=s.actionable_max_trigger_dist_pct,
        min_timing_score=s.actionable_min_signal_score,
        max_timing_score=s.actionable_max_signal_score,
        require_smc=s.actionable_require_smc,
        check_scanner_timing=True,
        signal_type=sig.signal_type,
        accept_armed=s.actionable_accept_armed,
    )
    print(f"\n--- Actionable filter (как в боте) ---")
    print(f"  TA порог: {s.actionable_min_ta_score}/10")
    print(f"  {'PASS' if ready else 'SKIP'}: {why or 'ok'}")

    html = build_proactive_intel_html(sig, ta, quality_reason=q.block_reason or "")
    print("\n--- Telegram analysis (HTML, stripped tags preview) ---\n")
    preview = (
        html.replace("<b>", "")
        .replace("</b>", "")
        .replace("<i>", "")
        .replace("</i>", "")
        .replace("&lt;", "<")
    )
    print(preview[:2200])
    if len(preview) > 2200:
        print("  …")


def main(argv: list[str]) -> int:
    symbols = argv[1:] if len(argv) > 1 else ["ETHUSDT", "SOLUSDT"]
    for sym in symbols:
        try:
            run_symbol(sym, side="long")
        except Exception as exc:
            print(f"FAIL {sym}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
