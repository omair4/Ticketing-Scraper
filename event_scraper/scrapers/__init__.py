"""
Scraper implementations for different API scenarios
"""

from .base import BaseScraper
from .seatsio_scraper import SeatsIOScraper
from .direct_api_scraper import DirectAPIScraper
from .graphql_scraper import GraphQLScraper
from .embed_widget_scraper import EmbedWidgetScraper
from .external_redirect_scraper import ExternalRedirectScraper

__all__ = [
    'BaseScraper',
    'SeatsIOScraper',
    'DirectAPIScraper',
    'GraphQLScraper',
    'EmbedWidgetScraper',
    'ExternalRedirectScraper',
]


def get_scraper_for_scenario(scenario: str) -> BaseScraper:
    """
    Factory function to get appropriate scraper for detected scenario.

    Args:
        scenario: One of 'seatsio', 'direct_api', 'graphql', 'embed_widget',
                  'external_redirect', or 'unknown'

    Returns:
        Appropriate scraper instance
    """
    scrapers = {
        'seatsio': SeatsIOScraper,
        'direct_api': DirectAPIScraper,
        'graphql': GraphQLScraper,
        'embed_widget': EmbedWidgetScraper,
        'external_redirect': ExternalRedirectScraper,
    }

    scraper_class = scrapers.get(scenario)

    if scraper_class:
        return scraper_class()

    # Default to seatsio scraper for unknown scenarios
    return SeatsIOScraper()
