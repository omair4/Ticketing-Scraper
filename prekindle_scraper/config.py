"""
Configuration settings for PreKindle scraper
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScraperConfig:
    """Main configuration for the scraper"""

    # Browser settings
    headless: bool = False
    browser_timeout: int = 30000  # 30 seconds
    navigation_timeout: int = 30000

    # Network settings
    request_timeout: int = 30  # seconds for httpx
    proxy: Optional[str] = None

    # Wait times (milliseconds)
    click_delay: int = 2000
    page_load_delay: int = 3000
    queue_timeout: int = 120000  # 2 minutes for queue page

    # Retry settings
    max_retries: int = 3
    retry_delay: int = 2  # seconds

    # Output settings
    output_dir: str = "./output"

    # User agent
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # PreKindle-specific
    search_api_url: str = "https://web.prekindle.com/api/event/search"
    search_limit: int = 100
    timezone: str = "America/New_York"
    discovery_request_delay: float = 1.0  # seconds between paginated requests


PREKINDLE_DOMAINS = ['prekindle.com', 'web.prekindle.com']

BUTTON_SELECTORS = {
    "get_tickets": [
        'a:has-text("GET TICKETS")',
        'a:has-text("Get Tickets")',
        'a:has-text("Buy Tickets")',
        'a:has-text("RSVP")',
        'a[href*="checkout/id/"]',
        'a[href*="find-tickets/id/"]',
    ],
}

# Default configuration instance
DEFAULT_CONFIG = ScraperConfig()
