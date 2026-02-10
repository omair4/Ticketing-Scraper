"""
PreKindle Event Search API client

Uses the REST search API at web.prekindle.com to discover all events.
API: GET https://web.prekindle.com/api/event/search?page={page}&limit={limit}&timeZone=America%2FNew_York&startTime={iso_datetime}
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, urlencode

# Ensure parent package (prekindle_scraper/) is on sys.path for bare imports
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from config import ScraperConfig, DEFAULT_CONFIG
from models import PreKindleEventSummary

logger = structlog.get_logger()


class PreKindleEventSearch:
    """Async client for the PreKindle event search API."""

    def __init__(self, config: ScraperConfig = None):
        self.config = config or DEFAULT_CONFIG

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_page(self, page: int, limit: int, start_time: str) -> dict:
        """
        Fetch a single page of search results.

        Args:
            page: 0-indexed page number
            limit: Results per page
            start_time: ISO datetime string for filtering

        Returns:
            Raw API response dict
        """
        params = {
            'page': page,
            'limit': limit,
            'timeZone': self.config.timezone,
            'startTime': start_time,
        }

        headers = {
            'User-Agent': self.config.user_agent,
            'Accept': 'application/json',
        }

        url = f"{self.config.search_api_url}?{urlencode(params, quote_via=quote)}"

        async with httpx.AsyncClient(timeout=self.config.request_timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            logger.debug("search_page_fetched",
                          page=page,
                          events_count=len(data.get('searchedEvents', [])),
                          total_pages=data.get('totalPages', 0))

            return data

    async def fetch_all_events(
        self,
        limit: int = None,
        max_pages: Optional[int] = None,
        start_page: int = 0,
    ) -> List[PreKindleEventSummary]:
        """
        Fetch all events by paginating through the search API.

        Args:
            limit: Results per page (default from config)
            max_pages: Maximum pages to fetch (None = all)
            start_page: Starting page (0-indexed)

        Returns:
            List of PreKindleEventSummary objects
        """
        limit = limit or self.config.search_limit

        # Build startTime from current time in configured timezone
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(self.config.timezone)
            now = datetime.now(tz)
        except ImportError:
            import pytz
            tz = pytz.timezone(self.config.timezone)
            now = datetime.now(tz)

        start_time = now.strftime('%Y-%m-%dT%H:%M:%S.000%z')
        # Format offset: +0000 → +00:00
        start_time = start_time[:-2] + ':' + start_time[-2:]

        logger.info("fetching_all_events", start_time=start_time, limit=limit)

        all_events = []
        page = start_page
        total_pages = None

        while True:
            if max_pages is not None and (page - start_page) >= max_pages:
                logger.info("max_pages_reached", max_pages=max_pages)
                break

            try:
                data = await self._fetch_page(page, limit, start_time)
            except httpx.HTTPStatusError as e:
                logger.error("search_api_error", page=page, status=e.response.status_code)
                break
            except Exception as e:
                logger.error("search_api_failed", page=page, error=str(e))
                break

            if total_pages is None:
                total_pages = data.get('totalPages', 0)
                logger.info("search_total_pages", total_pages=total_pages)

            searched_events = data.get('searchedEvents', [])
            if not searched_events:
                break

            for event_data in searched_events:
                summary = self._parse_event(event_data)
                if summary:
                    all_events.append(summary)

            logger.info("search_page_processed",
                          page=page,
                          page_events=len(searched_events),
                          total_so_far=len(all_events))

            page += 1
            if total_pages is not None and page >= total_pages:
                break

        logger.info("fetch_complete", total_events=len(all_events))
        return all_events

    def _parse_event(self, event_data: dict) -> Optional[PreKindleEventSummary]:
        """Parse a single event from the search API response."""
        try:
            # Extract event ID from ctaButtonProps.href
            event_id = ''
            cta_props = event_data.get('ctaButtonProps', {})
            ticket_url = cta_props.get('href', '')
            is_sold_out = cta_props.get('disabled', False)

            if ticket_url:
                id_match = re.search(r'/(?:promo|checkout|find-tickets)/id/(-?\d+)', ticket_url)
                if id_match:
                    event_id = id_match.group(1)

            if not event_id:
                # Try from other fields
                event_id = str(event_data.get('id', ''))

            # Performers
            performers = event_data.get('performers', [])

            return PreKindleEventSummary(
                event_id=event_id,
                title=event_data.get('title', ''),
                subtitle=event_data.get('subtitle'),
                organizer_name=event_data.get('organizerName'),
                performers=performers,
                start_time=event_data.get('startTime'),
                details_primary=event_data.get('detailsPrimary'),
                details_secondary=event_data.get('detailsSecondary'),
                ticket_url=ticket_url,
                thumbnail_url=event_data.get('thumbnailUrl'),
                is_sold_out=bool(is_sold_out),
            )

        except Exception as e:
            logger.warning("event_parse_failed", error=str(e), data=str(event_data)[:200])
            return None
