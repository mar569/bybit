from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import quote

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import Browser, async_playwright
except ImportError:  # pragma: no cover
    async_playwright = None  # type: ignore[misc, assignment]
    Browser = None  # type: ignore[misc, assignment]

CAPTURE_TIMEOUT_MS = 45_000
RENDER_WAIT_MS = 5_000


def tradingview_symbol(exchange: str, symbol: str) -> str:
    normalized = symbol.upper().replace("/", "")
    if "bybit" in exchange.lower():
        return f"BYBIT:{normalized}.P"
    if "binance" in exchange.lower():
        return f"BINANCE:{normalized}.P"
    return f"BYBIT:{normalized}.P"


def tradingview_widget_url(symbol: str, interval_minutes: int = 5) -> str:
    interval = str(interval_minutes)
    params = (
        f"symbol={quote(symbol, safe='')}"
        f"&interval={interval}"
        "&theme=dark"
        "&style=1"
        "&locale=ru"
        "&hide_side_toolbar=1"
        "&hide_top_toolbar=1"
        "&allow_symbol_change=0"
        "&save_image=0"
        "&withdateranges=0"
        "&details=0"
        "&calendar=0"
    )
    return f"https://s.tradingview.com/widgetembed/?{params}"


class ChartScreenshotService:
    """Скриншот реального TradingView-графика (тот же движок, что у Bybit)."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> bool:
        if async_playwright is None:
            logger.warning("Playwright not installed — real charts unavailable")
            return False
        if self._browser is not None:
            return True
        async with self._lock:
            if self._browser is not None:
                return True
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                    ],
                )
                logger.info("Playwright browser started for chart screenshots")
                return True
            except Exception:
                logger.exception("Failed to start Playwright browser")
                return False

    async def capture_tradingview(
        self,
        exchange: str,
        symbol: str,
        *,
        interval_minutes: int = 5,
    ) -> bytes | None:
        if not await self._ensure_browser():
            return None
        assert self._browser is not None

        tv_symbol = tradingview_symbol(exchange, symbol)
        url = tradingview_widget_url(tv_symbol, interval_minutes)

        page = None
        try:
            page = await self._browser.new_page(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=CAPTURE_TIMEOUT_MS)
            try:
                await page.wait_for_selector("canvas", timeout=20_000)
            except Exception:
                await page.wait_for_timeout(RENDER_WAIT_MS)
            else:
                await page.wait_for_timeout(2_000)

            png = await page.screenshot(type="png", full_page=False)
            logger.info("TradingView chart captured %s %s (%s)", exchange, symbol, tv_symbol)
            return png
        except Exception:
            logger.warning("TradingView chart capture failed for %s %s", exchange, symbol, exc_info=True)
            return None
        finally:
            if page is not None:
                await page.close()

    async def capture_coinglass(self, chart_url: str) -> bytes | None:
        if not chart_url or not await self._ensure_browser():
            return None
        assert self._browser is not None

        page = None
        try:
            page = await self._browser.new_page(
                viewport={"width": 1400, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            resp = await page.goto(chart_url, wait_until="domcontentloaded", timeout=CAPTURE_TIMEOUT_MS)
            status = resp.status if resp is not None else 0
            if status >= 400:
                logger.warning("CoinGlass HTTP %s for %s", status, chart_url)
                return None
            await page.wait_for_timeout(RENDER_WAIT_MS)
            try:
                chart = page.locator("canvas").first
                if await chart.count() > 0:
                    return await chart.screenshot(type="png")
            except Exception:
                pass
            return await page.screenshot(type="png", full_page=False)
        except Exception:
            logger.warning("CoinGlass chart capture failed: %s", chart_url, exc_info=True)
            return None
        finally:
            if page is not None:
                await page.close()

    async def capture_liquidation_heatmap(
        self,
        symbol: str,
        exchange: str = "bybit",
        *,
        range_label: str = "12hour",
    ) -> bytes | None:
        """Capture CoinGlass Liquidation Heatmap Model 1; skip error/empty pages."""
        from .liquidation_alerts import base_ticker, coinglass_liq_map_url

        coin = base_ticker(symbol)
        ex = "Bybit" if "bybit" in (exchange or "").lower() else (
            "Binance" if "binance" in (exchange or "").lower() else "Binance"
        )
        # Model 1 first (near-term); Model 3 as last fallback if page fails
        candidates = [
            coinglass_liq_map_url(symbol, exchange),
            (
                "https://www.coinglass.com/pro/futures/LiquidationHeatMap"
                f"?coin={coin}&type=pair&exchange={ex}&range={range_label}"
            ),
            f"https://www.coinglass.com/pro/futures/LiquidationHeatMap?coin={coin}",
            f"https://www.coinglass.com/pro/futures/LiquidationHeatMapModel3?coin={coin}&type=pair",
        ]
        if not await self._ensure_browser():
            return None
        assert self._browser is not None

        for url in candidates:
            page = None
            try:
                page = await self._browser.new_page(
                    viewport={"width": 1440, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                )
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=CAPTURE_TIMEOUT_MS)
                status = resp.status if resp is not None else 0
                if status >= 400:
                    logger.info("Liq heatmap HTTP %s skip %s", status, url)
                    continue
                # SPA needs time; wait for canvas or heatmap text
                try:
                    await page.wait_for_selector("canvas", timeout=25_000)
                except Exception:
                    await page.wait_for_timeout(RENDER_WAIT_MS + 3_000)
                else:
                    await page.wait_for_timeout(3_500)

                body_text = ""
                try:
                    body_text = (await page.inner_text("body")).lower()
                except Exception:
                    body_text = ""
                bad_markers = (
                    "404",
                    "not found",
                    "page not found",
                    "access denied",
                    "cloudflare",
                    "just a moment",
                    "captcha",
                )
                if any(m in body_text for m in bad_markers) and "liquidation" not in body_text:
                    logger.info("Liq heatmap looks blocked/empty: %s", url)
                    continue

                png: bytes | None = None
                try:
                    # Prefer largest canvas (main heatmap)
                    canvases = page.locator("canvas")
                    count = await canvases.count()
                    best = None
                    best_area = 0
                    for i in range(min(count, 8)):
                        box = await canvases.nth(i).bounding_box()
                        if not box:
                            continue
                        area = float(box.get("width", 0)) * float(box.get("height", 0))
                        if area > best_area:
                            best_area = area
                            best = canvases.nth(i)
                    if best is not None and best_area > 80_000:
                        png = await best.screenshot(type="png")
                except Exception:
                    png = None
                if png is None:
                    png = await page.screenshot(type="png", full_page=False)
                if png and len(png) > 20_000:
                    logger.info("Liq heatmap captured %s (%d bytes) via %s", coin, len(png), url)
                    return png
                logger.info("Liq heatmap too small/empty for %s", url)
            except Exception:
                logger.warning("Liq heatmap capture failed: %s", url, exc_info=True)
            finally:
                if page is not None:
                    await page.close()
        return None

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None


chart_capture_service = ChartScreenshotService()
