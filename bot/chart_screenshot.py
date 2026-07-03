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
            page = await self._browser.new_page(viewport={"width": 1400, "height": 800})
            await page.goto(chart_url, wait_until="domcontentloaded", timeout=CAPTURE_TIMEOUT_MS)
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

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None


chart_capture_service = ChartScreenshotService()
