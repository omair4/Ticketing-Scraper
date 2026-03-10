"""
Base scraper class defining the interface for all Tixr scrapers
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import re
import structlog

from models import ScrapeResult, Seat, TicketCategory, EventInfo, SeatStatus
from browser import CapturedResponse

logger = structlog.get_logger()


class BaseScraper(ABC):
    """
    Abstract base class for Tixr ticket scrapers.

    Each scenario (general_admission, seatsio) implements this interface.
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
        event_api_data: Optional[dict] = None,
    ) -> ScrapeResult:
        """
        Extract ticket data from captured responses and event API data.

        Args:
            captured_responses: List of captured API responses
            page_html: Current page HTML content
            event_url: Original event URL
            event_api_data: Parsed /api/events/{id} response (primary data source)

        Returns:
            ScrapeResult with extracted data
        """
        pass

    @abstractmethod
    def get_scenario_name(self) -> str:
        """Return the scenario name (e.g., 'general_admission', 'seatsio')"""
        pass

    def add_error(self, error: str):
        self.errors.append(error)
        logger.error("scraper_error", scenario=self.get_scenario_name(), error=error)

    def add_warning(self, warning: str):
        self.warnings.append(warning)
        logger.warning("scraper_warning", scenario=self.get_scenario_name(), warning=warning)

    def find_response_by_pattern(self, responses: list[CapturedResponse],
                                  pattern: str) -> Optional[CapturedResponse]:
        """Find first response matching a URL pattern, prioritizing those with bodies"""
        for response in responses:
            if pattern in response.url and response.body:
                return response
        for response in responses:
            if pattern in response.url:
                return response
        return None

    def find_all_responses_by_pattern(self, responses: list[CapturedResponse],
                                       pattern: str) -> list[CapturedResponse]:
        """Find all responses matching a URL pattern"""
        return [r for r in responses if pattern in r.url and r.body]

    def extract_event_info_from_api(self, event_data: dict, event_url: str) -> EventInfo:
        """
        Extract event info from the Tixr /api/events/{id} response.
        """
        venue = event_data.get('venue', {}) or {}
        address = venue.get('address', {}) or {}
        group = event_data.get('group', {}) or {}

        venue_parts = []
        if address.get('address1'):
            venue_parts.append(address['address1'])
        if address.get('city'):
            venue_parts.append(address['city'])
        if address.get('state'):
            venue_parts.append(address['state'])
        if address.get('zipCode'):
            venue_parts.append(address['zipCode'])

        # Extract lineup
        lineup = []
        for lineup_entry in event_data.get('lineups', []) or []:
            for act in lineup_entry.get('acts', []) or []:
                artist = act.get('artist', {}) or {}
                lineup.append({
                    'name': artist.get('name', ''),
                    'type': act.get('type', ''),
                    'rank': act.get('rank', 0),
                    'image_url': artist.get('imageUrl', ''),
                })

        return EventInfo(
            event_id=str(event_data.get('id', 'unknown')),
            event_name=event_data.get('name', 'Unknown Event'),
            venue_name=venue.get('name', 'Unknown Venue'),
            venue_address=', '.join(venue_parts) if venue_parts else None,
            venue_city=address.get('city'),
            venue_state=address.get('state'),
            venue_country=address.get('country'),
            venue_timezone=venue.get('timezone'),
            venue_capacity=venue.get('capacity'),
            event_date=event_data.get('startDate'),
            end_date=event_data.get('endDate'),
            doors_date=event_data.get('doorsOpenDate'),
            event_url=event_url,
            group_name=group.get('name'),
            group_id=str(group.get('id', '')),
            group_subdomain=group.get('subdomain'),
            event_category=event_data.get('eventCategoryName'),
            lineup=lineup if lineup else None,
        )

    def create_result(self, event_info: EventInfo, seats: list[Seat] = None,
                      ticket_categories: list[TicketCategory] = None,
                      api_urls: dict = None, scrape_method: str = "browser",
                      raw_event_data: dict = None) -> ScrapeResult:
        """Create a ScrapeResult from extracted data"""
        return ScrapeResult(
            event=event_info,
            scenario_type=self.get_scenario_name(),
            seats=seats or [],
            ticket_categories=ticket_categories or [],
            errors=self.errors,
            warnings=self.warnings,
            api_urls=api_urls,
            scrape_method=scrape_method,
            raw_event_data=raw_event_data,
        )

    def parse_seat_status(self, status_str: str) -> SeatStatus:
        """Convert status string to SeatStatus enum"""
        status_lower = status_str.lower().strip()
        if status_lower in ('free', 'available', 'open'):
            return SeatStatus.AVAILABLE
        elif status_lower in ('booked', 'sold', 'taken'):
            return SeatStatus.BOOKED
        elif status_lower in ('held', 'hold'):
            return SeatStatus.HELD
        elif status_lower in ('reserved', 'reservedbytoken'):
            return SeatStatus.RESERVED
        else:
            return SeatStatus.UNKNOWN

    def _extract_event_id_from_url(self, event_url: str) -> str:
        """Extract Tixr event ID from URL. Pattern: /events/{slug}-{id}"""
        match = re.search(r'-(\d+)(?:\?|$)', event_url)
        if match:
            return match.group(1)
        return "unknown"
