"""Quick live-market probe against scanner thresholds (run: python -m bot.market_probe)."""
from __future__ import annotations

import asyncio

import aiohttp


async def probe_symbol(session: aiohttp.ClientSession, symbol: str, period_min: int = 10) -> dict | None:
    params = {"category": "linear", "symbol": symbol, "interval": "5", "limit": 30}
    async with session.get("https://api.bybit.com/v5/market/kline", params=params) as response:
        kdata = await response.json()
    klines = list(reversed(kdata.get("result", {}).get("list", [])))
    bars = max(period_min // 5, 1)
    if len(klines) < bars + 1:
        return None

    now_close = float(klines[-1][4])
    ago_close = float(klines[-1 - bars][4])
    if ago_close <= 0:
        return None
    price_chg = (now_close - ago_close) / ago_close * 100.0

    oi_params = {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": "5min",
        "limit": 30,
    }
    async with session.get(
        "https://api.bybit.com/v5/market/open-interest",
        params=oi_params,
    ) as response:
        oidata = await response.json()
    oilist = list(reversed(oidata.get("result", {}).get("list", [])))
    if len(oilist) < bars + 1:
        return None

    oi_now = float(oilist[-1].get("openInterest", 0))
    oi_ago = float(oilist[-1 - bars].get("openInterest", 0))
    if oi_ago <= 0:
        return None
    oi_val_now = oi_now * now_close
    oi_val_ago = oi_ago * ago_close
    oi_chg = (oi_val_now - oi_val_ago) / oi_val_ago * 100.0
    oi_usd_flow = oi_val_now - oi_val_ago
    return {
        "symbol": symbol,
        "price_chg": price_chg,
        "oi_chg": oi_chg,
        "oi_usd": oi_usd_flow,
        "oi_val": oi_val_now,
    }


async def main() -> None:
    profiles = [
        ("Текущие дефолты", 2.0, 1.2, 20_000.0),
        ("Мягкие", 1.0, 0.8, 10_000.0),
        ("Очень мягкие", 0.5, 0.5, 5_000.0),
    ]
    for label, oi_thr, price_thr, min_flow in profiles:
        await _run_profile(label, oi_thr, price_thr, min_flow)


async def _run_profile(
    label: str,
    oi_thr: float,
    price_thr: float,
    min_flow: float,
) -> None:
    min_oi = 100_000.0
    max_flow: float | None = None

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
        ) as response:
            tickers = (await response.json())["result"]["list"]

    tickers.sort(key=lambda item: float(item.get("openInterestValue") or 0), reverse=True)
    symbols = [item["symbol"] for item in tickers[:50]]

    results: list[dict] = []
    async with aiohttp.ClientSession() as session:
        for symbol in symbols:
            try:
                row = await probe_symbol(session, symbol)
                if row:
                    results.append(row)
            except Exception:
                pass
            await asyncio.sleep(0.05)

    passed: list[dict] = []
    near: list[dict] = []
    for row in results:
        flow_ok = abs(row["oi_usd"]) >= min_flow
        if max_flow is not None and abs(row["oi_usd"]) > max_flow:
            flow_ok = False
        both = row["oi_chg"] >= oi_thr and row["price_chg"] >= price_thr
        liq_ok = row["oi_val"] >= min_oi
        if both and liq_ok and flow_ok:
            passed.append(row)
        elif (row["oi_chg"] >= oi_thr or row["price_chg"] >= price_thr) and liq_ok:
            near.append(row)

    print(f"\n=== {label} (Bybit top-50, 10 мин) ===")
    print(f"Пороги: OI>={oi_thr}% + цена>={price_thr}% | OI>={min_oi/1000:.0f}k | приток>={min_flow/1000:.0f}k")
    print(f"С данными: {len(results)} | Прошли бы ВСЕ фильтры: {len(passed)}")
    for row in passed[:12]:
        print(
            f"  {row['symbol']}: OI {row['oi_chg']:+.2f}% | "
            f"цена {row['price_chg']:+.2f}% | приток {row['oi_usd']/1000:.0f}k"
        )
    print(f"Почти (OI или цена, но не оба): {len(near)}")
    for row in sorted(near, key=lambda r: -(abs(r["oi_chg"]) + abs(r["price_chg"])))[:8]:
        miss = []
        if row["oi_chg"] < oi_thr:
            miss.append(f"OI {row['oi_chg']:+.1f}%<{oi_thr}%")
        if row["price_chg"] < price_thr:
            miss.append(f"цена {row['price_chg']:+.1f}%<{price_thr}%")
        if abs(row["oi_usd"]) < min_flow:
            miss.append(f"приток {abs(row['oi_usd'])/1000:.0f}k<{min_flow/1000:.0f}k")
        print(f"  {row['symbol']}: {' | '.join(miss)}")


if __name__ == "__main__":
    asyncio.run(main())
