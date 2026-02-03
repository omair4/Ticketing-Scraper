"""
Browser-based event discovery (fallback when direct GraphQL fails)
"""

import re
from typing import List, Optional

from bs4 import BeautifulSoup
import structlog

from config import ScraperConfig, DEFAULT_CONFIG
from models import EventSummary, EventsScrapeResult
from browser import BrowserAutomation, CapturedResponse
from discovery.graphql_scraper import GraphQLEventsScraper
from discovery.cities import KNOWN_CITIES

logger = structlog.get_logger()


class BrowserEventsScraper:
    """
    Scraper that discovers events by navigating pages and capturing GraphQL responses.
    Used when direct GraphQL queries are not available or for verification.
    """

    def __init__(self, config: ScraperConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.graphql_parser = GraphQLEventsScraper(config=self.config)

    async def fetch_events(
        self,
        cities: Optional[List[str]] = None,
        event_type: Optional[str] = None,
    ) -> EventsScrapeResult:
        """
        Fetch events by navigating to city pages and capturing GraphQL responses.

        Args:
            cities: List of city slugs (default: KNOWN_CITIES)
            event_type: 'pro', 'community', or None for both

        Returns:
            EventsScrapeResult with discovered events
        """
        cities = cities or KNOWN_CITIES
        result = EventsScrapeResult(cities_covered=cities.copy())
        seen_ids: set[str] = set()

        async with BrowserAutomation(self.config) as browser:
            for city in cities:
                logger.info("scraping_city", city=city)

                # Navigate to city events page
                url = f"/events/{city}"
                nav_result = await browser.navigate_to_page(url)

                if not nav_result.success:
                    result.add_error(f"Failed to navigate to {city}: {nav_result.error}")
                    continue

                # Parse GraphQL responses
                for resp in nav_result.captured_responses:
                    if "graphql.admitone.com" in resp.url and resp.body and resp.is_json():
                        events_pro = self.graphql_parser.parse_events_from_response(
                            resp.body, city=city, event_type="pro"
                        )
                        events_community = self.graphql_parser.parse_events_from_response(
                            resp.body, city=city, event_type="community"
                        )

                        for e in events_pro + events_community:
                            if event_type and e.event_type != event_type:
                                continue
                            if e.event_id and e.event_id not in seen_ids:
                                seen_ids.add(e.event_id)
                                result.events.append(e)

                # Also parse HTML for event links (fallback)
                html_events = self._parse_events_from_html(nav_result.page_html, city)
                for e in html_events:
                    if event_type and e.event_type != event_type:
                        continue
                    if e.event_id and e.event_id not in seen_ids:
                        seen_ids.add(e.event_id)
                        result.events.append(e)

        # Compute stats
        result.total_events = len(result.events)
        result.pro_events = sum(1 for e in result.events if e.event_type == "pro")
        result.community_events = sum(1 for e in result.events if e.event_type == "community")

        logger.info(
            "browser_scrape_complete",
            total=result.total_events,
            pro=result.pro_events,
            community=result.community_events,
        )
        return result

    def _parse_events_from_html(self, html: str, city: str) -> List[EventSummary]:
        """
        Parse event links from page HTML as fallback.
        Extracts event cards with links to /events/{city}/pro/... or /events/{city}/community/...
        """
        events: List[EventSummary] = []

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Find all links to event pages
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                if not href or "events" not in href:
                    continue

                # Match /events/{city}/pro/... or /events/{city}/community/...
                # Pro: /events/{city}/pro/{cat}/{venue}/{artist}/{name}/{id}
                # Community: /events/{city}/community/{cat}/{slug}/{id}
                match = re.search(
                    r"/events/[^/]+/(pro|community)/[^/]+/[^/]+/[^/]+/([a-f0-9]{24}|[A-Za-z0-9]{6,})",
                    href,
                )
                if not match:
                    match = re.search(
                        r"/events/[^/]+/(pro|community)/[^/]+/([a-f0-9]{24}|[A-Za-z0-9]{6,})",
                        href,
                    )
                if match:
                    ev_type = match.group(1)
                    event_id = match.group(2)

                    # Try to get event name from link text
                    name = link.get_text(strip=True) or "Unknown"
                    # Clean name (remove "Get tickets" etc.)
                    for suffix in ["Get tickets", "Get Tickets", "_cover"]:
                        if suffix in name:
                            name = name.replace(suffix, "").strip()

                    # Try to find venue from nearby elements
                    venue_name = "Unknown"
                    parent = link.find_parent()
                    if parent:
                        for p in parent.find_all("p"):
                            text = p.get_text(strip=True)
                            if text and "p.m." not in text and "a.m." not in text:
                                # Likely venue (e.g. "The Concert Hall, Toronto")
                                if "," in text:
                                    venue_name = text.split(",")[0].strip()
                                else:
                                    venue_name = text
                                break

                    events.append(
                        EventSummary(
                            event_id=event_id,
                            name=name[:200] if name else "Unknown",
                            artist_name=None,
                            subtitle=None,
                            date=None,
                            venue_name=venue_name,
                            city=city,
                            category="",
                            event_type=ev_type,
                            url=href,
                            image_url=None,
                            price_range=None,
                        )
                    )

        except Exception as e:
            logger.warning("html_parse_failed", error=str(e))

        return events
