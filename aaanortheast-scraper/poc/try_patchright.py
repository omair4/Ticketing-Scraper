"""Method 2: Patchright (patched Playwright Chromium).

Headed by default — Cloudflare scores headless Chromium more aggressively, so
the POC starts with the configuration most likely to succeed. Set
`POC_HEADLESS=1` to retry headless once a headed run passes.
"""
from __future__ import annotations

import asyncio
import os
import sys

from poc.shared import (
    URL,
    MethodResult,
    Timer,
    emit,
    is_blocked,
    parse_events,
    playwright_proxy,
    save_html,
)


async def run() -> MethodResult:
    result = MethodResult(method="patchright")
    headless = os.environ.get("POC_HEADLESS", "0") == "1"
    try:
        from patchright.async_api import async_playwright

        proxy = playwright_proxy()
        launch_kwargs: dict = {"headless": headless}
        if proxy:
            launch_kwargs["proxy"] = proxy

        with Timer() as t:
            async with async_playwright() as p:
                browser = await p.chromium.launch(**launch_kwargs)
                ctx = await browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
                page = await ctx.new_page()
                resp = await page.goto(
                    URL, wait_until="domcontentloaded", timeout=60_000
                )
                # Allow any CF JS challenge to settle.
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                html = await page.content()
                status = resp.status if resp else None
                await ctx.close()
                await browser.close()
        result.elapsed_s = round(t.elapsed, 3)
        result.status = status
        result.html_path = str(save_html("patchright", html))
        blocked, reason = is_blocked(status, html)
        result.blocked = blocked
        result.block_reason = reason
        if not blocked:
            result.event_count = len(parse_events(html))
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    return result


def main() -> int:
    result = asyncio.run(run())
    emit(result)
    return 0 if (not result.error and not result.blocked) else 1


if __name__ == "__main__":
    sys.exit(main())
