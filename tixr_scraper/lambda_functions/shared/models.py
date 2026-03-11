from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class DiscoveryBatch(BaseModel):
    """Single discovery unit for Step Functions.

    For now we batch by sitemap URL (one group-sitemap-*.xml per batch).
    """

    sitemap_url: str = Field(..., description="URL of a single group-sitemap-*.xml file")


class EventRecord(BaseModel):
    """Minimal event record emitted by discovery."""

    event_id: int
    event_name: str
    group_slug: str
    group_id: int
    ticket_url: str
    venue_name: Optional[str] = None
    start_date: Optional[int] = None
    end_date: Optional[int] = None


ScraperType = Literal["open_seating", "seatsio", "sold_out"]


class ScraperConfig(BaseModel):
    """Configuration + raw data produced by determine_scraper."""

    event_id: int
    scraper_type: ScraperType
    workspace_key: Optional[str] = None
    event_key: Optional[str] = None
    chart_key: Optional[str] = None
    pricing_list: Optional[list[dict[str, Any]]] = None
    raw_event_data: dict[str, Any]
    event_meta: dict[str, Any] = Field(default_factory=dict)


class InventoryResult(BaseModel):
    """Unified inventory response from tixr_event_inventory."""

    event_id: int
    scraper_type: ScraperType
    seats: list[dict[str, Any]] = Field(default_factory=list)
    ticket_categories: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

