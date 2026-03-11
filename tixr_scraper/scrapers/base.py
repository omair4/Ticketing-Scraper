"""
Base scraper class defining the interface for all Tixr scrapers.

This is intentionally very close to `event_scraper.scrapers.base.BaseScraper`
so that scenario implementations feel familiar.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict

import structlog
from bs4 import BeautifulSoup

from ..models import ScrapeResult, Seat, EventInfo, SeatStatus
from ..browser import CapturedResponse


logger = structlog.get_logger()


class BaseScraper(ABC):
    """
    Abstract base class for Tixr ticket scrapers.
    """

    def __init__(self):
        self.event_info: Optional[EventInfo] = None
        self.seats: list[Seat] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @abstractmethod
    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        """
        Extract ticket data from captured responses and page HTML.
        """
        raise NotImplementedError

    @abstractmethod
    def get_scenario_name(self) -> str:
        """Return the scenario name (e.g., 'ga', 'seatmap_api')."""
        raise NotImplementedError

    def add_error(self, error: str):
        self.errors.append(error)
        logger.error("scraper_error", scenario=self.get_scenario_name(), error=error)

    def add_warning(self, warning: str):
        self.warnings.append(warning)
        logger.warning(
            "scraper_warning", scenario=self.get_scenario_name(), warning=warning
        )

    def find_response_by_pattern(
        self, responses: list[CapturedResponse], pattern: str
    ) -> Optional[CapturedResponse]:
        """Find first response matching a URL pattern, prioritizing those with bodies."""
        for response in responses:
            if pattern in response.url and response.body:
                return response

        for response in responses:
            if pattern in response.url:
                return response

        return None

    def find_all_responses_by_pattern(
        self, responses: list[CapturedResponse], pattern: str
    ) -> list[CapturedResponse]:
        """Find all responses matching a URL pattern."""
        return [r for r in responses if pattern in r.url and r.body]

    def create_result(
        self,
        event_info: EventInfo,
        seats: list[Seat] = None,
        ticket_categories: list = None,
    ) -> ScrapeResult:
        """Create a ScrapeResult from extracted data."""
        return ScrapeResult(
            event=event_info,
            scenario_type=self.get_scenario_name(),
            seats=seats or [],
            ticket_categories=ticket_categories or [],
            errors=self.errors,
            warnings=self.warnings,
        )

    def parse_seat_status(self, status_str: str) -> SeatStatus:
        """Convert status string to SeatStatus enum."""
        status_lower = status_str.lower().strip()

        if status_lower in ("free", "available", "open"):
            return SeatStatus.AVAILABLE
        if status_lower in ("booked", "sold", "taken"):
            return SeatStatus.BOOKED
        if status_lower in ("held", "hold"):
            return SeatStatus.HELD
        if status_lower in ("reserved", "reservedbytoken"):
            return SeatStatus.RESERVED
        return SeatStatus.UNKNOWN

    def _extract_event_info_from_html(
        self, page_html: str
    ) -> Optional[Dict[str, Optional[str]]]:
        """
        Extract basic event information from the Tixr event page HTML.

        This is a heuristic parser and will likely need refinement once we know
        the exact DOM structure. For now we look for a main <h1> title and
        common labels like date / time / venue.
        """
        try:
            soup = BeautifulSoup(page_html, "html.parser")

            result: Dict[str, Optional[str]] = {
                "event_name": None,
                "event_date": None,
                "event_time": None,
                "venue_name": None,
                "venue_address": None,
            }

            # Event name from <h1>
            h1 = soup.find("h1")
            if h1:
                result["event_name"] = h1.get_text(strip=True)

            # Very generic heuristics we can refine later:
            # look for elements with labels like "Date", "Time", "Venue"
            for label in ["Date", "Time", "Venue", "Location"]:
                label_el = soup.find(string=lambda t: isinstance(t, str) and label in t)
                if not label_el:
                    continue
                parent = label_el.parent
                if not parent:
                    continue
                # naive: next sibling text
                next_el = parent.find_next_sibling()
                text = None
                if next_el:
                    text = next_el.get_text(strip=True)
                if not text:
                    continue

                if label == "Date":
                    result["event_date"] = text
                elif label == "Time":
                    result["event_time"] = text
                elif label in ("Venue", "Location"):
                    # We keep the first venue as name; any extra content can be pushed into address.
                    if not result["venue_name"]:
                        result["venue_name"] = text
                    else:
                        result["venue_address"] = text

            if result["event_name"]:
                return result
            return None
        except Exception as e:
            logger.warning("html_parsing_failed", error=str(e))
            return None


class StubScraper(BaseScraper):
    """
    Temporary scraper used until concrete Tixr scenarios are implemented.
    It simply returns a mostly-empty ScrapeResult with any basic event info
    we can glean from the page HTML.
    """

    def get_scenario_name(self) -> str:
        return "tixr_stub"

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        # Use override if provided; otherwise try parsing from HTML.
        if event_info_override is not None:
            event_info = event_info_override
        else:
            info = self._extract_event_info_from_html(page_html) or {}
            event_info = EventInfo(
                event_id="unknown",
                event_name=info.get("event_name") or "Unknown Tixr Event",
                venue_name=info.get("venue_name") or "Unknown Venue",
                venue_address=info.get("venue_address"),
                event_date=info.get("event_date"),
                event_time=info.get("event_time"),
                event_url=event_url,
            )

        if not captured_responses:
            self.add_warning(
                "No Tixr API responses captured yet; this is expected for the stub scraper."
            )

        return self.create_result(event_info=event_info)

