"""
Scraper for Tixr events using seats.io seat selection.

Data Sources:
1. Event API — /api/events/{id} → seatsIoConfiguration, sales[].tiers[] (pricing)
2. rendering-info (plain JSON) — categories[], objectCategories{}, category definitions
3. charts/published (hex-encoded) — visual layout, x/y coords, ADA flags, sections
4. object-statuses (hex-encoded) — seat availability status
"""

import json
import re
from typing import Optional, Dict, Any, List
from decimal import Decimal
import structlog

from scrapers.base import BaseScraper
from models import ScrapeResult, Seat, TicketCategory, EventInfo, SeatStatus
from browser import CapturedResponse
from utils.decoder import decode_seatsio_response, decode_charts_response

logger = structlog.get_logger()


class SeatsIOScraper(BaseScraper):
    """Scraper for Tixr events using seats.io seat selection."""

    def get_scenario_name(self) -> str:
        return "seatsio"

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_api_data: Optional[dict] = None,
    ) -> ScrapeResult:
        """Extract seats from seats.io API responses + pricing from Tixr event API."""

        logger.info("starting_seatsio_extraction", response_count=len(captured_responses))

        # Step 1: Get event info from API data
        if event_api_data:
            event_info = self.extract_event_info_from_api(event_api_data, event_url)
        else:
            event_info = EventInfo(
                event_id=self._extract_event_id_from_url(event_url),
                event_name="Unknown Event",
                venue_name="Unknown Venue",
                event_url=event_url,
            )

        # Step 2: Extract pricing from event API sales[].tiers[]
        pricing = self._extract_pricing_from_api(event_api_data) if event_api_data else {}
        if not pricing:
            self.add_warning("Could not extract pricing from event API")

        # Step 3: Extract rendering-info
        rendering_info = self._extract_rendering_info(captured_responses)
        if not rendering_info:
            self.add_warning("No rendering-info found — category assignment may be incomplete")

        # Step 4: Extract chart data
        chart_data = self._extract_chart_data(captured_responses, rendering_info)

        # Step 5: Extract availability
        availability = self._extract_availability(captured_responses)

        # Step 6: Build seats
        seats = []
        ticket_categories = []

        if chart_data:
            seats, ticket_categories = self._build_seats(
                chart_data, availability, pricing, rendering_info, captured_responses
            )
            if not seats and availability:
                seats = self._build_seats_from_object_statuses(
                    captured_responses, pricing,
                    chart_data.get('category_map', {}),
                    rendering_info,
                )
                if seats and ticket_categories:
                    self._recalculate_ticket_category_stock(seats, ticket_categories)
        else:
            self.add_warning("No chart data found — trying fallback from availability data")
            seats = self._build_seats_from_availability(availability, pricing)

        if not seats and not ticket_categories:
            self.add_error("No seats or categories extracted")

        # Build API URLs for re-querying
        api_urls = self._build_api_urls(captured_responses, event_api_data)

        logger.info("seatsio_extraction_complete",
                    seats=len(seats),
                    categories=len(ticket_categories))

        return self.create_result(
            event_info=event_info,
            seats=seats,
            ticket_categories=ticket_categories,
            api_urls=api_urls,
            raw_event_data=event_api_data,
        )

    def _extract_pricing_from_api(self, event_data: dict) -> Dict[str, Dict[str, Any]]:
        """
        Extract pricing from Tixr event API sales[].tiers[].

        Returns dict mapping category_label → {price, tier_name, sale_name}
        Uses sale name as the category label for matching against seats.io categories.
        """
        pricing = {}
        sales = event_data.get('sales', []) or []

        for sale in sales:
            sale_name = sale.get('name', '')
            sale_category = sale.get('category', '')
            tiers = sale.get('tiers', []) or []

            # Find active tier price
            active_tiers = [t for t in tiers if t.get('active')]
            if not active_tiers:
                active_tiers = tiers

            for tier in active_tiers:
                price = tier.get('price', 0)
                tier_name = tier.get('name', '')

                # Map by sale name (this is what seats.io categories typically match)
                if sale_name:
                    pricing[sale_name] = {
                        'price': Decimal(str(price)) if price else Decimal('0'),
                        'tier_name': tier_name,
                        'sale_name': sale_name,
                        'category': sale_category,
                    }

                # Also map by tier name if different
                if tier_name and tier_name != sale_name:
                    pricing[tier_name] = {
                        'price': Decimal(str(price)) if price else Decimal('0'),
                        'tier_name': tier_name,
                        'sale_name': sale_name,
                        'category': sale_category,
                    }

        logger.info("pricing_extracted_from_api", levels=len(pricing),
                    names=list(pricing.keys())[:10])
        return pricing

    def _extract_rendering_info(self, responses: list[CapturedResponse]) -> Optional[dict]:
        """Extract rendering-info from captured responses (plain JSON)."""
        for resp in responses:
            if 'rendering-info' in resp.url and resp.body:
                try:
                    data = json.loads(resp.body)
                    if isinstance(data, dict):
                        # Build category_by_key lookup
                        categories = data.get('categories', [])
                        category_by_key = {}
                        for cat in categories:
                            key = cat.get('key')
                            if key is not None:
                                category_by_key[key] = cat
                                category_by_key[str(key)] = cat
                        data['category_by_key'] = category_by_key

                        logger.info("rendering_info_extracted",
                                    categories=len(categories),
                                    object_categories=len(data.get('objectCategories', {})))
                        return data
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("rendering_info_parse_failed", error=str(e))
        return None

    def _extract_chart_data(self, responses: list[CapturedResponse],
                            rendering_info: Optional[dict] = None) -> Optional[dict]:
        """Extract chart structure from charts/published response (hex-encoded)."""
        chart_response = None
        for resp in responses:
            if 'charts' in resp.url and 'published' in resp.url and resp.body:
                chart_response = resp
                break

        if not chart_response:
            return None

        decoded = decode_charts_response(chart_response.body)
        if not decoded:
            self.add_warning("Failed to decode charts/published response")
            return None

        try:
            chart_json = json.loads(decoded)
        except json.JSONDecodeError:
            self.add_warning("Failed to parse decoded chart JSON")
            return None

        # Build category map from chart
        category_map = {}
        for cat in chart_json.get('categories', {}).get('list', []):
            key = cat.get('key')
            label = cat.get('label', '')
            if key is not None:
                category_map[str(key)] = label

        # Extract for-sale config
        excluded_objects = set()
        for_sale_configs = []
        if rendering_info:
            for_sale_configs = rendering_info.get('forSaleConfigsPerEvent', []) or []
        for config in for_sale_configs:
            if not config.get('forSale', True):
                for obj in config.get('objects', []):
                    excluded_objects.add(obj)

        # Extract seats from chart
        rows = []
        ga_areas = []
        table_areas = []
        sections = chart_json.get('sections', []) or []

        if sections:
            # Multi-section venue: sections[] with subChart.rows[]
            for section in sections:
                section_label = section.get('label', '')
                sub_chart = section.get('subChart', {})
                for row in sub_chart.get('rows', []):
                    row_data = dict(row)
                    row_data['_section'] = section_label
                    rows.append(row_data)
                for area in sub_chart.get('generalAdmissionAreas', []):
                    area_data = dict(area)
                    area_data['_section'] = section_label
                    ga_areas.append(area_data)
                for table in sub_chart.get('tables', []):
                    table_data = dict(table)
                    table_data['_section'] = section_label
                    table_areas.append(table_data)
        else:
            # Flat venue
            rows = chart_json.get('rows', []) or []
            ga_areas = chart_json.get('generalAdmissionAreas', []) or []
            table_areas = chart_json.get('tables', []) or []

        logger.info("chart_parsed",
                    rows=len(rows),
                    ga_areas=len(ga_areas),
                    tables=len(table_areas),
                    sections=len(sections),
                    categories=len(category_map),
                    excluded=len(excluded_objects))

        return {
            'rows': rows,
            'ga_areas': ga_areas,
            'table_areas': table_areas,
            'category_map': category_map,
            'excluded_objects': excluded_objects,
            'sections': sections,
            'raw': chart_json,
        }

    def _extract_availability(self, responses: list[CapturedResponse]) -> dict:
        """Extract availability from object-statuses response (hex-encoded)."""
        for resp in responses:
            if 'object-statuses' in resp.url and resp.body:
                decoded = decode_seatsio_response(resp.body)
                if decoded:
                    try:
                        data = json.loads(decoded)
                        if isinstance(data, dict):
                            logger.info("availability_extracted", objects=len(data))
                            return data
                    except json.JSONDecodeError:
                        pass
        return {}

    def _build_seats(self, chart_data: dict, availability: dict,
                     pricing: dict, rendering_info: Optional[dict],
                     responses: list[CapturedResponse]) -> tuple:
        """Build seat list from chart + availability + pricing."""
        seats = []
        category_counts = {}
        excluded = chart_data.get('excluded_objects', set())

        object_categories = {}
        category_by_key = {}
        if rendering_info:
            object_categories = rendering_info.get('objectCategories', {})
            category_by_key = rendering_info.get('category_by_key', {})

        # Process rows → seats
        for row in chart_data.get('rows', []):
            section_label = row.get('_section', '')
            row_label = row.get('label', '')

            for seat_obj in row.get('seats', []):
                seat_label = seat_obj.get('label', '')

                # Build composite label
                if section_label:
                    composite = f"{section_label}-{row_label}-{seat_label}"
                else:
                    composite = f"{row_label}-{seat_label}"

                # Skip excluded
                if composite in excluded or seat_label in excluded:
                    continue

                # Get category from rendering-info
                category_label = None
                category_key = None
                lookup_key = composite
                if lookup_key in object_categories:
                    cat_key = object_categories[lookup_key]
                    category_key = str(cat_key)
                    if cat_key in category_by_key:
                        category_label = category_by_key[cat_key].get('label')

                # Get status from availability
                status = SeatStatus.AVAILABLE
                for label_variant in [composite, f"{section_label} {row_label} {seat_label}", seat_label]:
                    if label_variant in availability:
                        status_data = availability[label_variant]
                        if isinstance(status_data, dict):
                            status_str = status_data.get('status', 'free')
                        else:
                            status_str = str(status_data)
                        status = self.parse_seat_status(status_str)
                        break

                # Get price
                price, level_label = self._get_price_for_category(
                    category_key, chart_data.get('category_map', {}),
                    pricing, category_label
                )

                # ADA
                ada = seat_obj.get('accessible', False)
                if rendering_info and lookup_key in rendering_info.get('accessible', {}):
                    ada = True

                seat = Seat(
                    section=section_label or row_label,
                    row=row_label,
                    seat=seat_label,
                    price=price,
                    status=status,
                    price_level=category_label or level_label,
                    price_level_id=category_key,
                    x=seat_obj.get('x'),
                    y=seat_obj.get('y'),
                    ada=ada,
                )
                seats.append(seat)

                # Track category counts
                cat_name = category_label or level_label or 'Unknown'
                if cat_name not in category_counts:
                    category_counts[cat_name] = {'total': 0, 'available': 0, 'price': price, 'key': category_key}
                category_counts[cat_name]['total'] += 1
                if status == SeatStatus.AVAILABLE:
                    category_counts[cat_name]['available'] += 1

        # Build ticket categories from category counts
        ticket_categories = []
        for cat_name, info in category_counts.items():
            ticket_categories.append(TicketCategory(
                category_name=cat_name,
                category_id=info.get('key', cat_name),
                price=info['price'],
                current_stock=info['available'],
                is_out_of_stock=info['available'] == 0,
                group_name=cat_name,
            ))

        # Add GA areas
        for ga_area in chart_data.get('ga_areas', []):
            area_label = ga_area.get('label', '')
            if area_label in excluded:
                continue

            capacity = ga_area.get('capacity', 0)

            # Get category
            category_label = None
            category_key = None
            if area_label in object_categories:
                cat_key = object_categories[area_label]
                category_key = str(cat_key)
                if cat_key in category_by_key:
                    category_label = category_by_key[cat_key].get('label')

            price, level_label = self._get_price_for_category(
                category_key, chart_data.get('category_map', {}),
                pricing, category_label
            )

            display_name = category_label or area_label
            ticket_categories.append(TicketCategory(
                category_name=display_name,
                category_id=category_key or area_label,
                price=price,
                current_stock=capacity if capacity else None,
                group_name='GA',
            ))

        logger.info("seats_built", seats=len(seats), categories=len(ticket_categories))
        return seats, ticket_categories

    def _build_seats_from_object_statuses(self, responses: list[CapturedResponse],
                                           pricing: dict, category_map: dict,
                                           rendering_info: Optional[dict]) -> list[Seat]:
        """Fallback: build seats from object-statuses when chart has no rows."""
        availability = self._extract_availability(responses)
        if not availability:
            return []

        seats = []
        object_categories = rendering_info.get('objectCategories', {}) if rendering_info else {}
        category_by_key = rendering_info.get('category_by_key', {}) if rendering_info else {}

        for label, status_data in availability.items():
            if isinstance(status_data, dict):
                status_str = status_data.get('status', 'free')
            else:
                status_str = str(status_data)
            status = self.parse_seat_status(status_str)

            # Parse label into section/row/seat
            parts = label.split('-')
            if len(parts) >= 3:
                section = parts[0]
                row = parts[1]
                seat_num = '-'.join(parts[2:])
            elif len(parts) == 2:
                section = parts[0]
                row = parts[0]
                seat_num = parts[1]
            else:
                section = label
                row = 'GA'
                seat_num = 'GA'

            # Get category
            category_label = None
            category_key = None
            if label in object_categories:
                cat_key = object_categories[label]
                category_key = str(cat_key)
                if cat_key in category_by_key:
                    category_label = category_by_key[cat_key].get('label')

            price, level_label = self._get_price_for_category(
                category_key, category_map, pricing, category_label
            )

            seats.append(Seat(
                section=section,
                row=row,
                seat=seat_num,
                price=price,
                status=status,
                price_level=category_label or level_label,
                price_level_id=category_key,
            ))

        logger.info("seats_from_object_statuses", count=len(seats))
        return seats

    def _build_seats_from_availability(self, availability: dict, pricing: dict) -> list[Seat]:
        """Last-resort fallback: build seats from availability data only."""
        seats = []
        for label, status_data in availability.items():
            if isinstance(status_data, dict):
                status_str = status_data.get('status', 'free')
            else:
                status_str = str(status_data)

            parts = label.split('-')
            section = parts[0] if parts else label
            row = parts[1] if len(parts) > 1 else 'GA'
            seat_num = parts[2] if len(parts) > 2 else 'GA'

            # Try to find price by section name
            price = Decimal('0')
            for price_key, price_info in pricing.items():
                if price_key.lower() in label.lower() or label.lower() in price_key.lower():
                    price = price_info.get('price', Decimal('0'))
                    break

            seats.append(Seat(
                section=section,
                row=row,
                seat=seat_num,
                price=price,
                status=self.parse_seat_status(status_str),
            ))

        return seats

    def _get_price_for_category(self, category_key: Optional[str],
                                 category_map: dict, pricing: dict,
                                 category_label: Optional[str] = None) -> tuple:
        """
        Get price for a category, trying multiple lookup strategies.

        Returns (price, level_label)
        """
        # Strategy 1: Direct label match in pricing
        if category_label and category_label in pricing:
            info = pricing[category_label]
            return info.get('price', Decimal('0')), category_label

        # Strategy 2: Category map label match
        if category_key:
            map_label = category_map.get(category_key, '')
            if map_label and map_label in pricing:
                info = pricing[map_label]
                return info.get('price', Decimal('0')), map_label

        # Strategy 3: Case-insensitive partial match
        if category_label:
            for price_key, price_info in pricing.items():
                if price_key.lower() == category_label.lower():
                    return price_info.get('price', Decimal('0')), price_key

        # Strategy 4: First available price as fallback
        if pricing:
            first_key = next(iter(pricing))
            return pricing[first_key].get('price', Decimal('0')), first_key

        return Decimal('0'), None

    def _recalculate_ticket_category_stock(self, seats: list[Seat],
                                            ticket_categories: list[TicketCategory]):
        """Recalculate stock counts in ticket categories based on actual seats."""
        cat_counts = {}
        for seat in seats:
            cat_name = seat.price_level or 'Unknown'
            if cat_name not in cat_counts:
                cat_counts[cat_name] = {'available': 0, 'total': 0}
            cat_counts[cat_name]['total'] += 1
            if seat.status == SeatStatus.AVAILABLE:
                cat_counts[cat_name]['available'] += 1

        for tc in ticket_categories:
            if tc.category_name in cat_counts:
                tc.current_stock = cat_counts[tc.category_name]['available']
                tc.is_out_of_stock = cat_counts[tc.category_name]['available'] == 0

    def _build_api_urls(self, responses: list[CapturedResponse],
                        event_data: Optional[dict]) -> dict:
        """Build dict of API URLs for re-querying."""
        api_urls = {}

        if event_data:
            api_urls['event_api'] = f"https://www.tixr.com/api/events/{event_data.get('id')}"

        for resp in responses:
            if 'rendering-info' in resp.url:
                api_urls['rendering_info'] = resp.url
            elif 'charts' in resp.url and 'published' in resp.url:
                api_urls['chart_published'] = resp.url
            elif 'object-statuses' in resp.url:
                api_urls['object_statuses'] = resp.url

        return api_urls
