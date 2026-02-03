"""
Configuration settings for AdmitONE website scraper
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScraperConfig:
    """Main configuration for the website scraper"""

    # Browser settings
    headless: bool = False
    browser_timeout: int = 30000  # 30 seconds
    navigation_timeout: int = 30000  # 30 seconds

    # Network settings
    request_timeout: int = 30  # seconds for httpx
    proxy: Optional[str] = None  # e.g. http://host:port or http://user:pass@host:port

    # Wait times (milliseconds)
    page_load_delay: int = 3000  # Wait for dynamic content

    # Retry settings
    max_retries: int = 3
    retry_delay: int = 2  # seconds

    # Output settings
    output_dir: str = "./output"

    # API settings
    base_url: str = "https://admitone.com"
    graphql_url: str = "https://graphql.admitone.com/"

    # User agent
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )


# Default configuration instance
DEFAULT_CONFIG = ScraperConfig()
