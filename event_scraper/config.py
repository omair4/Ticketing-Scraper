"""
Configuration settings for AdmitONE scraper
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class ScraperConfig:
    """Main configuration for the scraper"""
    
    # Browser settings
    headless: bool = False  # Set to True for production
    browser_timeout: int = 30000  # 30 seconds
    navigation_timeout: int = 30000  # 30 seconds (increased for slow-loading pages)
    
    # Network settings
    request_timeout: int = 30  # seconds for httpx
    proxy: Optional[str] = None  # e.g. http://host:port or http://user:pass@host:port
    
    # Wait times (milliseconds)
    click_delay: int = 2000  # Wait after clicking buttons (increased for iframe loading)
    page_load_delay: int = 3000  # Wait for dynamic content (increased for modal/iframe)
    
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
    """URL patterns for different APIs"""
    
    # Scenario 1: seats.io
    SEATSIO_STATUS_PATTERN = "cdn-eu.seatsio.net"
    SEATSIO_OBJECT_STATUS = "/events/object-statuses"
    ADMITONELIVE_EMBED = "embed.admitonelive.com/api/events/seated"
    
    # Scenario 2: Direct API
    TICKETS_ADMITONE = "tickets.admitone.com"
    SEATING_AVAILABILITY = "/seating-plan/availability"
    EVENT_DATA = "/events/"


# Button selectors for navigation
BUTTON_SELECTORS = {
    # Primary buy buttons
    "buy_now": [
        'button:has-text("Buy Now")',
        'a:has-text("Buy Now")',
        '[class*="buy"]:has-text("Buy")',
        'button:has-text("Get Tickets")',
        'a:has-text("Get Tickets")',
    ],
    # Select seats button (scenario 1)
    "select_seats": [
        'button:has-text("Select Seats")',
        'a:has-text("Select Seats")',
        '[class*="select"]:has-text("Select")',
    ],
    # Modal/popup close buttons
    "close_modal": [
        'button[aria-label="Close"]',
        '.close-button',
        '[class*="close"]',
    ]
}

# Default configuration instance
DEFAULT_CONFIG = ScraperConfig()
