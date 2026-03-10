"""
Scraper factory — selects the appropriate scraper based on scenario.
"""

from scrapers.base import BaseScraper
from scrapers.general_admission import GeneralAdmissionScraper
from scrapers.seatsio_scraper import SeatsIOScraper


def get_scraper_for_scenario(scenario: str) -> BaseScraper:
    """
    Factory function to get the appropriate scraper for a scenario.

    Args:
        scenario: 'general_admission' or 'seatsio'

    Returns:
        An instance of the appropriate scraper
    """
    scrapers = {
        'general_admission': GeneralAdmissionScraper,
        'seatsio': SeatsIOScraper,
    }

    scraper_class = scrapers.get(scenario)
    if scraper_class is None:
        raise ValueError(f"Unknown scenario: {scenario}. Available: {list(scrapers.keys())}")

    return scraper_class()
