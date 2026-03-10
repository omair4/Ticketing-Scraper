"""
Orchestration-specific data models
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EventStateRecord(BaseModel):
    """Database record for event scraping state"""
    event_id: str
    event_url: str
    event_name: str
    group_name: str = ""
    has_seatsio: bool = False
    is_sold_out: bool = False

    # Scraping state
    status: str = Field(..., description="pending, in_progress, completed, failed")
    scenario_type: Optional[str] = None
    scraped_at: Optional[datetime] = None
    output_json_path: Optional[str] = None

    # Error tracking
    error_message: Optional[str] = None
    retries: int = 0

    # Timestamps
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ScrapingStatistics(BaseModel):
    """Summary statistics for bulk scraping progress"""
    total_events: int
    pending: int
    in_progress: int
    completed: int
    failed: int

    # Scenario breakdown
    seatsio_count: int = 0
    general_admission_count: int = 0
    unknown_count: int = 0

    # Filtering stats
    sold_out_count: int = 0

    @property
    def completion_rate(self) -> float:
        if self.total_events == 0:
            return 0.0
        return (self.completed / self.total_events) * 100

    @property
    def failure_rate(self) -> float:
        if self.total_events == 0:
            return 0.0
        return (self.failed / self.total_events) * 100
