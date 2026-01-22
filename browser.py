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
    error_context: Optional[str] = None  # Categorizes error type
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

    async def _wait_for_api_response(self, url_pattern: str, timeout: int = 25000) -> bool:
        """
        Wait for a specific API response to be captured.

        Polls self.captured_responses until pattern is found or timeout.

        Args:
            url_pattern: URL pattern to match
            timeout: Timeout in milliseconds

        Returns:
            True if response was captured, False on timeout
        """
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout / 1000

        while True:
            # Check if we have the response
            for response in self.captured_responses:
                if url_pattern in response.url:
                    logger.info("api_response_captured", pattern=url_pattern, url=response.url[:80])
                    return True

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                logger.warning("api_response_timeout", pattern=url_pattern, elapsed=elapsed)
                return False

            # Wait before next poll
            await asyncio.sleep(self.config.api_poll_interval / 1000)

    async def _wait_for_any_api_response(self, patterns: list[str], timeout: int = 25000) -> Optional[str]:
        """
        Wait for any of the given API response patterns.

        Returns the first matched pattern or None on timeout.

        Args:
            patterns: List of URL patterns to match
            timeout: Timeout in milliseconds

        Returns:
            Matched pattern or None
        """
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout / 1000

        while True:
            for pattern in patterns:
                for response in self.captured_responses:
                    if pattern in response.url:
                        logger.info("api_response_captured", pattern=pattern, url=response.url[:80])
                        return pattern

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                logger.warning("api_response_timeout", patterns=patterns, elapsed=elapsed)
                return None

            await asyncio.sleep(self.config.api_poll_interval / 1000)

    async def _wait_for_element(self, selector: str, timeout: int = 20000) -> bool:
        """
        Wait for element to be visible.

        Args:
            selector: CSS selector
            timeout: Timeout in milliseconds

        Returns:
            True if element found, False otherwise
        """
        try:
            await self.page.wait_for_selector(selector, state='visible', timeout=timeout)
            return True
        except:
            return False

    async def _check_for_modal(self, timeout: int = 15000) -> bool:
        """
        Check if a modal/popup appeared.

        Args:
            timeout: Timeout in milliseconds (default 15s to handle slow-rendering modals)

        Returns:
            True if modal found, False otherwise
        """
        # First, try to detect the modal container/overlay itself (appears quickly)
        modal_container_selectors = [
            '[role="dialog"]',
            '[class*="modal"]',
            '[class*="Modal"]',
            '[class*="popup"]',
            '[class*="dialog"]',
            'div[style*="z-index"]',  # Modals often have high z-index
        ]

        logger.info("checking_for_modal_container")
        for selector in modal_container_selectors:
            try:
                await self.page.wait_for_selector(selector, state='visible', timeout=3000)
                logger.info("modal_container_detected", selector=selector)
                # Modal container found, now give it more time for content to render
                await asyncio.sleep(2.0)
                break
            except:
                continue

        # Now try to find the actual button (with longer timeout since content renders slowly)
        logger.info("checking_for_select_seats_button", timeout=timeout)
        for selector in BUTTON_SELECTORS['select_seats']:
            try:
                await self.page.wait_for_selector(selector, state='visible', timeout=timeout)
                logger.info("select_seats_button_detected", selector=selector)
                return True
            except:
                continue

        # Add generic fallback using regex pattern
        try:
            # Match any element with seat selection text
            await self.page.wait_for_selector('text=/select.*seat|choose.*seat|pick.*seat/i',
                                              state='visible',
                                              timeout=timeout)
            logger.info("modal_detected_via_generic_pattern")
            return True
        except:
            pass

        logger.warning("modal_detection_failed_after_timeout", timeout=timeout)
        return False

    async def _wait_for_element_or_url_change(self, selectors: list[str], url_pattern: str, timeout: int = 20000) -> tuple[bool, str]:
        """
        Wait for either an element to appear or URL to change.

        Args:
            selectors: List of CSS selectors to check
            url_pattern: URL pattern to match
            timeout: Timeout in milliseconds

        Returns:
            (success, reason) where reason is 'url_changed', 'element_found', or 'timeout'
        """
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout / 1000

        while True:
            # Check URL
            if url_pattern in self.page.url:
                return True, 'url_changed'

            # Check selectors
            for selector in selectors:
                try:
                    element = self.page.locator(selector).first
                    if await element.is_visible(timeout=500):
                        return True, 'element_found'
                except:
                    continue

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                return False, 'timeout'

            await asyncio.sleep(0.5)
    
    async def navigate_to_seats(self, event_url: str) -> NavigationResult:
        """
        Navigate through the ticket purchase flow to reach the seating selection.

        Enhanced version with intelligent wait strategies instead of networkidle.

        This handles both scenarios:
        1. Direct: Buy Now → Seating plan loads
        2. Two-step: Buy Now → Modal → Select Seats → Seating plan loads

        Returns:
            NavigationResult with captured API responses
        """
        self.captured_responses = []  # Reset captures

        try:
            logger.info("navigating_to_event", url=event_url)

            # STEP 1: Navigate to event page
            # Wait for domcontentloaded only, not networkidle
            await self.page.goto(event_url,
                               wait_until='domcontentloaded',
                               timeout=self.config.dom_load_timeout)

            # Wait for Buy Now button to be visible (indicates page is ready)
            buy_button_visible = False
            for selector in BUTTON_SELECTORS['buy_now']:
                try:
                    await self.page.wait_for_selector(selector, state='visible',
                                                     timeout=3000)
                    buy_button_visible = True
                    break
                except:
                    continue

            if not buy_button_visible:
                # Fallback to fixed delay
                logger.warning("buy_button_not_visible_using_delay")
                await asyncio.sleep(self.config.page_load_delay / 1000)
            else:
                # Even if button is visible, give it time to become interactive
                logger.info("buy_button_visible_waiting_for_interactivity")
                await asyncio.sleep(1.0)  # 1 second for button to become clickable

            logger.info("event_page_loaded")

            # STEP 2: Click first "Buy Now" or "Get Tickets" button
            clicked = await self._click_buy_button()
            if not clicked:
                return NavigationResult(
                    success=False,
                    current_url=self.page.url,
                    error="Could not find or click Buy Now button",
                    error_context="buy_button_missing"
                )

            logger.info("buy_button_clicked")

            # STEP 3: Wait for response - either modal iframe or redirect
            # After clicking Buy Now, an iframe modal may load with the seating selection
            logger.info("waiting_for_modal_iframe_or_redirect")
            await asyncio.sleep(2.0)  # 2 seconds for initial response

            # First check if URL changed to tickets.admitone.com (Scenario 2)
            url_changed = 'tickets.admitone.com' in self.page.url
            logger.info("url_check_result",
                       current_url=self.page.url,
                       is_tickets_domain=url_changed)

            # If URL didn't change, look for iframe modal (Scenario 1)
            iframe_appeared = False
            if not url_changed:
                logger.info("checking_for_modal_iframe")
                # Wait for iframe that contains the seat selection
                # Common patterns: embed.admitonelive.com, seatsio iframe
                iframe_selectors = [
                    'iframe[src*="embed.admitonelive"]',
                    'iframe[src*="seatsio"]',
                    'iframe[src*="seats.io"]',
                ]

                for selector in iframe_selectors:
                    try:
                        await self.page.wait_for_selector(selector, state='attached',
                                                          timeout=15000)
                        iframe_appeared = True
                        logger.info("modal_iframe_detected", selector=selector)
                        # Give iframe time to load its content
                        await asyncio.sleep(3.0)
                        break
                    except:
                        continue

            logger.info("modal_iframe_result", iframe_appeared=iframe_appeared)

            # STEP 4: Handle modal iframe flow (Scenario 1)
            select_seats_clicked = False

            if iframe_appeared:
                # The Select Seats button is inside the iframe
                logger.info("looking_for_select_seats_in_iframe")
                select_seats_clicked = await self._try_click_select_seats_in_iframe()
                logger.info("select_seats_click_in_iframe_result", success=select_seats_clicked)
            else:
                # Fallback: try to find button in main page (old behavior)
                logger.info("about_to_attempt_select_seats_click")
                select_seats_clicked = await self._try_click_select_seats()
                logger.info("select_seats_click_result", success=select_seats_clicked)

            if select_seats_clicked:
                logger.info("select_seats_clicked", scenario="seatsio")
                await asyncio.sleep(self.config.click_delay / 1000)

                # Wait for seatsio API response or iframe
                api_patterns = ['seatsio.net', 'embed.admitonelive.com']
                matched = await self._wait_for_any_api_response(
                    api_patterns,
                    timeout=self.config.api_response_timeout
                )

                if not matched:
                    # Fallback: wait for seatsio iframe
                    logger.info("api_not_captured_waiting_for_iframe")
                    await self._wait_for_element(
                        'iframe[src*="seatsio"], iframe[src*="admitonelive"]',
                        timeout=10000
                    )

            # STEP 5: Handle redirect flow (Scenario 2)
            else:
                current_url = self.page.url
                if 'tickets.admitone.com' in current_url:
                    logger.info("detected_tickets_admitone", url=current_url)

                    # Click Buy Now again on this page
                    second_click = await self._click_buy_button()

                    if second_click:
                        await asyncio.sleep(self.config.click_delay / 1000)

                        # Wait for availability API response
                        api_captured = await self._wait_for_api_response(
                            'seating-plan/availability',
                            timeout=self.config.api_response_timeout
                        )

                        if not api_captured:
                            # Fallback: wait for seating plan container
                            logger.info("availability_api_not_captured_waiting_for_container")
                            await self._wait_for_element(
                                '[class*="seating"], [id*="seating"], .ticket-selection',
                                timeout=10000
                            )

            # Give a final moment for any late API calls
            await asyncio.sleep(2)

            # Detect scenario from captured responses
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
            error_msg = str(e)
            logger.error("navigation_failed", error=error_msg)

            # Enhanced error message with context
            error_context = None
            if 'Timeout' in error_msg:
                if 'wait_for_selector' in error_msg:
                    error_msg = f"Timeout waiting for element: {error_msg}"
                    error_context = "element_timeout"
                elif 'goto' in error_msg:
                    error_msg = f"Timeout loading initial page: {error_msg}"
                    error_context = "page_load_timeout"
                else:
                    error_msg = f"Navigation timeout: {error_msg}"
                    error_context = "api_timeout"

            return NavigationResult(
                success=False,
                current_url=self.page.url if self.page else event_url,
                captured_responses=self.captured_responses.copy(),
                error=error_msg,
                error_context=error_context
            )
    
    async def navigate_to_seats_with_retry(self, event_url: str) -> NavigationResult:
        """
        Navigate with automatic retry on timeout.

        Args:
            event_url: Full URL to the AdmitONE event page

        Returns:
            NavigationResult with captured API responses
        """
        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = await self.navigate_to_seats(event_url)

                if result.success:
                    return result

                # Check if error is retryable (timeout vs hard failure)
                if not self._is_retryable_error(result.error):
                    logger.info("error_not_retryable", error=result.error)
                    return result

                logger.warning("navigation_failed_retrying",
                              attempt=attempt,
                              max_retries=self.config.max_retries,
                              error=result.error,
                              retry_in=self.config.retry_delay)

                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_delay)
                    # Reset captured responses for clean retry
                    self.captured_responses = []

            except Exception as e:
                logger.error("navigation_exception", attempt=attempt, error=str(e))
                if attempt >= self.config.max_retries:
                    return NavigationResult(
                        success=False,
                        current_url=event_url,
                        error=f"Failed after {self.config.max_retries} attempts: {str(e)}",
                        error_context="max_retries_exceeded"
                    )
                await asyncio.sleep(self.config.retry_delay)

        return NavigationResult(
            success=False,
            current_url=event_url,
            error=f"Failed after {self.config.max_retries} attempts",
            error_context="max_retries_exceeded"
        )

    def _is_retryable_error(self, error: Optional[str]) -> bool:
        """
        Check if error is worth retrying.

        Args:
            error: Error message

        Returns:
            True if error is likely transient and worth retrying
        """
        if not error:
            return False

        retryable_patterns = [
            'timeout',
            'Timeout',
            'networkidle',
            'Navigation',
            'net::ERR',
            'timed out',
        ]

        return any(pattern in error for pattern in retryable_patterns)

    async def _click_buy_button(self) -> bool:
        """
        Try to click a Buy Now or Get Tickets button.

        Enhanced with better error handling and clickability checks.

        Returns:
            True if a button was clicked
        """
        logger.info("attempting_to_click_buy_now")

        for selector in BUTTON_SELECTORS['buy_now']:
            try:
                logger.debug("trying_buy_selector", selector=selector)

                # Wait for element to be clickable (not just visible)
                if await self._wait_for_clickable(selector, timeout=5000):
                    button = self.page.locator(selector).first
                    await button.click()
                    logger.info("clicked_buy_now_success", selector=selector)
                    return True

            except Exception as e:
                logger.warning("buy_selector_click_failed",
                              selector=selector,
                              error=str(e))
                continue

        logger.warning("all_configured_buy_selectors_failed_trying_fallback")

        # Try more generic approaches
        try:
            # Look for any button/link with buy-related text
            if await self._wait_for_clickable('text=/buy|get tickets|purchase/i', timeout=5000):
                buy_element = self.page.locator('text=/buy|get tickets|purchase/i').first
                await buy_element.click()
                logger.info("clicked_buy_generic_fallback_success")
                return True
        except Exception as e:
            logger.error("buy_generic_fallback_failed", error=str(e))

        logger.error("all_buy_button_attempts_failed")
        return False

    async def _wait_for_clickable(self, selector: str, timeout: int = 5000) -> bool:
        """
        Wait for element to be both visible AND clickable.

        Args:
            selector: CSS selector or text selector
            timeout: Timeout in milliseconds

        Returns:
            True if element is clickable, False otherwise
        """
        try:
            element = self.page.locator(selector).first

            # Wait for element to be visible
            await element.wait_for(state='visible', timeout=timeout)

            # Additional wait for any animations/transitions
            await asyncio.sleep(0.5)

            # Verify it's still visible and enabled
            is_visible = await element.is_visible()
            is_enabled = await element.is_enabled()

            if is_visible and is_enabled:
                logger.debug("element_is_clickable", selector=selector[:50])
                return True
            else:
                logger.debug("element_not_clickable",
                            selector=selector[:50],
                            visible=is_visible,
                            enabled=is_enabled)
                return False

        except Exception as e:
            logger.debug("wait_for_clickable_failed",
                        selector=selector[:50],
                        error=str(e))
            return False

    async def _try_click_select_seats(self) -> bool:
        """
        Try to click a Select Seats button (for modal flow).

        Enhanced with better error handling and retry logic.

        Returns:
            True if button was found and clicked
        """
        logger.info("attempting_to_click_select_seats")

        # Try configured selectors first
        # Use longer timeout since button renders slowly in modal
        for selector in BUTTON_SELECTORS['select_seats']:
            try:
                logger.debug("trying_selector", selector=selector)

                # Wait for element to be clickable (not just visible)
                # Button renders very slowly, so use 15-second timeout
                if await self._wait_for_clickable(selector, timeout=15000):
                    button = self.page.locator(selector).first

                    # Try to click
                    await button.click()
                    logger.info("clicked_select_seats_success", selector=selector)

                    # Verify something happened (URL changed or new elements appeared)
                    await asyncio.sleep(1)
                    return True

            except Exception as e:
                logger.warning("selector_click_failed",
                              selector=selector,
                              error=str(e))
                continue

        logger.warning("all_configured_selectors_failed_trying_fallback")

        # Add generic fallback using regex pattern
        try:
            # Match any element with text containing variations
            select_element = self.page.locator('text=/select.*seat|choose.*seat|pick.*seat/i').first

            if await self._wait_for_clickable('text=/select.*seat|choose.*seat|pick.*seat/i', timeout=15000):
                await select_element.click()
                logger.info("clicked_select_seats_generic_fallback_success")
                await asyncio.sleep(1)
                return True

        except Exception as e:
            logger.error("generic_fallback_failed", error=str(e))

        logger.error("all_select_seats_attempts_failed")

        # DEBUG: Take screenshot and save HTML for debugging
        try:
            timestamp = int(asyncio.get_event_loop().time())
            screenshot_path = f"./debug_select_seats_failed_{timestamp}.png"
            html_path = f"./debug_select_seats_failed_{timestamp}.html"

            await self.page.screenshot(path=screenshot_path)
            logger.info("screenshot_saved_for_debugging", path=screenshot_path)

            # Save HTML content
            html_content = await self.page.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info("html_saved_for_debugging", path=html_path)
        except Exception as e:
            logger.error("debug_file_save_failed", error=str(e))

        return False

    async def _try_click_select_seats_in_iframe(self) -> bool:
        """
        Try to click Select Seats button inside iframe modal.

        After clicking Buy Now, a modal iframe loads with the seat selection interface.
        This method finds the iframe and clicks the button inside it.

        Returns:
            True if button was found and clicked inside iframe
        """
        logger.info("attempting_to_click_select_seats_in_iframe")

        # Find the iframe containing the seat selection
        iframe_selectors = [
            'iframe[src*="embed.admitonelive"]',
            'iframe[src*="seatsio"]',
            'iframe[src*="seats.io"]',
        ]

        iframe_element = None
        for selector in iframe_selectors:
            try:
                iframe_element = self.page.frame_locator(selector)
                logger.info("found_iframe_for_seat_selection", selector=selector)
                break
            except:
                continue

        if not iframe_element:
            logger.error("no_iframe_found_for_seat_selection")
            return False

        # Try to find and click the Select Seats button inside the iframe
        for selector in BUTTON_SELECTORS['select_seats']:
            try:
                logger.debug("trying_selector_in_iframe", selector=selector)

                # Look for button inside the iframe
                button = iframe_element.locator(selector).first

                # Wait for button to be visible
                await button.wait_for(state='visible', timeout=15000)

                # Additional wait for button to become interactive
                await asyncio.sleep(1.0)

                # Click the button
                await button.click()
                logger.info("clicked_select_seats_in_iframe_success", selector=selector)

                await asyncio.sleep(1)
                return True

            except Exception as e:
                logger.warning("selector_click_in_iframe_failed",
                              selector=selector,
                              error=str(e))
                continue

        # Try generic fallback in iframe
        try:
            logger.info("trying_generic_fallback_in_iframe")
            button = iframe_element.locator('text=/select.*seat|choose.*seat|pick.*seat/i').first
            await button.wait_for(state='visible', timeout=15000)
            await asyncio.sleep(1.0)
            await button.click()
            logger.info("clicked_select_seats_in_iframe_generic_success")
            await asyncio.sleep(1)
            return True
        except Exception as e:
            logger.error("generic_fallback_in_iframe_failed", error=str(e))

        logger.error("all_iframe_select_seats_attempts_failed")

        # DEBUG: Take screenshot of main page (iframe content may not be captured)
        try:
            timestamp = int(asyncio.get_event_loop().time())
            screenshot_path = f"./debug_iframe_select_seats_failed_{timestamp}.png"
            await self.page.screenshot(path=screenshot_path)
            logger.info("iframe_screenshot_saved", path=screenshot_path)
        except Exception as e:
            logger.error("iframe_screenshot_failed", error=str(e))

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
