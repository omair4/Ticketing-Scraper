"""
Scraper for events that redirect to external third-party ticket vendors
(e.g., axs.com, ticketmaster.com).
"""

from typing import Optional
import structlog

from .base import BaseScraper
from models import ScrapeResult, EventInfo
from browser import CapturedResponse
from utils.decoder import extract_event_id_from_url

logger = structlog.get_logger()


class ExternalRedirectScraper(BaseScraper):
    """
    Handles events where clicking Buy Now redirects to an external
    third-party ticket vendor instead of the AdmitONE checkout flow.
    """

    def get_scenario_name(self) -> str:
        return 'external_redirect'

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
        external_redirect_url: str = "",
        initial_page_html: str = "",
    ) -> ScrapeResult:
        """
        Build a ScrapeResult for an external redirect event.

        Uses initial_page_html (the AdmitONE event page before the redirect)
        to extract event metadata, since the final page is a third-party site.
        """
        # Try to get event info from override or HTML
        if event_info_override:
            event_info = event_info_override
        else:
            event_id = extract_event_id_from_url(event_url) or "unknown"
            html_for_info = initial_page_html or page_html
            parsed = self._extract_event_info_from_html(html_for_info)

            if parsed:
                event_info = EventInfo(
                    event_id=event_id,
                    event_name=parsed.get('event_name') or 'Unknown',
                    venue_name=parsed.get('venue_name') or 'Unknown',
                    venue_address=parsed.get('venue_address'),
                    event_date=parsed.get('event_date'),
                    event_url=event_url,
                )
            else:
                event_info = EventInfo(
                    event_id=event_id,
                    event_name="Unknown",
                    venue_name="Unknown",
                    event_url=event_url,
                )

        self.add_warning(
            f"Event redirects to external ticket vendor: {external_redirect_url}"
        )

        logger.info(
            "external_redirect_result",
            event_name=event_info.event_name,
            external_url=external_redirect_url,
        )

        return ScrapeResult(
            event=event_info,
            scenario_type=self.get_scenario_name(),
            external_redirect_url=external_redirect_url,
            warnings=self.warnings,
            errors=self.errors,
        )
