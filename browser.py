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
            '/events/'
        ]
        
        if any(pattern in url for pattern in relevant_patterns):
            try:
                content_type = response.headers.get('content-type', '')
                body = None
                
                # Try to get response body for JSON responses
                if response.ok and ('json' in content_type or 'text' in content_type):
                    try:
                        body = await response.text()
                    except:
                        pass
                
                captured = CapturedResponse(
                    url=url,
                    status=response.status,
                    content_type=content_type,
                    body=body
                )
                self.captured_responses.append(captured)
                logger.debug("captured_response", url=url[:100], status=response.status)
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
            await self.page.wait_for_load_state('networkidle', timeout=self.config.navigation_timeout)
            await asyncio.sleep(self.config.page_load_delay / 1000)
            
            # Step 2: Click first "Buy Now" or "Get Tickets" button
            clicked = await self._click_buy_button()
            if not clicked:
                return NavigationResult(
                    success=False,
                    current_url=self.page.url,
                    error="Could not find Buy Now button"
                )
            
            # Wait for response (modal or redirect)
            await asyncio.sleep(self.config.click_delay / 1000)
            await self.page.wait_for_load_state('networkidle', timeout=self.config.navigation_timeout)
            
            # Step 3: Check if we need a second click (Select Seats button)
            # This is for Scenario 1 where a modal appears
            select_seats_clicked = await self._try_click_select_seats()
            
            if select_seats_clicked:
                logger.info("select_seats_clicked", scenario="modal_flow")
                await asyncio.sleep(self.config.click_delay / 1000)
                await self.page.wait_for_load_state('networkidle', timeout=self.config.navigation_timeout)
            else:
                # Check if we're on tickets.admitone.com and need another Buy Now
                current_url = self.page.url
                if 'tickets.admitone.com' in current_url:
                    logger.info("detected_tickets_admitone", url=current_url)
                    # Try clicking Buy Now again on this page
                    await self._click_buy_button()
                    await asyncio.sleep(self.config.click_delay / 1000)
                    await self.page.wait_for_load_state('networkidle', timeout=self.config.navigation_timeout)
            
            # Give time for API calls to complete
            await asyncio.sleep(2)
            
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
        for selector in BUTTON_SELECTORS['buy_now']:
            try:
                button = self.page.locator(selector).first
                if await button.is_visible(timeout=2000):
                    await button.click()
                    logger.info("clicked_button", selector=selector)
                    return True
            except:
                continue
        
        # Try more generic approaches
        try:
            # Look for any button/link with buy-related text
            buy_element = self.page.locator('text=/buy|get tickets|purchase/i').first
            if await buy_element.is_visible(timeout=2000):
                await buy_element.click()
                logger.info("clicked_buy_generic")
                return True
        except:
            pass
        
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
