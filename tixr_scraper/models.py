"""
Pydantic models for Tixr ticket data
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


class SaleState(str, Enum):
    """Tixr sale states"""
    OPEN = "OPEN"
    QUEUED = "QUEUED"
    SOLD_OUT = "SOLD_OUT"
    HIDE = "HIDE"
    CLOSED = "CLOSED"


class Seat(BaseModel):
    """Individual seat data (for seatsio events)"""
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
            cleaned = v.replace('$', '').replace(',', '').replace('USD', '').strip()
            if cleaned:
                return Decimal(cleaned)
        return Decimal('0')

    def to_display_string(self) -> str:
        return f"{self.section} - Row {self.row} - Seat {self.seat}"


class TicketCategory(BaseModel):
    """Ticket category (GA sale + tier combination)"""
    category_name: str = Field(..., description="Sale category name (e.g., 'General Admission')")
    category_id: str = Field(..., description="Internal sale/tier ID")
    price: Decimal = Field(..., ge=0, description="All-in ticket price")
    service_fee: Optional[Decimal] = Field(default=None, ge=0, description="Service/booking fee (usually included in Tixr)")
    current_stock: Optional[int] = Field(default=None, description="Available quantity (not exposed by Tixr API)")
    is_out_of_stock: bool = Field(default=False, description="Whether category is sold out")
    group_name: Optional[str] = Field(default=None, description="Sale group/category label")
    ticket_type: Optional[str] = Field(default=None, description="Sale category (GA/VIP/PARKING/CAMPING)")
    sale_state: Optional[str] = Field(default=None, description="Tixr sale state (OPEN/QUEUED/SOLD_OUT/HIDE)")
    tier_name: Optional[str] = Field(default=None, description="Tier name within sale")
    max_tickets: Optional[int] = Field(default=None, description="Max tickets allowed per order")
    delivery_type: Optional[str] = Field(default=None, description="Delivery type (MOBILE/WILL_CALL/etc.)")

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
            cleaned = v.replace('$', '').replace(',', '').replace('USD', '').strip()
            if cleaned:
                return Decimal(cleaned)
        return Decimal('0')

    @property
    def is_available(self) -> bool:
        if self.is_out_of_stock:
            return False
        if self.sale_state and self.sale_state in ('SOLD_OUT', 'HIDE', 'CLOSED'):
            return False
        return True


class EventInfo(BaseModel):
    """Event metadata"""
    event_id: str
    event_name: str
    venue_name: str
    venue_address: Optional[str] = None
    venue_city: Optional[str] = None
    venue_state: Optional[str] = None
    venue_country: Optional[str] = None
    venue_timezone: Optional[str] = None
    venue_capacity: Optional[int] = None
    event_date: Optional[str] = None
    end_date: Optional[str] = None
    doors_date: Optional[str] = None
    event_url: str
    group_name: Optional[str] = None
    group_id: Optional[str] = None
    group_subdomain: Optional[str] = None
    event_category: Optional[str] = None
    lineup: Optional[List[Dict[str, Any]]] = None


class ScrapeResult(BaseModel):
    """Complete scrape result"""
    event: EventInfo
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    scenario_type: str = Field(..., description="general_admission or seatsio")

    # Seats (for seated events)
    total_seats_found: int = 0
    available_seats_count: int = 0
    seats: List[Seat] = Field(default_factory=list)

    # Ticket categories (for GA events)
    ticket_categories: List[TicketCategory] = Field(default_factory=list)

    # Price summary
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None

    # Tixr-specific
    api_urls: Optional[Dict[str, Any]] = Field(default=None, description="Discovered API URLs for direct re-querying")
    scrape_method: str = Field(default="browser", description="browser or api_refresh")
    raw_event_data: Optional[Dict[str, Any]] = Field(default=None, description="Raw event API response for re-processing")

    # Errors/warnings
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def model_post_init(self, __context):
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


class TixrEventSummary(BaseModel):
    """Event summary from catalog discovery"""
    event_id: int = Field(..., description="Tixr event ID")
    event_name: str
    group_id: int
    group_name: str
    group_subdomain: str
    venue_name: Optional[str] = None
    venue_city: Optional[str] = None
    venue_state: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    event_url: str
    status: Optional[str] = None
    event_category: Optional[str] = None
    has_seatsio: bool = False
    sales_count: int = 0
    is_sold_out: bool = False
