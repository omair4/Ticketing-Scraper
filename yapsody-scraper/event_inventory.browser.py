"""
Playwright browser setup helper for Yapsody scraper.

Provides async_browser() context manager that yields a configured
Playwright Browser instance. Lambda paths and anti-bot flags match
the prekindle_kevins_structure pattern.
"""
import sys
from contextlib import asynccontextmanager
from typing import Optional

from playwright.async_api import async_playwright, Browser


def _get_proxy_kwargs(proxy_host, proxy_port, proxy_username, proxy_password) -> Optional[dict]:
    """Build Playwright proxy dict or None if no proxy configured."""
    if not proxy_host:
        return None
    server = f"http://{proxy_host}:{proxy_port}"
    kwargs = {"server": server}
    if proxy_username:
        kwargs["username"] = proxy_username
        kwargs["password"] = proxy_password
    return kwargs


def _launch_args() -> list[str]:
    """Chrome flags for Lambda / headless environment."""
    return [
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=site-per-process",
        "--single-process",
        "--no-zygote",
        "--disable-dev-tools",
    ]


@asynccontextmanager
async def async_browser(proxy_host=None, proxy_port=None,
                        proxy_username=None, proxy_password=None):
    """Async context manager that yields a Playwright Browser.

    Usage:
        async with async_browser(*get_proxy_info()) as browser:
            page = await browser.new_page()
            ...
    """
    proxy = _get_proxy_kwargs(proxy_host, proxy_port, proxy_username, proxy_password)

    # On Lambda, Chromium is at /opt/chrome/chrome-linux64/chrome.
    # Locally, Playwright uses its own bundled Chromium (executable_path=None).
    is_lambda = sys.platform == "linux" and "/opt/chrome" in _find_lambda_chrome()
    executable_path = "/opt/chrome/chrome-linux64/chrome" if is_lambda else None

    async with async_playwright() as pw:
        launch_kwargs = dict(
            headless=True,
            args=_launch_args(),
        )
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser: Browser = await pw.chromium.launch(**launch_kwargs)
        try:
            yield browser
        finally:
            await browser.close()


def _find_lambda_chrome() -> str:
    """Check if Lambda Chrome binary exists."""
    import os
    path = "/opt/chrome/chrome-linux64/chrome"
    return path if os.path.exists(path) else ""
