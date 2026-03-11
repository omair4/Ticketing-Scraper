"""
Scraper for PreKindle Scenario 3: SVG Seatmap (NOT SUPPORTED)

This scenario uses server-rendered SVG overlays on base64-encoded PNG background images.
Seat-level data requires clicking each section and parsing Wicket AJAX responses.
This is detected but returns an unsupported result.
"""

from typing import Optional
import structlog

from scrapers.base import BaseScraper
from models import ScrapeResult, EventInfo
from browser import CapturedResponse

logger = structlog.get_logger()


class SvgSeatmapScraper(BaseScraper):
    """Placeholder scraper for SVG seatmap events (not supported)."""

    def get_scenario_name(self) -> str:
        return "svg_seatmap"

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        """Return unsupported result for SVG seatmap events."""

        logger.info("svg_seatmap_detected_not_supported")

        event_info = event_info_override or EventInfo(
            event_id=self._extract_event_id(event_url),
            event_name="Unknown Event",
            venue_name="Unknown Venue",
            event_url=event_url,
        )

        self.add_error(
            "SVG seatmap scenario not supported. "
            "This event uses server-rendered SVG overlays with Wicket AJAX interactions. "
            "Seat-level data requires clicking each section individually."
        )

        return self.create_result(event_info)
