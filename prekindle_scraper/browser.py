"""
Browser automation using Playwright for navigating PreKindle event pages
"""

import asyncio
from typing import Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse
import structlog

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response
from config import ScraperConfig, BUTTON_SELECTORS, DEFAULT_CONFIG

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
        logger.warning("proxy_parse_failed", error=str(e))
        return None


@dataclass
class CapturedResponse:
    """Captured network response"""
    url: str
    status: int
    content_type: str
    body: Optional[str] = None

    def is_json(self) -> bool:
        return 'application/json' in self.content_type or 'text/json' in self.content_type


@dataclass
class NavigationResult:
    """Result of browser navigation"""
    success: bool
    current_url: str
    captured_responses: list[CapturedResponse] = field(default_factory=list)
    page_html: str = ""
    promo_page_html: str = ""
    error: Optional[str] = None
    scenario_detected: Optional[str] = None


class BrowserAutomation:
    """
    Handles browser automation for navigating PreKindle ticket purchase flows.

    Flow:
    1. Navigate to promo page (/promo/id/{eventId})
    2. Extract event info from promo page
    3. Click "GET TICKETS" → navigates to /find-tickets or /checkout
    4. Wait for page load (may pass through Queue-IT)
    5. Detect scenario from loaded page content
    6. Capture network responses (seats.io URLs)
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
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
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
        self.page.on('response', self._on_response)

        # Remove webdriver detection
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
        if hasattr(self, 'playwright'):
            await self.playwright.stop()
        logger.info("browser_closed")

    async def _on_response(self, response: Response):
        """Capture relevant API responses (seats.io and PreKindle APIs)"""
        url = response.url

        relevant_patterns = [
            'seatsio.net',
            'object-statuses',
            'charts',
            'published',
            '/api/',
        ]

        if any(pattern in url for pattern in relevant_patterns):
            try:
                content_type = response.headers.get('content-type', '')
                body = None

                is_critical_api = any(critical in url for critical in [
                    'object-statuses',
                    'charts/published',
                    'cdn-na.seatsio.net',
                    'cdn-eu.seatsio.net',
                ])

                if response.ok:
                    if is_critical_api or ('json' in content_type or 'text' in content_type or 'application/' in content_type):
                        try:
                            body = await response.text()
                        except Exception:
                            try:
                                body_bytes = await response.body()
                                try:
                                    body = body_bytes.decode('utf-8')
                                except Exception:
                                    import base64
                                    body = base64.b64encode(body_bytes).decode('utf-8')
                            except Exception:
                                pass

                captured = CapturedResponse(
                    url=url,
                    status=response.status,
                    content_type=content_type,
                    body=body
                )
                self.captured_responses.append(captured)
                logger.debug("captured_response", url=url[:100], status=response.status, has_body=body is not None)
            except Exception as e:
                logger.warning("response_capture_failed", url=url, error=str(e))

    async def navigate_to_tickets(self, event_url: str) -> NavigationResult:
        """
        Navigate through the PreKindle ticket purchase flow.

        1. Go to promo page
        2. Capture promo page HTML (has event info)
        3. Click GET TICKETS
        4. Wait for ticket/checkout page (may pass Queue-IT)
        5. Detect scenario
        6. Return captured data

        Args:
            event_url: PreKindle promo URL

        Returns:
            NavigationResult with captured responses and page HTML
        """
        self.captured_responses = []

        try:
            logger.info("navigating_to_event", url=event_url)

            # Step 1: Navigate to promo page
            await self.page.goto(event_url, wait_until='domcontentloaded',
                                 timeout=self.config.browser_timeout)

            try:
                await self.page.wait_for_load_state('load', timeout=10000)
            except Exception:
                logger.debug("load_state_timeout", msg="Continuing anyway")

            try:
                await self.page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                logger.debug("networkidle_timeout", msg="Continuing anyway")

            await asyncio.sleep(self.config.page_load_delay / 1000)

            # Step 2: Capture promo page HTML (contains event info)
            promo_page_html = await self.page.content()
            logger.info("captured_promo_page", url=self.page.url, html_length=len(promo_page_html))

            # Step 3: Click GET TICKETS button
            logger.info("attempting_to_click_get_tickets")
            clicked = await self._click_get_tickets()

            if not clicked:
                return NavigationResult(
                    success=False,
                    current_url=self.page.url,
                    promo_page_html=promo_page_html,
                    error="Could not find GET TICKETS button"
                )

            # Step 4: Wait for navigation + Queue-IT
            logger.info("waiting_for_ticket_page")
            await asyncio.sleep(self.config.click_delay / 1000)

            # Wait for Queue-IT to pass and ticket page to load
            # Queue-IT redirects: promo → q.prekindle.com → find-tickets/checkout
            await self._wait_for_ticket_page(timeout=60000)

            # Step 5: Additional wait for dynamic content (seats.io iframe, etc.)
            await asyncio.sleep(self.config.page_load_delay / 1000)

            # Check if seats.io iframe is present and wait for it
            has_seatsio = await self._wait_for_seatsio_iframe()
            if has_seatsio:
                # Give extra time for seats.io API calls
                await asyncio.sleep(5)

            # Step 6: Capture final page HTML
            html = await self.page.content()

            # Step 7: Detect scenario
            scenario = self._detect_scenario(html)

            logger.info("navigation_complete",
                         scenario=scenario,
                         responses_captured=len(self.captured_responses),
                         current_url=self.page.url)

            return NavigationResult(
                success=True,
                current_url=self.page.url,
                captured_responses=self.captured_responses.copy(),
                page_html=html,
                promo_page_html=promo_page_html,
                scenario_detected=scenario,
            )

        except Exception as e:
            logger.error("navigation_failed", error=str(e))
            return NavigationResult(
                success=False,
                current_url=self.page.url if self.page else event_url,
                captured_responses=self.captured_responses.copy(),
                error=str(e)
            )

    async def _click_get_tickets(self) -> bool:
        """Try to click GET TICKETS button on promo page."""
        for selector in BUTTON_SELECTORS['get_tickets']:
            try:
                button = self.page.locator(selector).first
                if await button.is_visible(timeout=5000):
                    text = await button.text_content()
                    await button.click()
                    logger.info("clicked_get_tickets", selector=selector, text=text)
                    return True
            except Exception as e:
                logger.debug("selector_failed", selector=selector, error=str(e))
                continue

        # Fallback: scan all links for "GET TICKETS" or "TICKETS" text
        try:
            all_links = self.page.locator('a')
            count = await all_links.count()
            for i in range(min(count, 30)):
                link = all_links.nth(i)
                try:
                    text = await link.text_content()
                    if text and ('get tickets' in text.lower() or text.strip().upper() == 'TICKETS'):
                        if await link.is_visible(timeout=2000):
                            await link.click()
                            logger.info("clicked_get_tickets_fallback", text=text)
                            return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("fallback_link_search_failed", error=str(e))

        logger.warning("get_tickets_button_not_found")
        return False

    async def _wait_for_ticket_page(self, timeout: int = 60000):
        """
        Wait for the ticket/checkout page to load after Queue-IT.
        Queue-IT may redirect through q.prekindle.com before landing on find-tickets or checkout.
        """
        try:
            # Wait until URL contains find-tickets or checkout (post Queue-IT)
            await self.page.wait_for_url(
                lambda url: 'find-tickets' in url or 'checkout' in url,
                timeout=timeout
            )
            logger.info("ticket_page_reached", url=self.page.url)
        except Exception:
            logger.debug("ticket_page_wait_timeout", url=self.page.url,
                          msg="May already be on ticket page or Queue-IT is slow")

        # Wait for page to finish loading
        try:
            await self.page.wait_for_load_state('domcontentloaded', timeout=15000)
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass

    async def _wait_for_seatsio_iframe(self) -> bool:
        """Check if a seats.io iframe is present and wait for it to load."""
        try:
            iframe_element = await self.page.query_selector('iframe[src*="seatsio.net"]')
            if iframe_element:
                logger.info("seatsio_iframe_detected")
                iframe = await iframe_element.content_frame()
                if iframe:
                    try:
                        await iframe.wait_for_load_state('networkidle', timeout=15000)
                    except Exception:
                        pass
                return True
        except Exception:
            pass
        return False

    def _detect_scenario(self, page_html: str) -> str:
        """
        Detect which scenario we're in based on page content and captured responses.

        Returns:
            'seatsio', 'general_admission', 'svg_seatmap', or 'unknown'
        """
        # Priority 1: seats.io — check for seatsio.net in captured responses or iframe in HTML
        has_seatsio_response = any('seatsio.net' in r.url for r in self.captured_responses)
        has_seatsio_iframe = 'seatsio.net' in page_html or 'seatsio' in page_html.lower()

        if has_seatsio_response or has_seatsio_iframe:
            return 'seatsio'

        # Priority 2: SVG seatmap — large base64 background image + SVG overlay with wicketpath
        has_wicketpath = 'wicketpath' in page_html
        has_svg_seatmap = '<svg' in page_html and has_wicketpath

        if has_svg_seatmap:
            return 'svg_seatmap'

        # Priority 3: General admission — HTML table with quantity dropdowns
        # Look for select elements (quantity dropdowns) or ticket table patterns
        has_select = '<select' in page_html
        has_ticket_table = 'Total Price' in page_html or 'howManyPanel' in page_html

        if has_select or has_ticket_table:
            return 'general_admission'

        return 'unknown'

    def get_responses_by_pattern(self, pattern: str) -> list[CapturedResponse]:
        """Get all captured responses matching a URL pattern"""
        return [r for r in self.captured_responses if pattern in r.url]

    async def get_page_content(self) -> str:
        """Get current page HTML"""
        return await self.page.content() if self.page else ""

    async def screenshot(self, path: str):
        """Take a screenshot for debugging"""
        if self.page:
            await self.page.screenshot(path=path)
            logger.info("screenshot_saved", path=path)
