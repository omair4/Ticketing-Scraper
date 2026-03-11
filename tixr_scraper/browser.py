"""
Browser automation using Playwright for navigating Tixr event pages.

Handles DataDome bot protection via anti-detection measures and
intercepts API responses via network listeners.
"""

import asyncio
import json
from typing import Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse
import structlog

from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Page, Browser, BrowserContext, Response
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
    error: Optional[str] = None
    scenario_detected: Optional[str] = None
    event_api_data: Optional[dict] = None


class BrowserAutomation:
    """
    Handles browser automation for Tixr events.

    Flow:
    1. Navigate to event URL
    2. Wait for DataDome challenge to clear
    3. Intercept /api/events/{id} response
    4. Detect scenario (general_admission vs seatsio)
    5. For seatsio: wait for seats.io CDN responses
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
        """Initialize Camoufox anti-detect browser."""
        proxy_dict = _parse_proxy(self.config.proxy)
        launch_options: dict = dict(
            headless=self.config.headless,
        )
        if proxy_dict:
            launch_options["proxy"] = proxy_dict
            logger.info("browser_using_proxy", server=proxy_dict.get("server"))

        self._camoufox = AsyncCamoufox(**launch_options)
        self.browser = await self._camoufox.__aenter__()
        await self._create_context()
        logger.info("browser_started", headless=self.config.headless)

    async def _create_context(self):
        """Create a new browser context with response capture."""
        context_options: dict = {
            "viewport": {"width": 1920, "height": 1080},
        }
        self.context = await self.browser.new_context(**context_options)
        self.page = await self.context.new_page()
        self.page.on('response', self._on_response)

    async def reset_context(self):
        """Close current context and create fresh one (resets DataDome session)."""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        self.captured_responses = []
        await self._create_context()
        logger.info("browser_context_reset")

    async def close(self):
        """Clean up browser resources."""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if hasattr(self, '_camoufox'):
            await self._camoufox.__aexit__(None, None, None)
        logger.info("browser_closed")

    async def _on_response(self, response: Response):
        """Capture relevant API responses (Tixr API + seats.io)"""
        url = response.url

        relevant_patterns = [
            '/api/events/',
            '/api/groups/',
            '/api/page/requirements',
            'seatsio.net',
            'object-statuses',
            'charts',
            'published',
            'rendering-info',
        ]

        if any(pattern in url for pattern in relevant_patterns):
            try:
                content_type = response.headers.get('content-type', '')
                body = None

                is_critical_api = any(critical in url for critical in [
                    '/api/events/',
                    '/api/groups/',
                    '/api/page/requirements',
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

    async def navigate_to_event(self, event_url: str) -> NavigationResult:
        """
        Navigate to a Tixr event page and capture API responses.

        1. Navigate to event URL
        2. Wait for DataDome challenge
        3. Intercept /api/events/{id} response
        4. Detect scenario
        5. For seatsio: wait for seats.io responses
        """
        self.captured_responses = []

        try:
            logger.info("navigating_to_event", url=event_url)

            # Step 1: Navigate to event page
            await self.page.goto(event_url, wait_until='domcontentloaded',
                                 timeout=self.config.browser_timeout)

            try:
                await self.page.wait_for_load_state('load', timeout=10000)
            except Exception:
                logger.debug("load_state_timeout", msg="Continuing anyway")

            # Step 2: Wait for DataDome challenge to resolve
            await asyncio.sleep(self.config.datadome_wait / 1000)

            try:
                await self.page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                logger.debug("networkidle_timeout", msg="Continuing anyway")

            # Step 3: Check for DataDome CAPTCHA
            if await self._is_datadome_blocked():
                logger.warning("datadome_captcha_detected",
                              msg="DataDome CAPTCHA detected. Waiting for manual resolution...")
                # Wait up to 60 seconds for manual CAPTCHA solving in headful mode
                for _ in range(60):
                    await asyncio.sleep(1)
                    if not await self._is_datadome_blocked():
                        logger.info("datadome_captcha_resolved")
                        break
                else:
                    return NavigationResult(
                        success=False,
                        current_url=self.page.url,
                        error="DataDome CAPTCHA not resolved within timeout"
                    )

            # Step 4: Extract event API data from captured responses
            event_api_data = self._extract_event_api_data()

            # Step 5: If seatsio, wait for seats.io responses
            has_seatsio = False
            if event_api_data:
                seatsio_config = event_api_data.get('seatsIoConfiguration')
                if seatsio_config:
                    has_seatsio = True
                    logger.info("seatsio_event_detected", config=str(seatsio_config)[:100])
                    # Wait for seats.io iframe and API calls
                    await self._wait_for_seatsio_responses()

            # Step 6: Capture page HTML
            html = await self.page.content()

            # Step 7: Detect scenario
            scenario = self._detect_scenario(event_api_data, html)

            logger.info("navigation_complete",
                        scenario=scenario,
                        responses_captured=len(self.captured_responses),
                        current_url=self.page.url)

            return NavigationResult(
                success=True,
                current_url=self.page.url,
                captured_responses=self.captured_responses.copy(),
                page_html=html,
                scenario_detected=scenario,
                event_api_data=event_api_data,
            )

        except Exception as e:
            logger.error("navigation_failed", error=str(e))
            return NavigationResult(
                success=False,
                current_url=self.page.url if self.page else event_url,
                captured_responses=self.captured_responses.copy(),
                error=str(e)
            )

    async def navigate_to_group(self, group_url: str) -> NavigationResult:
        """
        Navigate to a Tixr group page and capture group events API response.
        """
        self.captured_responses = []

        try:
            logger.info("navigating_to_group", url=group_url)

            await self.page.goto(group_url, wait_until='domcontentloaded',
                                 timeout=self.config.browser_timeout)

            try:
                await self.page.wait_for_load_state('load', timeout=10000)
            except Exception:
                pass

            # Wait for DataDome
            await asyncio.sleep(self.config.datadome_wait / 1000)

            try:
                await self.page.wait_for_load_state('networkidle', timeout=15000)
            except Exception:
                pass

            # Check DataDome
            if await self._is_datadome_blocked():
                logger.warning("datadome_captcha_on_group",
                              msg="Waiting for manual resolution...")
                for _ in range(60):
                    await asyncio.sleep(1)
                    if not await self._is_datadome_blocked():
                        break
                else:
                    return NavigationResult(
                        success=False,
                        current_url=self.page.url,
                        error="DataDome CAPTCHA not resolved"
                    )

            html = await self.page.content()

            return NavigationResult(
                success=True,
                current_url=self.page.url,
                captured_responses=self.captured_responses.copy(),
                page_html=html,
            )

        except Exception as e:
            logger.error("group_navigation_failed", error=str(e))
            return NavigationResult(
                success=False,
                current_url=self.page.url if self.page else group_url,
                captured_responses=self.captured_responses.copy(),
                error=str(e)
            )

    async def _is_datadome_blocked(self) -> bool:
        """Check if currently blocked by DataDome CAPTCHA."""
        try:
            # DataDome typically shows an interstitial page or iframe
            content = await self.page.content()
            datadome_indicators = [
                'geo.captcha-delivery.com',
                'datadome',
                'dd.tixr.com',
                'captcha-delivery',
            ]
            return any(indicator in content.lower() for indicator in datadome_indicators)
        except Exception:
            return False

    async def _wait_for_seatsio_responses(self, timeout: int = 15):
        """Wait for seats.io CDN responses to be captured."""
        logger.info("waiting_for_seatsio_responses")

        # Wait for seats.io iframe to appear
        try:
            iframe_element = await self.page.query_selector('iframe[src*="seatsio.net"]')
            if iframe_element:
                iframe = await iframe_element.content_frame()
                if iframe:
                    try:
                        await iframe.wait_for_load_state('networkidle', timeout=15000)
                    except Exception:
                        pass
        except Exception:
            pass

        # Wait for key seats.io responses
        for _ in range(timeout):
            has_rendering = any('rendering-info' in r.url for r in self.captured_responses)
            has_statuses = any('object-statuses' in r.url for r in self.captured_responses)

            if has_rendering and has_statuses:
                logger.info("seatsio_responses_captured")
                return

            await asyncio.sleep(1)

        logger.warning("seatsio_responses_timeout",
                       rendering=any('rendering-info' in r.url for r in self.captured_responses),
                       statuses=any('object-statuses' in r.url for r in self.captured_responses))

    def _extract_event_api_data(self) -> Optional[dict]:
        """Extract event data from captured /api/events/{id} response."""
        for response in self.captured_responses:
            if '/api/events/' in response.url and response.body:
                try:
                    data = json.loads(response.body)
                    if isinstance(data, dict) and 'id' in data:
                        logger.info("event_api_data_extracted",
                                    event_id=data.get('id'),
                                    event_name=data.get('name', '')[:50])
                        return data
                except (json.JSONDecodeError, ValueError):
                    continue
        return None

    def _detect_scenario(self, event_api_data: Optional[dict], page_html: str) -> str:
        """Detect scraping scenario from event API data."""
        if event_api_data:
            seatsio_config = event_api_data.get('seatsIoConfiguration')
            if seatsio_config:
                return 'seatsio'
            return 'general_admission'

        # Fallback: check captured responses and HTML
        has_seatsio = any('seatsio.net' in r.url for r in self.captured_responses)
        if has_seatsio or 'seatsio.net' in page_html:
            return 'seatsio'

        return 'general_admission'

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
