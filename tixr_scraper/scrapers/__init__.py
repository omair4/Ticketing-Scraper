"""
Tixr scraper factory.

This exposes a `get_scraper_for_scenario()` function that returns the
appropriate scraper instance based on a scenario string. The concrete
scenarios will be added once we finalize the Tixr API/DOM patterns.
"""

from .base import BaseScraper


def get_scraper_for_scenario(scenario: str) -> BaseScraper:
    """
    Return a scraper instance for the given scenario.

    For now this returns a stub base implementation; once we discover the
    real scenarios (e.g., 'ga', 'seatmap_api'), we will route accordingly.
    """
    from .base import StubScraper

    return StubScraper()

