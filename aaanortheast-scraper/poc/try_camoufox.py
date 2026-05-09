"""Method 3: Camoufox (patched Firefox with BrowserForge fingerprints)."""
from __future__ import annotations

import asyncio
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
    result = MethodResult(method="camoufox")
    try:
        from camoufox.async_api import AsyncCamoufox

        proxy = playwright_proxy()
        cf_kwargs: dict = {"headless": True, "humanize": True}
        if proxy:
            cf_kwargs["proxy"] = proxy
            # geoip=True asks Camoufox to align timezone/locale/lat-lon with
            # the proxy's exit IP — important for fingerprint consistency.
            cf_kwargs["geoip"] = True

        with Timer() as t:
            async with AsyncCamoufox(**cf_kwargs) as browser:
                page = await browser.new_page()
                resp = await page.goto(
                    URL, wait_until="domcontentloaded", timeout=60_000
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                html = await page.content()
                status = resp.status if resp else None
        result.elapsed_s = round(t.elapsed, 3)
        result.status = status
        result.html_path = str(save_html("camoufox", html))
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
