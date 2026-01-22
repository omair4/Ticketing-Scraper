"""
Browser automation using Playwright for navigating AdmitONE event pages
"""

import asyncio
from typing import Optional, Callable
from dataclasses import dataclass, field
import structlog

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Response
from config import ScraperConfig, BUTTON_SELECTORS, DEFAULT_CONFIG

logger = structlog.get_logger()


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


class BrowserAutomation:
    """
    Handles browser automation for navigating ticket purchase flows.
    
    Supports both 1-step and 2-step navigation:
    - 1-step: Event page → Buy Now → Seating plan
    - 2-step: Event page → Buy Now → Modal with Select Seats → Seating plan
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
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=self.config.user_agent
        )
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
        """Capture relevant API responses"""
        url = response.url
        
        # Filter for relevant APIs
        relevant_patterns = [
            'seatsio.net',
            'embed.admitonelive.com',
            'tickets.admitone.com',
            '/api/',
            'object-statuses',
            'seating-plan',
            '/events/',
            'charts',
            'published'
        ]
        
        if any(pattern in url for pattern in relevant_patterns):
            try:
                content_type = response.headers.get('content-type', '')
                body = None
                
                # Check if this is a critical API endpoint that we must capture
                is_critical_api = any(critical in url for critical in [
                    '/api/events/seated/',
                    'object-statuses',
                    'seating-plan/availability',
                    'charts/published'
                ])
                
                # Try to get response body
                if response.ok:
                    # For critical APIs, try to capture regardless of content-type
                    # For others, only capture if it's JSON or text
                    if is_critical_api or ('json' in content_type or 'text' in content_type or 'application/' in content_type):
                        try:
                            body = await response.text()
                        except:
                            # If text() fails, try body() for binary data
                            try:
                                body_bytes = await response.body()
                                # Try to decode as text, fallback to base64 if needed
                                try:
                                    body = body_bytes.decode('utf-8')
                                except:
                                    import base64
                                    body = base64.b64encode(body_bytes).decode('utf-8')
                            except:
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
    
    async def navigate_to_seats(self, event_url: str) -> NavigationResult:
        """
        Navigate through the ticket purchase flow to reach the seating selection.
        
        This handles both scenarios:
        1. Direct: Buy Now → Seating plan loads
        2. Two-step: Buy Now → Modal → Select Seats → Seating plan loads
        
        Returns:
            NavigationResult with captured API responses
        """
        self.captured_responses = []  # Reset captures
        
        try:
            logger.info("navigating_to_event", url=event_url)
            
            # Step 1: Navigate to event page
            await self.page.goto(event_url, wait_until='domcontentloaded', 
                               timeout=self.config.browser_timeout)
            
            # Wait for page to load - use 'load' instead of 'networkidle' as fallback
            try:
                await self.page.wait_for_load_state('load', timeout=10000)
            except:
                logger.debug("load_state_timeout", msg="Continuing anyway")
            
            # Try networkidle but don't fail if it times out
            try:
                await self.page.wait_for_load_state('networkidle', timeout=10000)
            except:
                logger.debug("networkidle_timeout", msg="Continuing anyway - page may have ongoing requests")
            
            await asyncio.sleep(self.config.page_load_delay / 1000)
            
            # Step 2: Click first "Buy Now" or "Get Tickets" button
            logger.info("attempting_to_click_buy_button")
            clicked = await self._click_buy_button()
            if not clicked:
                return NavigationResult(
                    success=False,
                    current_url=self.page.url,
                    error="Could not find Buy Now button"
                )
            
            # Wait for modal/iframe to appear or redirect
            logger.info("waiting_for_modal_or_redirect")
            await asyncio.sleep(self.config.click_delay / 1000)  # Wait after clicking Buy Now
            
            # Check if a modal/iframe appeared (Scenario 1: seats.io)
            modal_iframe = await self._wait_for_modal_iframe(timeout=10000)
            
            if modal_iframe:
                logger.info("modal_iframe_detected", scenario="seatsio_modal_flow")
                
                # Wait for iframe content to fully load and render
                await asyncio.sleep(4)  # Give iframe time to load embed.admitonelive.com content
                try:
                    await modal_iframe.wait_for_load_state('networkidle', timeout=15000)
                except:
                    logger.debug("iframe_networkidle_timeout", msg="Continuing anyway")
                
                # Try to click Select Seats button inside iframe
                select_seats_clicked = await self._try_click_select_seats_in_iframe(modal_iframe)
                
                if select_seats_clicked:
                    logger.info("select_seats_clicked_in_iframe")
                    # Wait for seats.io map to load - this can take time
                    await asyncio.sleep(6)  # Give seats.io time to load and make API calls
                    try:
                        # Wait for seats.io API calls to complete
                        await self.page.wait_for_load_state('networkidle', timeout=25000)
                    except:
                        logger.debug("networkidle_timeout_after_select_seats", msg="Continuing anyway")
                else:
                    # Try clicking Select Seats in main page (fallback)
                    select_seats_clicked = await self._try_click_select_seats()
                    if select_seats_clicked:
                        logger.info("select_seats_clicked_main_page")
                        await asyncio.sleep(6)
                        try:
                            await self.page.wait_for_load_state('networkidle', timeout=25000)
                        except:
                            logger.debug("networkidle_timeout_after_select_seats_main", msg="Continuing anyway")
            else:
                # No modal - check if we redirected to tickets.admitone.com (Scenario 2)
                current_url = self.page.url
                if 'tickets.admitone.com' in current_url:
                    logger.info("detected_tickets_admitone", url=current_url)
                    # Wait for page to fully load
                    await self.page.wait_for_load_state('networkidle', timeout=self.config.navigation_timeout)
                    await asyncio.sleep(2)
                    
                    # Try clicking Buy Now again on this page
                    await self._click_buy_button()
                    await asyncio.sleep(3)
                    await self.page.wait_for_load_state('networkidle', timeout=self.config.navigation_timeout)
                else:
                    # Try clicking Select Seats in case it's already visible
                    select_seats_clicked = await self._try_click_select_seats()
                    if select_seats_clicked:
                        logger.info("select_seats_clicked_after_buy")
                        await asyncio.sleep(5)
                        await self.page.wait_for_load_state('networkidle', timeout=20000)
            
            # Give additional time for all API calls to complete
            await asyncio.sleep(3)
            
            # Detect which scenario we're in based on captured responses
            scenario = self._detect_scenario()
            
            html = await self.page.content()
            
            return NavigationResult(
                success=True,
                current_url=self.page.url,
                captured_responses=self.captured_responses.copy(),
                page_html=html,
                scenario_detected=scenario
            )
            
        except Exception as e:
            logger.error("navigation_failed", error=str(e))
            return NavigationResult(
                success=False,
                current_url=self.page.url if self.page else event_url,
                captured_responses=self.captured_responses.copy(),
                error=str(e)
            )
    
    async def _click_buy_button(self) -> bool:
        """
        Try to click a Buy Now or Get Tickets button.
        
        Returns:
            True if a button was clicked
        """
        # First try role-based selector (most reliable)
        try:
            buy_button = self.page.get_by_role('button', name='Buy Now')
            if await buy_button.is_visible(timeout=5000):
                await buy_button.click()
                logger.info("clicked_buy_button", method="get_by_role", text="Buy Now")
                return True
        except Exception as e:
            logger.debug("get_by_role_failed", error=str(e))
        
        # Try all configured selectors
        for selector in BUTTON_SELECTORS['buy_now']:
            try:
                button = self.page.locator(selector).first
                if await button.is_visible(timeout=5000):
                    text = await button.text_content()
                    await button.click()
                    logger.info("clicked_button", selector=selector, text=text)
                    return True
            except Exception as e:
                logger.debug("selector_failed", selector=selector, error=str(e))
                continue
        
        # Try more generic approaches - look for buttons/links with buy-related text
        try:
            # Try get_by_role with partial text
            for text_variant in ['Buy Now', 'Get Tickets', 'Purchase']:
                try:
                    buy_element = self.page.get_by_role('button', name=text_variant)
                    if await buy_element.is_visible(timeout=3000):
                        await buy_element.click()
                        logger.info("clicked_buy_by_role", text=text_variant)
                        return True
                except:
                    continue
        except Exception as e:
            logger.debug("role_based_generic_failed", error=str(e))
        
        # Last resort: try to find by partial text match
        try:
            all_buttons = self.page.locator('button, a')
            count = await all_buttons.count()
            logger.debug("checking_buttons", count=count)
            for i in range(min(count, 20)):  # Check first 20 buttons
                button = all_buttons.nth(i)
                try:
                    text = await button.text_content()
                    if text and any(keyword in text.lower() for keyword in ['buy now', 'get tickets', 'purchase', 'buy']):
                        if await button.is_visible(timeout=2000):
                            await button.click()
                            logger.info("clicked_buy_by_text", text=text)
                            return True
                except:
                    continue
        except Exception as e:
            logger.debug("text_search_failed", error=str(e))
        
        logger.warning("buy_button_not_found")
        return False
    
    async def _wait_for_modal_iframe(self, timeout: int = 10000) -> Optional[Page]:
        """
        Wait for modal iframe to appear and return its page object.
        
        Returns:
            Page object of the iframe, or None if not found
        """
        try:
            # Look for iframe with embed.admitonelive.com (most common)
            # Also check for iframe with title containing "Buy tickets"
            iframe_selectors = [
                'iframe[src*="embed.admitonelive.com"]',
                'iframe[src*="admitonelive"]',
                'iframe[title*="Buy tickets"]',
                'iframe[title*="tickets"]'
            ]
            
            iframe_element = None
            for selector in iframe_selectors:
                try:
                    iframe_element = await self.page.wait_for_selector(
                        selector, 
                        timeout=timeout // len(iframe_selectors),
                        state='attached'
                    )
                    if iframe_element:
                        break
                except:
                    continue
            
            if iframe_element:
                # Get the iframe's content frame
                iframe = await iframe_element.content_frame()
                if iframe:
                    logger.info("modal_iframe_found", src=await iframe_element.get_attribute('src'))
                    # Wait for iframe to load DOM and then network
                    try:
                        await iframe.wait_for_load_state('domcontentloaded', timeout=10000)
                        await asyncio.sleep(1)  # Small delay for content to render
                    except:
                        logger.debug("iframe_dom_load_timeout", msg="Continuing anyway")
                    return iframe
        except Exception as e:
            logger.debug("no_modal_iframe_found", error=str(e))
        
        return None
    
    async def _try_click_select_seats_in_iframe(self, iframe: Page) -> bool:
        """
        Try to click Select Seats button inside the iframe.
        
        Args:
            iframe: The iframe Page object
            
        Returns:
            True if button was found and clicked
        """
        try:
            # Wait a bit for iframe content to fully render
            await asyncio.sleep(3)
            
            # Try role-based selector first (most reliable)
            try:
                button = iframe.get_by_role('button', name='Select Seats')
                if await button.is_visible(timeout=4000):
                    await button.click()
                    logger.info("clicked_select_seats_in_iframe", method="get_by_role")
                    return True
            except:
                pass
            
            # Try various CSS selectors for Select Seats button
            selectors = [
                'button:has-text("Select Seats")',  # Most common based on testing
                'button[aria-label="Select Seats"]',
                'button:has-text("Choose Seats")',
                'a:has-text("Select Seats")',
                'a:has-text("Choose Seats")',
                '[class*="select"]:has-text("Select")',
                '[class*="seat"]:has-text("Seat")',
                'button[data-testid*="select"]',
                'button[aria-label*="Select"]',
            ]
            
            for selector in selectors:
                try:
                    button = iframe.locator(selector).first
                    if await button.is_visible(timeout=4000):
                        await button.click()
                        logger.info("clicked_select_seats_in_iframe", selector=selector)
                        return True
                except Exception as e:
                    logger.debug("selector_failed", selector=selector, error=str(e))
                    continue
            
            # Try more generic approach - look for any button with "seat" or "select" in text
            try:
                # Try get_by_role with case-insensitive partial match
                all_buttons = iframe.locator('button')
                count = await all_buttons.count()
                for i in range(count):
                    button = all_buttons.nth(i)
                    text = await button.text_content()
                    if text and ('select' in text.lower() or 'seat' in text.lower()):
                        if await button.is_visible(timeout=2000):
                            await button.click()
                            logger.info("clicked_select_seats_generic", text=text)
                            return True
            except:
                pass
                
        except Exception as e:
            logger.warning("failed_to_click_in_iframe", error=str(e))
        
        return False
    
    async def _try_click_select_seats(self) -> bool:
        """
        Try to click a Select Seats button (for modal flow).
        
        Returns:
            True if button was found and clicked
        """
        for selector in BUTTON_SELECTORS['select_seats']:
            try:
                button = self.page.locator(selector).first
                if await button.is_visible(timeout=3000):
                    await button.click()
                    logger.info("clicked_select_seats", selector=selector)
                    return True
            except:
                continue
        
        # Try additional selectors
        additional_selectors = [
            'button:has-text("Choose Seats")',
            'a:has-text("Choose Seats")',
            '[class*="select-seats"]',
            '[data-testid*="select-seats"]',
        ]
        
        for selector in additional_selectors:
            try:
                button = self.page.locator(selector).first
                if await button.is_visible(timeout=2000):
                    await button.click()
                    logger.info("clicked_select_seats_additional", selector=selector)
                    return True
            except:
                continue
        
        return False
    
    def _detect_scenario(self) -> str:
        """
        Detect which API scenario we're dealing with based on captured responses.
        
        Returns:
            'seatsio' or 'direct_api'
        """
        for response in self.captured_responses:
            if 'seatsio.net' in response.url:
                return 'seatsio'
            if 'tickets.admitone.com' in response.url and 'seating-plan' in response.url:
                return 'direct_api'
        
        # Check for embed.admitonelive.com (also seatsio scenario)
        for response in self.captured_responses:
            if 'embed.admitonelive.com' in response.url:
                return 'seatsio'
        
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
