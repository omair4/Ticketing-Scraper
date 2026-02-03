"""
Pydantic models for event discovery data
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import datetime
from dateutil import parser as date_parser


class VenueInfo(BaseModel):
    """Venue information"""

    venue_id: str = Field(..., description="Venue ID")
    name: str = Field(..., description="Venue name")
    address: Optional[str] = Field(default=None, description="Street address")
    city: str = Field(..., description="City name")

    @field_validator("name", "address", "city", "venue_id", mode="before")
    @classmethod
    def clean_string(cls, v):
        """Clean and normalize string fields"""
        if v is None:
            return ""
        return str(v).strip()


class EventSummary(BaseModel):
    """Event listing data (from search/list pages)"""

    event_id: str = Field(..., description="Event ID")
    name: str = Field(..., description="Event name")
    artist_name: Optional[str] = Field(default=None, description="Artist/performer name")
    subtitle: Optional[str] = Field(default=None, description="Event subtitle/tour name")
    date: Optional[datetime] = Field(default=None, description="Event date/time")
    venue_name: str = Field(..., description="Venue name")
    city: str = Field(..., description="City slug or name")
    category: str = Field(default="", description="Event category")
    event_type: Literal["pro", "community"] = Field(..., description="Pro or community event")
    url: str = Field(..., description="Event page URL path or full URL")
    image_url: Optional[str] = Field(default=None, description="Event image URL")
    price_range: Optional[str] = Field(default=None, description="Price range e.g. '$5.00 – $99.95' or 'Free'")

    @field_validator("name", "artist_name", "subtitle", "venue_name", "city", "category", "url", mode="before")
    @classmethod
    def clean_string(cls, v):
        """Clean and normalize string fields"""
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("date", mode="before")
    @classmethod
    def parse_date(cls, v):
        """Parse date from various formats"""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return date_parser.parse(v)
            except (ValueError, TypeError):
                return None
        return None


class EventDetail(BaseModel):
    """Full event details"""

    event_id: str = Field(..., description="Event ID")
    name: str = Field(..., description="Event name")
    artist_name: Optional[str] = Field(default=None, description="Artist/performer name")
    subtitle: Optional[str] = Field(default=None, description="Event subtitle/tour name")
    date: Optional[datetime] = Field(default=None, description="Event date/time")
    doors_time: Optional[str] = Field(default=None, description="Doors open time")
    show_time: Optional[str] = Field(default=None, description="Show start time")
    age_restriction: Optional[str] = Field(default=None, description="Age restriction e.g. 'All Ages'")
    venue: VenueInfo = Field(..., description="Venue information")
    address: str = Field(default="", description="Event/venue address")
    city: str = Field(..., description="City")
    category: str = Field(default="", description="Event category")
    event_type: Literal["pro", "community"] = Field(..., description="Pro or community event")
    description: Optional[str] = Field(default=None, description="Event description")
    url: str = Field(..., description="Event page URL")
    image_url: Optional[str] = Field(default=None, description="Event image URL")
    presenter: Optional[str] = Field(default=None, description="Presenter e.g. 'MRG Live presents'")


class EventsScrapeResult(BaseModel):
    """Complete events scrape result"""

    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    total_events: int = Field(default=0, description="Total events found")
    pro_events: int = Field(default=0, description="Count of pro events")
    community_events: int = Field(default=0, description="Count of community events")
    cities_covered: List[str] = Field(default_factory=list, description="Cities scraped")
    events: List[EventSummary] = Field(default_factory=list, description="List of events")
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def add_error(self, error: str):
        """Add an error message"""
        self.errors.append(error)

    def add_warning(self, warning: str):
        """Add a warning message"""
        self.warnings.append(warning)
