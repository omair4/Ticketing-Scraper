"""
GraphQL-based event discovery scraper
"""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from config import ScraperConfig, DEFAULT_CONFIG
from models import EventSummary, EventsScrapeResult
from discovery.graphql_client import GraphQLClient
from discovery.cities import (
    KNOWN_CITIES,
    CITY_SLUG_TO_EVENT_CITY,
    CANADA_CENTER_LAT,
    CANADA_CENTER_LON,
    CANADA_RADIUS_KM,
    PAGE_SIZE,
)

logger = structlog.get_logger()

GRAPHQL_QUERIES_FILE = Path(__file__).parent / "graphql_queries.json"

# Operations we use for event discovery (skip OrganizerEvents, GetOrganizerPublicProfile)
EVENT_OPERATIONS = ("PublicProEvents", "Events")


def _slugify(s: str) -> str:
    """Lowercase, strip accents, replace non-alphanumeric with hyphens, collapse and strip."""
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    normalized = unicodedata.normalize("NFKD", s)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower())
    return slug.strip("-")


def _date_range() -> Dict[str, str]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=365)
    return {
        "from": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


class GraphQLEventsScraper:
    """
    Scraper that fetches events via GraphQL API.
    Supports Canada-wide mode (single radius query) or per-city mode.
    """

    def __init__(self, config: ScraperConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.client = GraphQLClient(config=self.config)
        self._discovered_queries: Optional[Dict[str, Dict]] = None

    def _load_discovered_queries(self) -> Dict[str, Dict]:
        """Load PublicProEvents and Events queries from discovery file."""
        if self._discovered_queries is not None:
            return self._discovered_queries
        result: Dict[str, Dict] = {}
        if not GRAPHQL_QUERIES_FILE.exists():
            self._discovered_queries = result
            return result
        try:
            with open(GRAPHQL_QUERIES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            requests = data.get("requests", []) or []
            for r in requests:
                op = r.get("operationName")
                if op in EVENT_OPERATIONS and r.get("query"):
                    result[op] = r
            self._discovered_queries = result
            logger.info("graphql_queries_loaded", operations=list(result.keys()))
            return result
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("graphql_queries_load_failed", error=str(e))
            self._discovered_queries = {}
            return {}

    async def fetch_events(
        self,
        cities: Optional[List[str]] = None,
        event_type: Optional[str] = None,
        canada_wide: bool = True,
    ) -> EventsScrapeResult:
        """
        Fetch events via GraphQL API.

        Args:
            cities: List of city slugs (for per-city mode)
            event_type: 'pro', 'community', or None for both
            canada_wide: If True, use single Canada-wide radius query; else per-city

        Returns:
            EventsScrapeResult with discovered events
        """
        queries = self._load_discovered_queries()
        if not queries:
            result = EventsScrapeResult(cities_covered=[])
            result.add_warning("GraphQL schema not discovered. Run: python discovery/discover_graphql.py")
            return result

        cities = cities or KNOWN_CITIES
        result = EventsScrapeResult(cities_covered=cities.copy())
        seen_ids: set[str] = set()

        if canada_wide:
            await self._fetch_canada_wide(queries, event_type, result, seen_ids)
        else:
            await self._fetch_per_city(queries, cities, event_type, result, seen_ids)

        result.total_events = len(result.events)
        result.pro_events = sum(1 for e in result.events if e.event_type == "pro")
        result.community_events = sum(1 for e in result.events if e.event_type == "community")

        logger.info(
            "graphql_fetch_complete",
            total=result.total_events,
            pro=result.pro_events,
            community=result.community_events,
        )
        return result

    async def _fetch_canada_wide(
        self,
        queries: Dict[str, Dict],
        event_type: Optional[str],
        result: EventsScrapeResult,
        seen_ids: set[str],
    ) -> None:
        """Single Canada-wide query with pagination for both PublicProEvents and Events."""
        dr = _date_range()
        location = {"latitude": CANADA_CENTER_LAT, "longitude": CANADA_CENTER_LON}

        # PublicProEvents
        if "PublicProEvents" in queries and event_type in (None, "pro"):
            q = queries["PublicProEvents"]
            vars_base = {
                "location": location,
                "distance": CANADA_RADIUS_KM,
                "excludeStatus": ["Cancelled"],
                "startDateRange": dr,
                "take": PAGE_SIZE,
            }
            async for item, ev_type in self._paginate(q, vars_base, "publicProEvents", "pro"):
                e = self._parse_event_item(item, city="", event_type=ev_type)
                if e and e.mongo_event_id not in seen_ids:
                    seen_ids.add(e.mongo_event_id)
                    result.events.append(e)

        # Events (community)
        if "Events" in queries and event_type in (None, "community"):
            q = queries["Events"]
            vars_base = {
                "location": location,
                "distance": CANADA_RADIUS_KM,
                "orderBy": {"field": "start_date", "sort": 1},
                "startDateRange": dr,
                "take": PAGE_SIZE,
            }
            async for item, ev_type in self._paginate(q, vars_base, "events", "community"):
                e = self._parse_event_item(item, city="", event_type=ev_type)
                if e and e.mongo_event_id not in seen_ids:
                    seen_ids.add(e.mongo_event_id)
                    result.events.append(e)

    async def _fetch_per_city(
        self,
        queries: Dict[str, Dict],
        cities: List[str],
        event_type: Optional[str],
        result: EventsScrapeResult,
        seen_ids: set[str],
    ) -> None:
        """Per-city queries with pagination."""
        dr = _date_range()
        event_city_map = CITY_SLUG_TO_EVENT_CITY

        for city_slug in cities:
            event_city = event_city_map.get(city_slug, city_slug.title().replace("-", " "))

            # PublicProEvents: use location + distance
            if "PublicProEvents" in queries and event_type in (None, "pro"):
                q = queries["PublicProEvents"]
                vars_base = {
                    "city": event_city,
                    "location": {"latitude": 43.65, "longitude": -79.38},
                    "distance": 75,
                    "excludeStatus": ["Cancelled"],
                    "startDateRange": dr,
                    "take": PAGE_SIZE,
                }
                async for item, ev_type in self._paginate(q, vars_base, "publicProEvents", "pro"):
                    e = self._parse_event_item(item, city=city_slug, event_type=ev_type)
                    if e and e.mongo_event_id not in seen_ids:
                        seen_ids.add(e.mongo_event_id)
                        result.events.append(e)

            # Events: use city (EventCity enum)
            if "Events" in queries and event_type in (None, "community"):
                q = queries["Events"]
                vars_base = {
                    "city": event_city,
                    "orderBy": {"field": "start_date", "sort": 1},
                    "startDateRange": dr,
                    "take": PAGE_SIZE,
                }
                async for item, ev_type in self._paginate(q, vars_base, "events", "community"):
                    e = self._parse_event_item(item, city=city_slug, event_type=ev_type)
                    if e and e.mongo_event_id not in seen_ids:
                        seen_ids.add(e.mongo_event_id)
                        result.events.append(e)

    async def _paginate(
        self,
        query_def: Dict,
        vars_base: Dict[str, Any],
        data_key: str,
        event_type: str,
    ):
        """Paginate through GraphQL results. Yields (item, event_type) tuples."""
        op = query_def.get("operationName", "")
        query_str = query_def.get("query", "")
        skip = 0
        while True:
            vars_page = {**vars_base, "skip": skip}
            data = await self.client.query(op, query_str, vars_page)
            if not data:
                break
            blob = data.get(data_key)
            if not blob:
                break
            items = blob.get("items") or []
            total = blob.get("total") or 0
            for item in items:
                if isinstance(item, dict):
                    yield (item, event_type)
            if not items or skip + len(items) >= total:
                break
            skip += len(items)
            logger.debug("graphql_paginate", op=op, skip=skip, total=total)

    def _build_community_url(
        self,
        city_val: str,
        category: str,
        title: str,
        short_event_id: str,
    ) -> str:
        """Build full community event URL: /events/{city}/community/{category}/{title-slug}/{shortEventId}."""
        city_slug = _slugify(city_val) or "other"
        category_slug = _slugify(category) or "other"
        title_slug = _slugify(title) or "other"
        path = f"/events/{city_slug}/community/{category_slug}/{title_slug}/{short_event_id}"
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _build_pro_url(
        self,
        venue_city: str,
        category: str,
        venue_name: str,
        artist_name: str,
        mongo_event_id: str,
    ) -> str:
        """Build full pro event URL: /events/{city}/pro/{category|other}/{venue}/{artist}/{artist}/{eventId}."""
        city_slug = _slugify(venue_city) or "other"
        category_segment = _slugify(category).strip() if (category or "").strip() else "other"
        if not category_segment:
            category_segment = "other"
        venue_slug = _slugify(venue_name) or "unknown"
        artist_slug = _slugify(artist_name or "") or "unknown"
        path = f"/events/{city_slug}/pro/{category_segment}/{venue_slug}/{artist_slug}/{artist_slug}/{mongo_event_id}"
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _parse_event_item(self, item: Dict[str, Any], city: str, event_type: str) -> Optional[EventSummary]:
        """Parse a single event item into EventSummary."""
        mongo_event_id = item.get("id") or item.get("_id") or ""
        if not mongo_event_id:
            return None

        # event_id: pro = showclixId, community = shortEventId; fallback to mongo_event_id
        if event_type == "pro":
            showclix = item.get("showclixId")
            event_id = str(showclix) if showclix is not None else str(mongo_event_id)
        else:
            short_id = item.get("shortEventId")
            event_id = str(short_id) if short_id is not None else str(mongo_event_id)

        title = item.get("title") or item.get("name") or ""
        if not title:
            return None

        # Venue: Pro has venue.{name,city}, Community has address.{formattedAddress,city}
        venue_data = item.get("venue") or item.get("address") or {}
        if isinstance(venue_data, dict):
            venue_name = (
                venue_data.get("businessName")
                or venue_data.get("name")
                or venue_data.get("formattedAddress")
                or ""
            )
            city_from_item = venue_data.get("city") or ""
        else:
            venue_name = str(venue_data)
            city_from_item = ""

        addr = item.get("address")
        if isinstance(addr, dict):
            city_from_item = city_from_item or addr.get("city") or ""
        city_val = city or city_from_item or ""

        # Date: Pro uses eventStart/eventDate, Community uses startDate
        start_date = item.get("startDate") or item.get("eventStart") or item.get("date") or item.get("eventDate")
        date_val = None
        if start_date:
            try:
                from dateutil import parser as date_parser

                date_val = date_parser.parse(start_date) if isinstance(start_date, str) else start_date
            except Exception:
                pass

        # Artist: Pro has artists[0].title
        artist_name = item.get("artistName")
        if not artist_name and isinstance(item.get("artists"), list) and item["artists"]:
            first = item["artists"][0]
            artist_name = first.get("title") or first.get("name") if isinstance(first, dict) else None
        if not artist_name and isinstance(item.get("artist"), dict):
            artist_name = item.get("artist", {}).get("name")

        image_url = item.get("imageUrl") or item.get("eventImage")
        if not image_url and isinstance(item.get("coverImage"), dict):
            image_url = item.get("coverImage", {}).get("url")
        elif not image_url:
            image_url = item.get("coverImage") if isinstance(item.get("coverImage"), str) else None

        # Price: feesRange.min/max or ticketRange
        price_range = item.get("ticketRange") or item.get("priceRange") or item.get("priceSummary")
        if not price_range and isinstance(item.get("feesRange"), dict):
            fr = item["feesRange"]
            mn, mx = fr.get("min"), fr.get("max")
            if mn is not None or mx is not None:
                price_range = f"${mn or 0:.2f} – ${mx or 0:.2f}" if mx else f"${mn or 0:.2f}+"

        # URL: build AdmitOne-style full URL (community vs pro)
        category = item.get("category") or item.get("categorySlug") or ""
        if event_type == "community":
            url = self._build_community_url(city_val, category, title, event_id)
        else:
            venue_city = (item.get("venue") or {}).get("city") or city_val
            primary_artist = artist_name or item.get("admitoneTitle") or ""
            url = self._build_pro_url(
                venue_city,
                category,
                venue_name or "Unknown",
                primary_artist,
                str(mongo_event_id),
            )

        return EventSummary(
            mongo_event_id=str(mongo_event_id),
            event_id=event_id,
            name=title,
            artist_name=artist_name,
            subtitle=item.get("subtitle") or item.get("tourName"),
            date=date_val,
            venue_name=venue_name or "Unknown",
            city=city_val or "Unknown",
            category=category,
            event_type=event_type,
            url=url,
            image_url=image_url,
            price_range=price_range,
        )
