"""
Base scraper class defining the interface for all scrapers
"""

from abc import ABC, abstractmethod
from typing import Optional
import structlog

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
    async def extract(self, captured_responses: list[CapturedResponse], 
                     page_html: str, event_url: str) -> ScrapeResult:
        """
        Extract seat data from captured responses.
        
        Args:
            captured_responses: List of captured API responses
            page_html: Current page HTML content
            event_url: Original event URL
        
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
        """Find first response matching a URL pattern"""
        for response in responses:
            if pattern in response.url and response.body:
                return response
        return None
    
    def find_all_responses_by_pattern(self, responses: list[CapturedResponse],
                                       pattern: str) -> list[CapturedResponse]:
        """Find all responses matching a URL pattern"""
        return [r for r in responses if pattern in r.url and r.body]
    
    def create_result(self, event_info: EventInfo, seats: list[Seat]) -> ScrapeResult:
        """Create a ScrapeResult from extracted data"""
        return ScrapeResult(
            event=event_info,
            scenario_type=self.get_scenario_name(),
            seats=seats,
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
