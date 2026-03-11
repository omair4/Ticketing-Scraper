"""
Configuration settings for the Tixr scraper.

This intentionally mirrors `event_scraper.config.ScraperConfig` so that
higher-level orchestration can treat scrapers consistently.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScraperConfig:
    """Main configuration for the Tixr scraper."""

    # Browser settings
    headless: bool = False  # Override via CLI for production
    browser_timeout: int = 30000  # 30 seconds
    navigation_timeout: int = 30000  # 30 seconds

    # Network settings
    request_timeout: int = 30  # seconds for httpx
    proxy: Optional[str] = None  # e.g. http://user:pass@host:port

    # Wait times (milliseconds)
    click_delay: int = 2000  # Wait after clicking buttons
    page_load_delay: int = 3000  # Wait for dynamic content

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


@dataclass
class APIPatterns:
    """
    URL patterns for different Tixr APIs.

    NOTE: These are placeholders and should be updated after we complete
    network reconnaissance on real Tixr event pages.
    """

    # TODO: adjust once we know the real endpoints
    # Examples of what we expect to find:
    # - Event metadata GraphQL/REST endpoint
    # - Ticket inventory / pricing endpoint
    # - Seatmap / reserved seating endpoint
    EVENT_API_PATTERN: str = "tixr.com"
    INVENTORY_API_PATTERN: str = "tixr.com"
    SEATMAP_API_PATTERN: str = "tixr.com"


TIXR_DOMAINS = [
    "tixr.com",
]


def is_tixr_domain(url: str) -> bool:
    """Check if a URL belongs to a Tixr-related domain."""
    from urllib.parse import urlparse

    try:
        hostname = urlparse(url).hostname or ""
        return any(hostname == d or hostname.endswith("." + d) for d in TIXR_DOMAINS)
    except Exception:
        return False


# Button selectors for navigation – to be refined after DOM inspection.
BUTTON_SELECTORS = {
    # Primary buy buttons
    "buy_now": [
        'button:has-text("Buy Tickets")',
        'button:has-text("Get Tickets")',
        'a:has-text("Buy Tickets")',
        'a:has-text("Get Tickets")',
    ],
    # Select seats or continue buttons (for reserved seating flows)
    "select_seats": [
        'button:has-text("Select Seats")',
        'button:has-text("Continue")',
        'a:has-text("Select Seats")',
    ],
    # Modal/popup close buttons
    "close_modal": [
        'button[aria-label="Close"]',
        ".close-button",
        '[class*="close"]',
    ],
}


# Default configuration instance
DEFAULT_CONFIG = ScraperConfig()

