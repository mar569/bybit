"""Локально: PNG ручного TA без Telegram. Пример: python -m bot.render_manual_chart_cli ETHUSDT 15 out.png"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from bot.chart_renderer import render_annotated_chart
from bot.manual_ta import manual_ta_hours


async def _run(symbol: str, interval: int, out: Path) -> int:
    hours = manual_ta_hours(interval)
    png, ta = await render_annotated_chart(
        symbol.upper(),
        side="long",
        hours=hours,
        interval_minutes=interval,
        neutral=True,
        manual_ta_chart=True,
    )
    if not png or ta is None:
        print(f"Нет данных для {symbol} {interval}m", file=sys.stderr)
        return 1
    out.write_bytes(png)
    from bot.human_trade_brief import format_manual_ta_human_html

    print(f"OK → {out} ({len(png)//1024} KB)")
    print(f"verdict={ta.verdict} conf={getattr(ta, 'verdict_confidence', 0)}")
    cap = format_manual_ta_human_html(ta, symbol=symbol)
    print("\n--- caption preview ---\n")
    print(cap.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")[:1200])
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: python -m bot.render_manual_chart_cli SYMBOL INTERVAL [out.png]", file=sys.stderr)
        return 2
    sym = argv[1]
    interval = int(argv[2])
    out = Path(argv[3]) if len(argv) > 3 else Path(f"{sym.upper()}_{interval}m_manual.png")
    return asyncio.run(_run(sym, interval, out))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
