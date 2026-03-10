"""
Group → Events crawler for Tixr.

Navigates to each group page via Playwright, intercepts the
/api/groups/{id}/events response to enumerate all events.
"""

import json
import asyncio
from typing import List, Optional
import structlog

from browser import BrowserAutomation, CapturedResponse
from config import ScraperConfig
from models import TixrEventSummary

logger = structlog.get_logger()


class GroupCrawler:
    """
    Crawls Tixr group pages to discover their events.

    Must use Playwright (not direct HTTP) because DataDome blocks
    API requests outside of a browser navigation context.
    """

    def __init__(self, browser: BrowserAutomation, config: ScraperConfig):
        self.browser = browser
        self.config = config

    async def crawl_group(self, group_slug: str) -> List[TixrEventSummary]:
        """
        Navigate to a group page and extract its events.

        Flow:
        1. Navigate to https://www.tixr.com/groups/{slug}
        2. Intercept /api/page/requirements → extract groupId
        3. Intercept /api/groups/{groupId}/events → event list
        4. Parse events into TixrEventSummary models
        """
        group_url = f"https://www.tixr.com/groups/{group_slug}"
        logger.info("crawling_group", slug=group_slug, url=group_url)

        nav_result = await self.browser.navigate_to_group(group_url)

        if not nav_result.success:
            logger.warning("group_navigation_failed", slug=group_slug, error=nav_result.error)
            return []

        # Extract group info and events from captured responses
        group_id = self._extract_group_id(nav_result.captured_responses)
        events_data = self._extract_events_data(nav_result.captured_responses)

        if not events_data:
            logger.warning("no_events_data_captured", slug=group_slug)
            return []

        # Parse events
        events = self._parse_events(events_data, group_slug, group_id)
        logger.info("group_events_found", slug=group_slug, count=len(events))

        return events

    async def crawl_groups(self, group_slugs: List[str],
                           max_groups: Optional[int] = None) -> List[TixrEventSummary]:
        """
        Crawl multiple groups sequentially with delays.

        Args:
            group_slugs: List of group slugs to crawl
            max_groups: Maximum groups to crawl (None = all)

        Returns:
            Deduplicated list of all events
        """
        slugs = group_slugs[:max_groups] if max_groups else group_slugs
        all_events = []
        seen_event_ids = set()

        for i, slug in enumerate(slugs, 1):
            logger.info("crawling_group_progress", current=i, total=len(slugs), slug=slug)

            try:
                events = await self.crawl_group(slug)
                for event in events:
                    if event.event_id not in seen_event_ids:
                        seen_event_ids.add(event.event_id)
                        all_events.append(event)
            except Exception as e:
                logger.error("group_crawl_failed", slug=slug, error=str(e))

            # Delay between groups to avoid DataDome
            if i < len(slugs):
                await asyncio.sleep(self.config.inter_group_delay)

        logger.info("all_groups_crawled",
                    groups=len(slugs),
                    total_events=len(all_events),
                    unique_events=len(seen_event_ids))

        return all_events

    def _extract_group_id(self, responses: list[CapturedResponse]) -> Optional[int]:
        """Extract group ID from /api/page/requirements response."""
        for resp in responses:
            if '/api/page/requirements' in resp.url and resp.body:
                try:
                    data = json.loads(resp.body)
                    # Look for group ID in requirements data
                    if isinstance(data, dict):
                        group_id = data.get('groupId') or data.get('group', {}).get('id')
                        if group_id:
                            return int(group_id)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

        # Try to extract from /api/groups/ URLs
        for resp in responses:
            if '/api/groups/' in resp.url:
                import re
                match = re.search(r'/api/groups/(\d+)', resp.url)
                if match:
                    return int(match.group(1))

        return None

    def _extract_events_data(self, responses: list[CapturedResponse]) -> Optional[list]:
        """Extract events array from /api/groups/{id}/events response."""
        for resp in responses:
            if '/api/groups/' in resp.url and '/events' in resp.url and resp.body:
                try:
                    data = json.loads(resp.body)
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        # May be wrapped in a response object
                        events = data.get('events') or data.get('data') or data.get('results')
                        if isinstance(events, list):
                            return events
                except (json.JSONDecodeError, ValueError):
                    pass

        return None

    def _parse_events(self, events_data: list, group_slug: str,
                      group_id: Optional[int]) -> List[TixrEventSummary]:
        """Parse raw event data into TixrEventSummary models."""
        events = []

        for event_data in events_data:
            try:
                event_id = event_data.get('id')
                if not event_id:
                    continue

                venue = event_data.get('venue', {}) or {}
                address = venue.get('address', {}) or {}
                group = event_data.get('group', {}) or {}

                # Build event URL
                event_name_slug = event_data.get('slug', '') or event_data.get('name', '').lower().replace(' ', '-')
                event_url = f"https://www.tixr.com/groups/{group_slug}/events/{event_name_slug}-{event_id}"

                # Check seatsio
                has_seatsio = event_data.get('seatsIoConfiguration') is not None

                # Check sales
                sales = event_data.get('sales', []) or []
                is_sold_out = all(
                    s.get('state') in ('SOLD_OUT', 'HIDE', 'CLOSED')
                    for s in sales
                ) if sales else False

                events.append(TixrEventSummary(
                    event_id=event_id,
                    event_name=event_data.get('name', 'Unknown Event'),
                    group_id=group_id or group.get('id', 0),
                    group_name=group.get('name', group_slug),
                    group_subdomain=group.get('subdomain', group_slug),
                    venue_name=venue.get('name'),
                    venue_city=address.get('city'),
                    venue_state=address.get('state'),
                    start_date=event_data.get('startDate'),
                    end_date=event_data.get('endDate'),
                    event_url=event_url,
                    status=event_data.get('status'),
                    event_category=event_data.get('eventCategoryName'),
                    has_seatsio=has_seatsio,
                    sales_count=len(sales),
                    is_sold_out=is_sold_out,
                ))
            except Exception as e:
                logger.warning("event_parse_failed", error=str(e),
                              event_id=event_data.get('id'))

        return events
