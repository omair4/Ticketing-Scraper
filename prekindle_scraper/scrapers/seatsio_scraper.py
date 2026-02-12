"""
Scraper for PreKindle Scenario 1: seats.io integration

Data Sources:
1. Pricing — from page HTML (seatsio.SeatingChart init script `pricing` parameter)
2. Seat availability — from seats.io API (cdn-na.seatsio.net object-statuses, hex-encoded)
3. Chart structure — from seats.io API (cdn-na.seatsio.net charts/published, hex-encoded)
4. GA area limits — from page HTML (canGASelectionBeIncreased function)
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
    """Scraper for PreKindle events using seats.io seat selection."""

    def get_scenario_name(self) -> str:
        return "seatsio"

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        """Extract seats from seats.io API responses and pricing from page HTML."""

        logger.info("starting_seatsio_extraction", response_count=len(captured_responses))

        # Step 1: Get event info
        event_info = event_info_override or EventInfo(
            event_id=self._extract_event_id(event_url),
            event_name="Unknown Event",
            venue_name="Unknown Venue",
            event_url=event_url,
        )

        # Step 2: Parse pricing from seatsio init script in page HTML
        pricing = self._extract_pricing_from_html(page_html)
        if not pricing:
            self.add_warning("Could not extract pricing from page HTML")

        # Step 3: Extract seatsio config from page HTML (workspaceKey, event key)
        seatsio_config = self._extract_seatsio_config(page_html)

        # Step 4: Extract chart structure from charts/published response
        chart_data = self._extract_chart_data(captured_responses)

        # Step 5: Extract availability from object-statuses response
        availability = self._extract_availability(captured_responses)

        # Step 6: Build seats by combining chart + availability + pricing
        seats = []
        ga_categories = []

        if chart_data:
            seats, ga_categories = self._build_seats(chart_data, availability, pricing)
            # When chart has no rows (e.g. table/booth layout), build seats from object-statuses
            if not seats and availability:
                seats = self._build_seats_from_object_statuses(
                    captured_responses, pricing,
                    chart_data.get('category_map', {}),
                )
        else:
            self.add_warning("No chart data found — trying fallback extraction from availability data")
            seats = self._build_seats_from_availability(availability, pricing)

        # Step 7: Extract additional GA areas from page HTML
        html_ga_categories = self._extract_ga_categories_from_html(page_html, pricing)
        if html_ga_categories:
            # Merge, avoiding duplicates
            existing_names = {c.category_name for c in ga_categories}
            for cat in html_ga_categories:
                if cat.category_name not in existing_names:
                    ga_categories.append(cat)

        if not seats and not ga_categories:
            self.add_error("No seats or categories extracted")

        # Build API URLs for output
        api_urls = self._build_api_urls(captured_responses, seatsio_config)

        return self.create_result(
            event_info,
            seats=seats,
            ticket_categories=ga_categories,
            api_urls=api_urls,
            scrape_method="browser",
        )

    def _extract_pricing_from_html(self, page_html: str) -> Dict[str, Dict[str, Any]]:
        """
        Extract pricing from the seatsio.SeatingChart({...}) init script.

        Looks for the `pricing` parameter:
        pricing: [
          {'category': 'GA', 'ticketTypes': [{'ticketType': '...', 'price': 21.61, 'label': 'Ticket'}]},
          ...
        ]

        Returns:
            Dict mapping category code to {price, label, ticketType_id}
        """
        pricing = {}

        try:
            # Find the pricing array in the seatsio init script
            # Pattern: pricing:\s*\[...\]
            pricing_match = re.search(
                r'pricing\s*:\s*(\[[\s\S]*?\])\s*,?\s*(?:priceFormatter|onObjectSelected|onObjectDeselected|maxSelectedObjects|showMinimap|objectTooltip|\w+\s*:)',
                page_html
            )

            if not pricing_match:
                # Try a simpler pattern
                pricing_match = re.search(r"pricing\s*:\s*(\[.*?\])\s*,", page_html, re.DOTALL)

            if not pricing_match:
                logger.debug("no_pricing_in_html")
                return pricing

            pricing_str = pricing_match.group(1)

            # Convert JS object notation to valid JSON
            # Replace single quotes with double quotes
            pricing_str = pricing_str.replace("'", '"')
            # Handle trailing commas
            pricing_str = re.sub(r',\s*([}\]])', r'\1', pricing_str)

            pricing_list = json.loads(pricing_str)

            for item in pricing_list:
                category_code = item.get('category', '')
                ticket_types = item.get('ticketTypes', [])

                for tt in ticket_types:
                    price = tt.get('price', 0)
                    label = tt.get('label', 'Ticket')
                    ticket_type_id = tt.get('ticketType', '')

                    pricing[category_code] = {
                        'price': Decimal(str(price)),
                        'label': label,
                        'ticketType_id': str(ticket_type_id),
                    }

            logger.info("extracted_pricing_from_html", categories=len(pricing))

        except Exception as e:
            logger.warning("pricing_extraction_failed", error=str(e))

        return pricing

    def _extract_seatsio_config(self, page_html: str) -> Dict[str, str]:
        """
        Extract seats.io configuration from the page HTML.

        Looks for workspaceKey and event key in the SeatingChart init.
        """
        config = {}

        try:
            # workspaceKey (also called publicKey)
            wk_match = re.search(r"(?:workspaceKey|publicKey)\s*:\s*['\"]([^'\"]+)['\"]", page_html)
            if wk_match:
                config['workspace_key'] = wk_match.group(1)

            # event key
            ek_match = re.search(r"event\s*:\s*['\"]([^'\"]+)['\"]", page_html)
            if ek_match:
                config['event_key'] = ek_match.group(1)

        except Exception as e:
            logger.debug("seatsio_config_extraction_failed", error=str(e))

        return config

    def _extract_chart_data(self, responses: list[CapturedResponse]) -> Optional[Dict[str, Any]]:
        """
        Extract chart data from seats.io charts/published API.

        Returns:
            Dict with 'category_map' and 'chart_seats', or None
        """
        charts_response = self.find_response_by_pattern(responses, 'charts/published')
        if not charts_response:
            charts_response = self.find_response_by_pattern(responses, 'charts')

        if not charts_response or not charts_response.body:
            logger.debug("no_charts_response_found")
            return None

        try:
            decoded_json = decode_charts_response(charts_response.body)
            if not decoded_json:
                logger.warning("failed_to_decode_charts_response")
                return None

            chart_data = json.loads(decoded_json)
            sub_chart = chart_data.get('subChart', {})
            rows = sub_chart.get('rows', [])

            # Extract category map
            category_map = {}
            categories = chart_data.get('categories', {})
            category_list = categories.get('list', [])
            for cat in category_list:
                if isinstance(cat, dict) and 'key' in cat and 'label' in cat:
                    category_map[str(cat['key'])] = cat['label']

            # Extract seats from rows
            chart_seats = []

            for row in rows:
                if not isinstance(row, dict):
                    continue

                row_label = row.get('label', '')
                section_label = row.get('sectionLabel', '')
                seats = row.get('seats', [])

                for seat in seats:
                    if not isinstance(seat, dict):
                        continue

                    uuid = seat.get('uuid', '')
                    if not uuid:
                        continue

                    x_val = seat.get('x')
                    y_val = seat.get('y')
                    if x_val is not None:
                        x_val = float(x_val)
                    if y_val is not None:
                        y_val = float(y_val)

                    semi_ambulatory = bool(seat.get('semiAmbulatorySeat', False))
                    hearing_impaired = bool(seat.get('hearingImpaired', False))
                    sign_lang = bool(seat.get('signLanguageInterpretation', False))
                    ada = semi_ambulatory or hearing_impaired or sign_lang

                    chart_seats.append({
                        'section': section_label or 'Unknown',
                        'row': row_label or 'Unknown',
                        'seat_label': seat.get('label', ''),
                        'categoryKey': str(seat.get('categoryKey')) if seat.get('categoryKey') is not None else None,
                        'uuid': uuid,
                        'x': x_val,
                        'y': y_val,
                        'ADA': ada,
                    })

            # Extract GA areas
            ga_areas = []
            general_admission_areas = sub_chart.get('generalAdmissionAreas', [])
            for ga in general_admission_areas:
                if isinstance(ga, dict):
                    ga_areas.append({
                        'label': ga.get('label', ''),
                        'categoryKey': str(ga.get('categoryKey')) if ga.get('categoryKey') is not None else None,
                        'uuid': ga.get('uuid', ''),
                        'capacity': ga.get('capacity'),
                    })

            logger.info("extracted_chart_data",
                         categories=len(category_map),
                         seats=len(chart_seats),
                         ga_areas=len(ga_areas))

            return {
                'category_map': category_map,
                'chart_seats': chart_seats,
                'ga_areas': ga_areas,
            }

        except Exception as e:
            logger.error("chart_extraction_failed", error=str(e))
            return None

    def _extract_availability(self, responses: list[CapturedResponse]) -> Dict[str, str]:
        """
        Extract availability from object-statuses API.

        Returns:
            Dict mapping uuid/label -> status string
        """
        availability = {}

        seatsio_response = self.find_response_by_pattern(responses, 'object-statuses')
        if not seatsio_response:
            seatsio_response = self.find_response_by_pattern(responses, 'seatsio.net')

        if not seatsio_response or not seatsio_response.body:
            logger.warning("no_seatsio_response_for_availability")
            return availability

        try:
            decoded_json = decode_seatsio_response(seatsio_response.body)
            if not decoded_json:
                return availability

            parsed_data = json.loads(decoded_json)

            seat_list = []
            if isinstance(parsed_data, list):
                seat_list = parsed_data
            elif isinstance(parsed_data, dict):
                for status_key, items in parsed_data.items():
                    if isinstance(items, list):
                        seat_list.extend(items)

            for seat_data in seat_list:
                if not isinstance(seat_data, dict):
                    continue

                uuid = seat_data.get('uuid') or seat_data.get('objectLabelOrUuid', '')
                status = seat_data.get('status', 'free')

                if uuid:
                    availability[uuid] = status

            logger.info("extracted_availability", count=len(availability))

        except Exception as e:
            logger.error("availability_extraction_failed", error=str(e))

        return availability

    def _build_seats(self, chart_data: Dict[str, Any], availability: Dict[str, str],
                     pricing: Dict[str, Dict[str, Any]]) -> tuple[List[Seat], List[TicketCategory]]:
        """
        Combine chart seats + availability + pricing into Seat and TicketCategory objects.
        """
        seats = []
        ga_categories = []
        category_map = chart_data.get('category_map', {})
        chart_seats = chart_data.get('chart_seats', [])
        ga_areas = chart_data.get('ga_areas', [])

        # Build seats from chart data
        for cs in chart_seats:
            uuid = cs.get('uuid')
            category_key = cs.get('categoryKey')

            # Status
            status = SeatStatus.UNKNOWN
            if uuid and uuid in availability:
                status = self.parse_seat_status(availability[uuid])
            else:
                status = SeatStatus.AVAILABLE

            # Price
            price, label = self._get_price_for_category(category_key, category_map, pricing)

            seat = Seat(
                section=cs.get('section', 'Unknown'),
                row=cs.get('row', 'Unknown'),
                seat=cs.get('seat_label', '') or 'GA',
                price=price,
                status=status,
                price_level=label or category_map.get(category_key, 'Unknown'),
                x=cs.get('x'),
                y=cs.get('y'),
                ada=cs.get('ADA'),
            )
            seats.append(seat)

        # Build GA categories from chart GA areas
        for ga in ga_areas:
            category_key = ga.get('categoryKey')
            ga_label = ga.get('label', 'General Admission')
            price, ticket_label = self._get_price_for_category(category_key, category_map, pricing)

            # Check availability for this GA area
            ga_uuid = ga.get('uuid', '')
            ga_status = availability.get(ga_uuid, 'free')
            is_sold_out = ga_status in ('booked', 'sold', 'taken')

            ga_categories.append(TicketCategory(
                category_name=ga_label,
                category_id=category_key or ga_uuid,
                price=price,
                current_stock=ga.get('capacity'),
                is_out_of_stock=is_sold_out,
                group_name=category_map.get(category_key, ''),
                ticket_type=ticket_label or 'Ticket',
            ))

        logger.info("built_seats", seat_count=len(seats), ga_count=len(ga_categories))
        return seats, ga_categories

    def _build_seats_from_availability(self, availability: Dict[str, str],
                                       pricing: Dict[str, Dict[str, Any]]) -> List[Seat]:
        """Fallback: build seats from availability data only (no chart data)."""
        seats = []

        for label_or_uuid, status_str in availability.items():
            status = self.parse_seat_status(status_str)

            # Try to parse section-row-seat from label
            section, row, seat_num = self._parse_label(label_or_uuid)

            # Use first available price
            price = Decimal('0')
            if pricing:
                first = next(iter(pricing.values()))
                price = first.get('price', Decimal('0'))

            seats.append(Seat(
                section=section,
                row=row,
                seat=seat_num,
                price=price,
                status=status,
            ))

        return seats

    def _build_seats_from_object_statuses(
        self,
        responses: list[CapturedResponse],
        pricing: Dict[str, Dict[str, Any]],
        category_map: Dict[str, str],
    ) -> List[Seat]:
        """
        Build Seat list from object-statuses API when chart has no rows
        (e.g. table/booth layout). Uses objectLabelOrUuid or label/labels like event_scraper.
        """
        seats = []
        seatsio_response = self.find_response_by_pattern(responses, 'object-statuses')
        if not seatsio_response:
            seatsio_response = self.find_response_by_pattern(responses, 'seatsio.net')
        if not seatsio_response or not seatsio_response.body:
            return seats
        decoded_json = decode_seatsio_response(seatsio_response.body)
        if not decoded_json:
            return seats
        try:
            parsed_data = json.loads(decoded_json)
        except json.JSONDecodeError:
            return seats
        seat_list = []
        if isinstance(parsed_data, list):
            seat_list = parsed_data
        elif isinstance(parsed_data, dict):
            for items in parsed_data.values():
                if isinstance(items, list):
                    seat_list.extend(items)
        for seat_data in seat_list:
            if not isinstance(seat_data, dict):
                continue
            label = seat_data.get('objectLabelOrUuid') or seat_data.get('label', '')
            if not label and seat_data.get('uuid'):
                label = str(seat_data.get('uuid'))
            if not label:
                continue
            section, row, seat_num = self._parse_seat_label_from_object(seat_data, label)
            status_str = seat_data.get('status', 'free')
            status = self.parse_seat_status(status_str)
            category_key = str(seat_data.get('categoryKey', '')) if seat_data.get('categoryKey') is not None else None
            price, level_label = self._get_price_for_category(category_key, category_map, pricing)
            seats.append(Seat(
                section=section,
                row=row,
                seat=seat_num or 'GA',
                price=price,
                status=status,
                price_level=level_label or category_map.get(category_key, ''),
            ))
        logger.info("built_seats_from_object_statuses", count=len(seats))
        return seats

    def _parse_seat_label_from_object(self, seat_data: dict, label: str) -> tuple[str, str, str]:
        """Parse section, row, seat from object-statuses item (labels or label string)."""
        labels = seat_data.get('labels', {})
        if labels:
            section = labels.get('section', '')
            parent = labels.get('parent', {})
            own = labels.get('own', {})
            row = parent.get('label', '') if isinstance(parent, dict) else ''
            seat_num = own.get('label', '') if isinstance(own, dict) else ''
            if section or row or seat_num:
                return section or 'Unknown', row or 'Unknown', seat_num or 'GA'
        return self._parse_label(label)

    def _parse_label(self, label: str) -> tuple[str, str, str]:
        """Parse section-row-seat from a label string."""
        if not label:
            return 'Unknown', 'Unknown', 'Unknown'

        # Pattern: "Section-Row-Seat" or "Label-Row-Seat"
        parts = label.split('-')
        if len(parts) >= 3:
            return parts[0].strip(), parts[1].strip(), parts[2].strip()
        elif len(parts) == 2:
            return parts[0].strip(), parts[1].strip(), 'GA'
        else:
            return label.strip(), 'GA', 'GA'

    def _get_price_for_category(self, category_key: Optional[str],
                                 category_map: Dict[str, str],
                                 pricing: Dict[str, Dict[str, Any]]) -> tuple[Decimal, Optional[str]]:
        """
        Get price for a category key by matching through category_map and pricing.

        Returns:
            Tuple of (price, ticket_label)
        """
        if not category_key:
            if pricing:
                first = next(iter(pricing.values()))
                return first.get('price', Decimal('0')), first.get('label')
            return Decimal('0'), None

        # Direct match: category_key in pricing dict
        if category_key in pricing:
            p = pricing[category_key]
            return p.get('price', Decimal('0')), p.get('label')

        # Match via category_map label → pricing key
        category_label = category_map.get(category_key, '')
        if category_label:
            for pk, pv in pricing.items():
                if pk.lower() == category_label.lower():
                    return pv.get('price', Decimal('0')), pv.get('label')
                # Partial match
                if category_label.lower() in pk.lower() or pk.lower() in category_label.lower():
                    return pv.get('price', Decimal('0')), pv.get('label')

        # Fallback: first price
        if pricing:
            first = next(iter(pricing.values()))
            return first.get('price', Decimal('0')), first.get('label')

        return Decimal('0'), None

    def _extract_ga_categories_from_html(self, page_html: str,
                                          pricing: Dict[str, Dict[str, Any]]) -> List[TicketCategory]:
        """
        Extract GA area info from canGASelectionBeIncreased function in page HTML.

        The function lists GA areas with their max selection limits.
        """
        categories = []

        try:
            # Pattern: canGASelectionBeIncreased function contains GA area references
            ga_match = re.findall(
                r"canGASelectionBeIncreased.*?'([^']+)'.*?(\d+)",
                page_html, re.DOTALL
            )
            if ga_match:
                for ga_name, max_qty in ga_match:
                    price = Decimal('0')
                    label = None
                    # Match GA name to pricing
                    for pk, pv in pricing.items():
                        if pk.lower() == ga_name.lower() or ga_name.lower() in pk.lower():
                            price = pv.get('price', Decimal('0'))
                            label = pv.get('label')
                            break

                    categories.append(TicketCategory(
                        category_name=ga_name,
                        category_id=ga_name,
                        price=price,
                        current_stock=int(max_qty),
                        ticket_type=label or 'Ticket',
                    ))

        except Exception as e:
            logger.debug("ga_html_extraction_failed", error=str(e))

        return categories

    def _build_api_urls(self, responses: list[CapturedResponse],
                        seatsio_config: Dict[str, str]) -> Optional[Dict[str, str]]:
        """Build API URLs dict for output (enables direct re-querying)."""
        api_urls = {}

        # Find object-statuses URL
        for r in responses:
            if 'object-statuses' in r.url:
                api_urls['object_statuses'] = r.url
                break

        # Find charts/published URL
        for r in responses:
            if 'charts/published' in r.url or ('charts' in r.url and 'published' in r.url):
                api_urls['chart_published'] = r.url
                break

        if seatsio_config.get('workspace_key'):
            api_urls['workspace_key'] = seatsio_config['workspace_key']
        if seatsio_config.get('event_key'):
            api_urls['event_key'] = seatsio_config['event_key']

        return api_urls if api_urls else None
