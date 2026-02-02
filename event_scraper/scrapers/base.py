"""
Base scraper class defining the interface for all scrapers
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict
import structlog
from bs4 import BeautifulSoup

from models import ScrapeResult, Seat, EventInfo, SeatStatus
from browser import CapturedResponse

logger = structlog.get_logger()


class BaseScraper(ABC):
    """
    Abstract base class for ticket scrapers.
    
    Each scenario (seatsio, direct_api) implements this interface.
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
        Extract seat data from captured responses.

        Args:
            captured_responses: List of captured API responses
            page_html: Current page HTML content
            event_url: Original event URL (ticket_url)
            event_info_override: Optional event metadata; when provided, use instead of extracting from page

        Returns:
            ScrapeResult with extracted seat data
        """
        pass
    
    @abstractmethod
    def get_scenario_name(self) -> str:
        """Return the scenario name (e.g., 'seatsio', 'direct_api')"""
        pass
    
    def add_error(self, error: str):
        """Add an error message"""
        self.errors.append(error)
        logger.error("scraper_error", scenario=self.get_scenario_name(), error=error)
    
    def add_warning(self, warning: str):
        """Add a warning message"""
        self.warnings.append(warning)
        logger.warning("scraper_warning", scenario=self.get_scenario_name(), warning=warning)
    
    def find_response_by_pattern(self, responses: list[CapturedResponse], 
                                  pattern: str) -> Optional[CapturedResponse]:
        """Find first response matching a URL pattern, prioritizing API endpoints"""
        # Priority order: API endpoints first, then responses with bodies, then any match
        api_keywords = ['/api/', 'object-statuses', 'seating-plan']
        
        # First try: API endpoints with body
        for response in responses:
            if pattern in response.url and response.body:
                if any(keyword in response.url for keyword in api_keywords):
                    return response
        
        # Second try: Any response with body
        for response in responses:
            if pattern in response.url and response.body:
                return response
        
        # Third try: Any matching response (even without body, for debugging)
        for response in responses:
            if pattern in response.url:
                return response
        
        return None
    
    def find_all_responses_by_pattern(self, responses: list[CapturedResponse],
                                       pattern: str) -> list[CapturedResponse]:
        """Find all responses matching a URL pattern"""
        return [r for r in responses if pattern in r.url and r.body]
    
    def create_result(self, event_info: EventInfo, seats: list[Seat] = None,
                     ticket_categories: list = None, resale_listings: list = None) -> ScrapeResult:
        """Create a ScrapeResult from extracted data"""
        from models import TicketCategory, ResaleListing
        return ScrapeResult(
            event=event_info,
            scenario_type=self.get_scenario_name(),
            seats=seats or [],
            ticket_categories=ticket_categories or [],
            resale_listings=resale_listings or [],
            errors=self.errors,
            warnings=self.warnings
        )
    
    def normalize_section_name(self, raw_name: str) -> str:
        """
        Normalize section names to be more readable.
        
        Examples:
            'MEZZL' -> 'Mezzanine Left'
            'ORCHR' -> 'Orchestra Right'
            'ORCHC' -> 'Orchestra Center'
        """
        mappings = {
            'MEZZL': 'Mezzanine Left',
            'MEZZR': 'Mezzanine Right',
            'MEZZC': 'Mezzanine Center',
            'MEZZ': 'Mezzanine',
            'ORCHL': 'Orchestra Left',
            'ORCHR': 'Orchestra Right',
            'ORCHC': 'Orchestra Center',
            'ORCH': 'Orchestra',
            'BALCL': 'Balcony Left',
            'BALCR': 'Balcony Right',
            'BALC': 'Balcony',
        }
        
        upper = raw_name.upper().strip()
        return mappings.get(upper, raw_name)
    
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
    
    def _extract_event_info_from_html(self, page_html: str) -> Optional[Dict[str, Optional[str]]]:
        """
        Extract event information from the main event page HTML.
        
        Extracts:
        - Event name: <h1> text content
        - Event subtitle: First <p> after <h1> (if exists)
        - Date: Text after "Date" label in Event Details section
        - Time: Text after "Time" label in Event Details section
        - Venue: Text after "Venue" label in Event Details section
        - Address: Text after "Address" label in Event Details section
        
        Returns:
            Dict with keys: event_name, event_date, event_time, venue_name, venue_address
            Or None if parsing fails
        """
        try:
            soup = BeautifulSoup(page_html, 'html.parser')
            
            result = {
                'event_name': None,
                'event_date': None,
                'event_time': None,
                'venue_name': None,
                'venue_address': None
            }
            
            # Extract event name from <h1>
            h1 = soup.find('h1')
            if h1:
                event_name = h1.get_text(strip=True)
                result['event_name'] = event_name
                
                # Check for subtitle (first <p> after <h1>, may not be immediate sibling)
                # Look for the next <p> element after h1
                next_p = h1.find_next('p')
                if next_p:
                    subtitle = next_p.get_text(strip=True)
                    # Only use as subtitle if it's not empty and not too long (likely not a description)
                    if subtitle and len(subtitle) < 200:  # Subtitle should be relatively short
                        # Combine event name and subtitle
                        result['event_name'] = f"{event_name} - {subtitle}"
            
            # Find "Event Details" section
            event_details_heading = soup.find('h3', string=lambda text: text and 'Event Details' in text)
            if not event_details_heading:
                # Try case-insensitive search
                for h3 in soup.find_all('h3'):
                    if h3.get_text(strip=True).lower() == 'event details':
                        event_details_heading = h3
                        break
            
            if event_details_heading:
                # Get the container after the heading
                details_container = event_details_heading.find_next_sibling()
                if details_container:
                    # Find all paragraphs in the container
                    paragraphs = details_container.find_all('p')
                    
                    # Match labels to values
                    for i, p in enumerate(paragraphs):
                        text = p.get_text(strip=True)
                        
                        # Check if this is a label
                        if text == 'Date' and i + 1 < len(paragraphs):
                            date_p = paragraphs[i + 1]
                            result['event_date'] = date_p.get_text(strip=True)
                        
                        elif text == 'Time' and i + 1 < len(paragraphs):
                            time_p = paragraphs[i + 1]
                            result['event_time'] = time_p.get_text(strip=True)
                        
                        elif text == 'Venue' and i + 1 < len(paragraphs):
                            venue_p = paragraphs[i + 1]
                            result['venue_name'] = venue_p.get_text(strip=True)
                        
                        elif text == 'Address' and i + 1 < len(paragraphs):
                            address_p = paragraphs[i + 1]
                            result['venue_address'] = address_p.get_text(strip=True)
            
            # Only return if we extracted at least the event name
            if result['event_name']:
                logger.debug("extracted_event_info_from_html", 
                           event_name=result['event_name'],
                           has_date=result['event_date'] is not None,
                           has_venue=result['venue_name'] is not None)
                return result
            else:
                logger.debug("html_parsing_failed_no_event_name")
                return None
                
        except Exception as e:
            logger.warning("html_parsing_failed", error=str(e))
            return None
