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
    restricted_view: Optional[bool] = Field(default=None, description="Whether the seat has restricted view (true/false, only included if present in API)")
    # Position from chart (seats.io)
    x: Optional[float] = Field(default=None, description="X coordinate from chart")
    y: Optional[float] = Field(default=None, description="Y coordinate from chart")
    ada: Optional[bool] = Field(default=None, description="True if semiAmbulatorySeat, hearingImpaired, or signLanguageInterpretation")
    
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
    
    @field_validator('restricted_view', mode='before')
    @classmethod
    def parse_restricted_view(cls, v):
        """Ensure restricted_view is a proper boolean when not None"""
        if v is None:
            return None
        # Convert to proper boolean (handle int 0/1, float 0.0/1.0, bool)
        return bool(int(float(v)))
    
    def to_display_string(self) -> str:
        """Human-readable seat identifier"""
        return f"{self.section} - Row {self.row} - Seat {self.seat}"


class TicketCategory(BaseModel):
    """General admission ticket category (no individual seats)"""
    category_name: str = Field(..., description="Ticket category name (e.g., 'General Admission')")
    category_id: str = Field(..., description="Internal category/item ID")
    price: Decimal = Field(..., ge=0, description="Base ticket price")
    service_fee: Optional[Decimal] = Field(default=None, ge=0, description="Service/booking fee")
    current_stock: Optional[int] = Field(default=None, description="Available quantity")
    is_out_of_stock: bool = Field(default=False, description="Whether category is out of stock")
    group_name: Optional[str] = Field(default=None, description="Group name if applicable")
    
    @computed_field
    @property
    def total_price(self) -> Decimal:
        """Calculate total price including fees"""
        if self.service_fee:
            return self.price + self.service_fee
        return self.price
    
    @field_validator('price', 'service_fee', mode='before')
    @classmethod
    def parse_price(cls, v):
        """Parse price from various formats"""
        if v is None:
            return None
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        if isinstance(v, str):
            cleaned = v.replace('$', '').replace(',', '').replace('CAD', '').strip()
            if cleaned:
                return Decimal(cleaned)
        return Decimal('0')
    
    @property
    def is_available(self) -> bool:
        """Check if category has available tickets"""
        if self.is_out_of_stock:
            return False
        if self.current_stock is not None:
            return self.current_stock > 0
        return True


class ResaleListing(BaseModel):
    """Resale listing for tickets"""
    listing_id: str = Field(..., description="Resale listing ID")
    ticket_category: str = Field(..., description="Ticket category name")
    quantity: int = Field(..., ge=1, description="Number of tickets in this listing")
    price_per_ticket: Decimal = Field(..., ge=0, description="Price per ticket")
    service_fee_per_ticket: Optional[Decimal] = Field(default=None, ge=0, description="Service fee per ticket")
    
    @computed_field
    @property
    def total_price_per_ticket(self) -> Decimal:
        """Calculate total price per ticket including fees"""
        if self.service_fee_per_ticket:
            return self.price_per_ticket + self.service_fee_per_ticket
        return self.price_per_ticket
    
    @computed_field
    @property
    def total_listing_price(self) -> Decimal:
        """Calculate total price for entire listing"""
        return self.total_price_per_ticket * Decimal(str(self.quantity))
    
    @field_validator('price_per_ticket', 'service_fee_per_ticket', mode='before')
    @classmethod
    def parse_price(cls, v):
        """Parse price from various formats"""
        if v is None:
            return None
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        if isinstance(v, str):
            cleaned = v.replace('$', '').replace(',', '').replace('CAD', '').strip()
            if cleaned:
                return Decimal(cleaned)
        return Decimal('0')


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
    
    # Seats (for seated events)
    total_seats_found: int = 0
    available_seats_count: int = 0
    seats: List[Seat] = Field(default_factory=list)
    
    # Ticket categories (for general admission events)
    ticket_categories: List[TicketCategory] = Field(default_factory=list)
    
    # Resale listings (optional)
    resale_listings: List[ResaleListing] = Field(default_factory=list)
    
    # Price summary
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    
    # External redirect
    external_redirect_url: Optional[str] = Field(
        default=None,
        description="External ticket URL when event redirects to third-party vendor"
    )

    # Errors/warnings
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    
    def model_post_init(self, __context):
        """Calculate summary stats after initialization"""
        # Calculate seat stats
        if self.seats:
            self.total_seats_found = len(self.seats)
            self.available_seats_count = len([s for s in self.seats if s.status == SeatStatus.AVAILABLE])
        
        # Calculate price summary from all sources
        prices = []
        if self.seats:
            prices.extend([s.price for s in self.seats if s.price > 0])
        if self.ticket_categories:
            prices.extend([c.price for c in self.ticket_categories if c.price > 0])
        if self.resale_listings:
            prices.extend([r.price_per_ticket for r in self.resale_listings if r.price_per_ticket > 0])
        
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
