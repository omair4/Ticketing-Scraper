"""
Browser automation using Playwright for event discovery
"""

import asyncio
from typing import Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse

import structlog

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Response

from config import ScraperConfig, DEFAULT_CONFIG

logger = structlog.get_logger()


def _parse_proxy(proxy_url: Optional[str]) -> Optional[dict]:
    """Parse proxy URL into Playwright proxy dict."""
    if not proxy_url or not proxy_url.strip():
        return None
    try:
        parsed = urlparse(proxy_url.strip())
        if not parsed.hostname:
            return None
        server = f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or 80}"
        proxy_dict: dict = {"server": server}
        if parsed.username:
            proxy_dict["username"] = parsed.username
        if parsed.password:
            proxy_dict["password"] = parsed.password
        return proxy_dict
    except Exception as e:
        logger.warning("proxy_parse_failed", proxy_url=proxy_url[:50] if proxy_url else "", error=str(e))
        return None


@dataclass
class CapturedResponse:
    """Captured network response"""

    url: str
    status: int
    content_type: str
    body: Optional[str] = None

    def is_json(self) -> bool:
        return "application/json" in self.content_type or "text/json" in self.content_type


@dataclass
class NavigationResult:
    """Result of browser navigation"""

    success: bool
    current_url: str
    captured_responses: list[CapturedResponse] = field(default_factory=list)
    page_html: str = ""
    error: Optional[str] = None


class BrowserAutomation:
    """
    Simplified browser automation for event discovery.
    Navigates to pages and captures GraphQL API responses.
    """

    def __init__(self, config: ScraperConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.captured_responses: list[CapturedResponse] = []

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """Initialize browser"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context_options: dict = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": self.config.user_agent,
        }
        proxy_dict = _parse_proxy(self.config.proxy)
        if proxy_dict:
            context_options["proxy"] = proxy_dict
            logger.info("browser_using_proxy", server=proxy_dict.get("server"))
        self.context = await self.browser.new_context(**context_options)
        self.page = await self.context.new_page()

        # Set up response capture
        self.page.on("response", self._on_response)

        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)

        logger.info("browser_started", headless=self.config.headless)

    async def close(self):
        """Clean up browser resources"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if hasattr(self, "playwright"):
            await self.playwright.stop()
        logger.info("browser_closed")

    async def _on_response(self, response: Response):
        """Capture relevant API responses (GraphQL, etc.)"""
        url = response.url

        if "graphql.admitone.com" in url:
            try:
                content_type = response.headers.get("content-type", "")
                body = None

                if response.ok and ("json" in content_type or "text" in content_type or "application/" in content_type):
                    try:
                        body = await response.text()
                    except Exception:
                        try:
                            body_bytes = await response.body()
                            body = body_bytes.decode("utf-8", errors="replace")
                        except Exception:
                            pass

                captured = CapturedResponse(
                    url=url,
                    status=response.status,
                    content_type=content_type,
                    body=body,
                )
                self.captured_responses.append(captured)
                logger.debug("captured_graphql", url=url[:80], status=response.status, has_body=body is not None)
            except Exception as e:
                logger.warning("response_capture_failed", url=url, error=str(e))

    async def navigate_to_page(self, url: str) -> NavigationResult:
        """
        Navigate to a URL and capture responses.

        Returns:
            NavigationResult with captured API responses
        """
        self.captured_responses = []

        try:
            logger.info("navigating", url=url)

            full_url = url if url.startswith("http") else f"{self.config.base_url}{url}"
            await self.page.goto(
                full_url,
                wait_until="domcontentloaded",
                timeout=self.config.browser_timeout,
            )

            await self.page.wait_for_load_state("load", timeout=10000)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                logger.debug("networkidle_timeout", msg="Continuing anyway")

            await asyncio.sleep(self.config.page_load_delay / 1000)

            html = await self.page.content()

            return NavigationResult(
                success=True,
                current_url=self.page.url,
                captured_responses=self.captured_responses.copy(),
                page_html=html,
            )

        except Exception as e:
            logger.error("navigation_failed", error=str(e))
            return NavigationResult(
                success=False,
                current_url=self.page.url if self.page else url,
                captured_responses=self.captured_responses.copy(),
                error=str(e),
            )

    def get_responses_by_pattern(self, pattern: str) -> list[CapturedResponse]:
        """Get all captured responses matching a URL pattern"""
        return [r for r in self.captured_responses if pattern in r.url]
