"""
Scraper implementations for different PreKindle ticket scenarios
"""

from .base import BaseScraper
from .seatsio_scraper import SeatsIOScraper
from .general_admission_scraper import GeneralAdmissionScraper
from .svg_seatmap_scraper import SvgSeatmapScraper

__all__ = [
    'BaseScraper',
    'SeatsIOScraper',
    'GeneralAdmissionScraper',
    'SvgSeatmapScraper',
]


def get_scraper_for_scenario(scenario: str) -> BaseScraper:
    """
    Factory function to get appropriate scraper for detected scenario.

    Args:
        scenario: One of 'seatsio', 'general_admission', 'svg_seatmap', or 'unknown'

    Returns:
        Appropriate scraper instance
    """
    scrapers = {
        'seatsio': SeatsIOScraper,
        'general_admission': GeneralAdmissionScraper,
        'svg_seatmap': SvgSeatmapScraper,
    }

    scraper_class = scrapers.get(scenario)

    if scraper_class:
        return scraper_class()

    # Default to general admission for unknown scenarios
    return GeneralAdmissionScraper()
