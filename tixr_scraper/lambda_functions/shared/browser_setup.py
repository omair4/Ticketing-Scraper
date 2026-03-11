from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from playwright.async_api import Browser, Page, async_playwright

from .config import RATE_LIMIT_DELAY_MS, TIXR_BASE_URL


class CaptchaError(RuntimeError):
    """Raised when DataDome presents a hard captcha challenge."""


async def _sleep_ms(ms: int) -> None:
    await asyncio.sleep(ms / 1000.0)


async def create_tixr_session() -> tuple[Browser, Page]:
    """Launch Playwright, navigate to tixr.com, return (browser, page) with DataDome cookie set."""

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()
    # Initial navigation to establish DataDome cookie
    await page.goto(TIXR_BASE_URL, wait_until="domcontentloaded")
    return browser, page


async def fetch_in_page(page: Page, url: str) -> Any:
    """Execute fetch() inside page context, return parsed JSON.

    If the response looks like an HTML/DataDome challenge, raise CaptchaError.
    """

    js = """
    async (u) => {
      const res = await fetch(u, { headers: { 'Accept': '*/*' } });
      const ct = res.headers.get('content-type') || '';
      const text = await res.text();
      if (ct.includes('application/json')) {
        try {
          return { ok: true, json: JSON.parse(text) };
        } catch (e) {
          return { ok: false, status: res.status, contentType: ct, text };
        }
      }
      return { ok: false, status: res.status, contentType: ct, text };
    }
    """
    result = await page.evaluate(js, url)
    await _sleep_ms(RATE_LIMIT_DELAY_MS)

    if not result.get("ok"):
        text: str = result.get("text") or ""
        # Heuristic: DataDome / captcha pages are HTML with specific markers
        if "captcha-delivery.com" in text or "datadome" in text.lower():
            raise CaptchaError(f"Captcha challenge when fetching {url}")
        raise RuntimeError(
            f"Non-JSON response from {url}: status={result.get('status')} "
            f"contentType={result.get('contentType')}"
        )

    return result["json"]


async def with_tixr_session(
    fn: Callable[[Browser, Page], Awaitable[Any]],
) -> Any:
    """Helper to run a coroutine with a managed Tixr browser session."""

    browser: Browser | None = None
    try:
        browser, page = await create_tixr_session()
        return await fn(browser, page)
    finally:
        if browser is not None:
            await browser.close()

