"""
Pydantic models for ticket data
"""

from pydantic import BaseModel, Field, field_validator, computed_field
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
from enum import Enum


class SeatStatus(str, Enum):
    """Possible seat statuses"""
    AVAILABLE = "available"
    BOOKED = "booked"
    HELD = "held"
    RESERVED = "reserved"
    UNKNOWN = "unknown"


class Seat(BaseModel):
    """Individual seat data"""
    section: str = Field(..., description="Section/area name")
    row: str = Field(..., description="Row identifier")
    seat: str = Field(..., description="Seat number/identifier")
    price: Decimal = Field(..., ge=0, description="Base ticket price")
    service_fee: Optional[Decimal] = Field(default=None, ge=0, description="Service/booking fee")
    status: SeatStatus = Field(default=SeatStatus.AVAILABLE, description="Seat availability status")
    
    # Optional additional info
    price_level: Optional[str] = Field(default=None, description="Price level/tier name")
    price_level_id: Optional[str] = Field(default=None, description="Internal price level ID")
    
    @computed_field
    @property
    def total_price(self) -> Decimal:
        """Calculate total price including fees"""
        if self.service_fee:
            return self.price + self.service_fee
        return self.price
    
    @field_validator('section', 'row', 'seat', mode='before')
    @classmethod
    def clean_string(cls, v):
        """Clean and normalize string fields"""
        if v is None:
            return ""
        return str(v).strip()
    
    @field_validator('price', 'service_fee', mode='before')
    @classmethod
    def parse_price(cls, v):
        """Parse price from various formats"""
        if v is None:
            return None
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        if isinstance(v, str):
            # Remove currency symbols and commas
            cleaned = v.replace('$', '').replace(',', '').replace('CAD', '').strip()
            if cleaned:
                return Decimal(cleaned)
        return Decimal('0')
    
    def to_display_string(self) -> str:
        """Human-readable seat identifier"""
        return f"{self.section} - Row {self.row} - Seat {self.seat}"


class EventInfo(BaseModel):
    """Event metadata"""
    event_id: str
    event_name: str
    venue_name: str
    venue_address: Optional[str] = None
    event_date: Optional[str] = None
    event_url: str


class ScrapeResult(BaseModel):
    """Complete scrape result"""
    event: EventInfo
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    scenario_type: str = Field(..., description="seatsio or direct_api")
    total_seats_found: int = 0
    available_seats_count: int = 0
    seats: List[Seat] = Field(default_factory=list)
    
    # Price summary
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    
    # Errors/warnings
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    
    def model_post_init(self, __context):
        """Calculate summary stats after initialization"""
        if self.seats:
            self.total_seats_found = len(self.seats)
            self.available_seats_count = len([s for s in self.seats if s.status == SeatStatus.AVAILABLE])
            prices = [s.price for s in self.seats if s.price > 0]
            if prices:
                self.min_price = min(prices)
                self.max_price = max(prices)
    
    def add_error(self, error: str):
        """Add an error message"""
        self.errors.append(error)
    
    def add_warning(self, warning: str):
        """Add a warning message"""
        self.warnings.append(warning)


class CapturedRequest(BaseModel):
    """Captured network request data"""
    url: str
    method: str = "GET"
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    response_body: Optional[str] = None
    
    def is_json(self) -> bool:
        """Check if response is JSON"""
        return self.content_type and 'application/json' in self.content_type


# Price level mapping for Scenario 1
class PriceLevel(BaseModel):
    """Price level from AdmitONE API"""
    level_id: str
    level_name: str
    price: Decimal
    service_fee: Decimal
    inventory: int = 0
    active: bool = True


# Price band mapping for Scenario 2
class PriceBand(BaseModel):
    """Price band from tickets.admitone.com API"""
    id: int
    name: str
    colour: Optional[str] = None
    sort_order: int = 0
    has_available_tickets: bool = True
    # Note: price comes from ticket types, not directly in price_bands
