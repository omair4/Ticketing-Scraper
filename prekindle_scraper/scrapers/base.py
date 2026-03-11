"""
Base scraper class defining the interface for all PreKindle scrapers
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import re
import structlog
from bs4 import BeautifulSoup

from models import ScrapeResult, Seat, TicketCategory, EventInfo, SeatStatus
from browser import CapturedResponse

logger = structlog.get_logger()


class BaseScraper(ABC):
    """
    Abstract base class for PreKindle ticket scrapers.

    Each scenario (seatsio, general_admission, svg_seatmap) implements this interface.
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

        Args:
            captured_responses: List of captured API responses
            page_html: Current page HTML content (checkout/find-tickets page)
            event_url: Original promo URL
            event_info_override: Optional pre-extracted event info from promo page

        Returns:
            ScrapeResult with extracted data
        """
        pass

    @abstractmethod
    def get_scenario_name(self) -> str:
        """Return the scenario name (e.g., 'seatsio', 'general_admission')"""
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
        # First: responses with body
        for response in responses:
            if pattern in response.url and response.body:
                return response

        # Fallback: any match
        for response in responses:
            if pattern in response.url:
                return response

        return None

    def find_all_responses_by_pattern(self, responses: list[CapturedResponse],
                                       pattern: str) -> list[CapturedResponse]:
        """Find all responses matching a URL pattern"""
        return [r for r in responses if pattern in r.url and r.body]

    def create_result(self, event_info: EventInfo, seats: list[Seat] = None,
                      ticket_categories: list[TicketCategory] = None,
                      api_urls: dict = None, scrape_method: str = "browser",
                      delivery_methods: list[str] = None,
                      html_pricing: Optional[Dict[str, Dict[str, Any]]] = None) -> ScrapeResult:
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
            delivery_methods=delivery_methods,
            html_pricing=html_pricing,
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

    def extract_event_info_from_promo_html(self, promo_html: str, event_url: str) -> EventInfo:
        """
        Extract event information from the PreKindle promo page HTML.

        Parses:
        - h1 → event title
        - Date/Time/Location/Age Limit from strong-labeled fields
        - Organizer from header link
        """
        event_name = "Unknown Event"
        venue_name = "Unknown Venue"
        venue_address = None
        event_date = None
        doors_time = None
        show_time = None
        age_limit = None
        organizer_name = None
        event_id = self._extract_event_id(event_url)

        try:
            soup = BeautifulSoup(promo_html, 'html.parser')

            # Event title from <h1>
            h1 = soup.find('h1')
            if h1:
                event_name = h1.get_text(strip=True)

            # Parse labeled fields: strong text followed by sibling text
            # Pattern: <strong>Label</strong> value text
            for strong in soup.find_all('strong'):
                label = strong.get_text(strip=True).rstrip(':').strip()
                # Get the text following the strong element
                next_text = ''
                next_sibling = strong.next_sibling
                if next_sibling:
                    if hasattr(next_sibling, 'get_text'):
                        next_text = next_sibling.get_text(strip=True)
                    else:
                        next_text = str(next_sibling).strip()

                # If next_text is empty, try parent's full text minus the label
                if not next_text:
                    parent = strong.parent
                    if parent:
                        full_text = parent.get_text(strip=True)
                        if full_text.startswith(label):
                            next_text = full_text[len(label):].strip().lstrip(':').strip()

                label_lower = label.lower()

                if label_lower == 'date':
                    event_date = next_text
                elif label_lower == 'time':
                    # Parse doors and showtime from time text
                    time_text = next_text
                    if time_text:
                        # Try to split "Doors 7:00pm Showtime 8:00pm" pattern
                        doors_match = re.search(r'doors?\s*:?\s*(\d+:\d+\s*[ap]m)', time_text, re.IGNORECASE)
                        show_match = re.search(r'show(?:time)?\s*:?\s*(\d+:\d+\s*[ap]m)', time_text, re.IGNORECASE)
                        if doors_match:
                            doors_time = doors_match.group(1)
                        if show_match:
                            show_time = show_match.group(1)
                        if not doors_time and not show_time:
                            # Just a single time
                            show_time = time_text
                elif label_lower == 'location':
                    # Location may contain venue name + address
                    # Try to split on newlines or <br> tags
                    parent = strong.parent
                    if parent:
                        # Get all text nodes
                        texts = []
                        for child in parent.children:
                            if hasattr(child, 'get_text'):
                                t = child.get_text(strip=True)
                            else:
                                t = str(child).strip()
                            if t and t.lower() != label_lower and t != ':':
                                texts.append(t)
                        if texts:
                            venue_name = texts[0]
                            if len(texts) > 1:
                                venue_address = ', '.join(texts[1:])
                        elif next_text:
                            venue_name = next_text
                    elif next_text:
                        venue_name = next_text
                elif label_lower == 'age limit':
                    age_limit = next_text

            # Organizer from header link (e.g., "Presented by X")
            for a in soup.find_all('a'):
                href = a.get('href', '')
                if '/organizer/' in href or '/promoter/' in href:
                    organizer_name = a.get_text(strip=True)
                    break

        except Exception as e:
            logger.warning("promo_html_parsing_failed", error=str(e))

        return EventInfo(
            event_id=event_id,
            event_name=event_name,
            venue_name=venue_name,
            venue_address=venue_address,
            event_date=event_date,
            event_url=event_url,
            organizer_name=organizer_name,
            age_limit=age_limit,
            doors_time=doors_time,
            show_time=show_time,
        )

    def _extract_event_id(self, event_url: str) -> str:
        """Extract PreKindle event ID from promo URL."""
        # Pattern: /promo/id/{eventId} or /find-tickets/id/{eventId} or /checkout/id/{eventId}
        match = re.search(r'/(?:promo|find-tickets|checkout)/id/(-?\d+)', event_url)
        if match:
            return match.group(1)
        return "unknown"
