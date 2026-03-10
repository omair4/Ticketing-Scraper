"""
Configuration settings for Tixr scraper
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
    datadome_wait: int = 5000  # Extra wait for DataDome challenge

    # Delays between navigations (seconds) — conservative for DataDome
    inter_event_delay: float = 3.0
    inter_group_delay: float = 5.0

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


TIXR_DOMAINS = ['tixr.com', 'www.tixr.com']

SITEMAP_URLS = [
    'https://www.tixr.com/catalog/group-sitemap-1.xml',
    'https://www.tixr.com/catalog/group-sitemap-2.xml',
]

SITEMAP_INDEX_URL = 'https://www.tixr.com/sitemap.xml'

# Default configuration instance
DEFAULT_CONFIG = ScraperConfig()
