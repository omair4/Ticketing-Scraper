"""
Pydantic models for PreKindle ticket data
"""

from pydantic import BaseModel, Field, field_validator, computed_field
from typing import Optional, List, Dict, Any
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
    restricted_view: Optional[bool] = Field(default=None, description="Whether the seat has restricted view")
    x: Optional[float] = Field(default=None, description="X coordinate from chart")
    y: Optional[float] = Field(default=None, description="Y coordinate from chart")
    ada: Optional[bool] = Field(default=None, description="ADA accessible seat")

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
        if v is None:
            return ""
        return str(v).strip()

    @field_validator('price', 'service_fee', mode='before')
    @classmethod
    def parse_price(cls, v):
        if v is None:
            return None
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        if isinstance(v, str):
            cleaned = v.replace('$', '').replace(',', '').replace('CAD', '').strip()
            if cleaned:
                return Decimal(cleaned)
        return Decimal('0')

    @field_validator('restricted_view', mode='before')
    @classmethod
    def parse_restricted_view(cls, v):
        if v is None:
            return None
        return bool(int(float(v)))

    def to_display_string(self) -> str:
        return f"{self.section} - Row {self.row} - Seat {self.seat}"


class TicketCategory(BaseModel):
    """General admission ticket category (no individual seats)"""
    category_name: str = Field(..., description="Ticket category name (e.g., 'General Admission')")
    category_id: str = Field(..., description="Internal category/item ID")
    price: Decimal = Field(..., ge=0, description="Base ticket price")
    service_fee: Optional[Decimal] = Field(default=None, ge=0, description="Service/booking fee")
    current_stock: Optional[int] = Field(default=None, description="Available quantity (max from dropdown)")
    is_out_of_stock: bool = Field(default=False, description="Whether category is out of stock")
    group_name: Optional[str] = Field(default=None, description="Group name if applicable")
    ticket_type: Optional[str] = Field(default=None, description="Ticket type label (e.g., 'Ticket', 'RSVP', 'VIP')")

    @computed_field
    @property
    def total_price(self) -> Decimal:
        if self.service_fee:
            return self.price + self.service_fee
        return self.price

    @field_validator('price', 'service_fee', mode='before')
    @classmethod
    def parse_price(cls, v):
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
        if self.is_out_of_stock:
            return False
        if self.current_stock is not None:
            return self.current_stock > 0
        return True


class EventInfo(BaseModel):
    """Event metadata — extended for PreKindle"""
    event_id: str
    event_name: str
    venue_name: str
    venue_address: Optional[str] = None
    event_date: Optional[str] = None
    event_url: str
    organizer_name: Optional[str] = None
    age_limit: Optional[str] = None
    doors_time: Optional[str] = None
    show_time: Optional[str] = None


class ScrapeResult(BaseModel):
    """Complete scrape result — extended for PreKindle"""
    event: EventInfo
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    scenario_type: str = Field(..., description="seatsio, general_admission, or svg_seatmap")

    # Seats (for seated events)
    total_seats_found: int = 0
    available_seats_count: int = 0
    seats: List[Seat] = Field(default_factory=list)

    # Ticket categories (for GA events)
    ticket_categories: List[TicketCategory] = Field(default_factory=list)

    # Price summary
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None

    # PreKindle-specific
    api_urls: Optional[Dict[str, Any]] = Field(default=None, description="Discovered API URLs for direct re-querying")
    scrape_method: str = Field(default="browser", description="browser or html_parse")
    delivery_methods: Optional[List[str]] = Field(default=None, description="Available delivery methods")
    html_pricing: Optional[Dict[str, Dict[str, Any]]] = Field(default=None, description="Pricing data extracted from page HTML (seatsio scenario only)")

    # Errors/warnings
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def model_post_init(self, __context):
        """Calculate summary stats after initialization"""
        if self.seats:
            self.total_seats_found = len(self.seats)
            self.available_seats_count = len([s for s in self.seats if s.status == SeatStatus.AVAILABLE])

        prices = []
        if self.seats:
            prices.extend([s.price for s in self.seats if s.price > 0])
        if self.ticket_categories:
            prices.extend([c.price for c in self.ticket_categories if c.price > 0])

        if prices:
            self.min_price = min(prices)
            self.max_price = max(prices)

    def add_error(self, error: str):
        self.errors.append(error)

    def add_warning(self, warning: str):
        self.warnings.append(warning)


class PreKindleEventSummary(BaseModel):
    """Event summary from the PreKindle search API"""
    event_id: str = Field(..., description="PreKindle event ID (signed 64-bit integer)")
    title: str
    subtitle: Optional[str] = None
    organizer_name: Optional[str] = None
    performers: List[str] = Field(default_factory=list)
    start_time: Optional[str] = None
    details_primary: Optional[str] = None
    details_secondary: Optional[str] = None
    ticket_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    is_sold_out: bool = False
    purchasable: bool = True

    @field_validator('performers', mode='before')
    @classmethod
    def parse_performers(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return [p.get('name', str(p)) if isinstance(p, dict) else str(p) for p in v]
        return []
