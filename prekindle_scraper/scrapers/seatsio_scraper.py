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
import time
from pathlib import Path
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

        # Step 4: Extract rendering-info (category definitions and seat→category mappings)
        rendering_info = self._extract_rendering_info(captured_responses)
        if not rendering_info:
            self.add_warning("No rendering-info found — category assignment may be incomplete")

        # Step 5: Extract chart structure from charts/published response
        # Pass rendering_info to help distinguish tables from GA areas
        chart_data = self._extract_chart_data(captured_responses, rendering_info)

        # Step 6: Extract availability from object-statuses response
        availability = self._extract_availability(captured_responses)

        # Step 7: Build seats by combining rendering-info + chart + availability + pricing
        seats = []
        ticket_categories = []

        if chart_data:
            seats, ticket_categories = self._build_seats(chart_data, availability, pricing, rendering_info, captured_responses)
            # When chart has no rows (e.g. table/booth/couch layout), build seats from object-statuses
            if not seats and availability:
                table_areas = chart_data.get('table_areas', [])
                table_labels_in_chart = {ta.get('label') for ta in table_areas}
                
                seats = self._build_seats_from_object_statuses(
                    captured_responses, pricing,
                    chart_data.get('category_map', {}),
                    rendering_info,
                )
                
                # Add missing tables/couches from chart that aren't in object-statuses
                # These objects exist in the chart but may not have status yet (e.g., not yet on sale)
                # Check ALL built seats (not just tables/couches) to avoid false positives like "Open Seating"
                all_seats_built = {s.section for s in seats}
                missing_tables = table_labels_in_chart - all_seats_built
                
                if missing_tables and table_areas:
                    object_categories_lookup = {}
                    category_by_key_lookup = {}
                    if rendering_info:
                        object_categories_lookup = rendering_info.get('objectCategories', {})
                        category_by_key_lookup = rendering_info.get('category_by_key', {})
                    
                    for table_area in table_areas:
                        table_label = table_area.get('label', '')
                        if table_label in missing_tables:
                            # Build seat for this missing table/couch
                            category_label = None
                            category_key = None
                            
                            # Get category from rendering-info
                            if table_label in object_categories_lookup:
                                cat_key = object_categories_lookup[table_label]
                                if cat_key in category_by_key_lookup:
                                    category_label = category_by_key_lookup[cat_key].get('label')
                                    category_key = str(cat_key)
                            
                            # Get price for this category
                            price, level_label = self._get_price_for_category(
                                category_key, chart_data.get('category_map', {}), pricing, category_label
                            )
                            
                            # Default to available if not in object-statuses (may be not yet on sale)
                            seats.append(Seat(
                                section=table_label,
                                row='GA',
                                seat='GA',
                                price=price,
                                status=SeatStatus.AVAILABLE,  # Default to available if not in object-statuses
                                price_level=category_label or level_label or chart_data.get('category_map', {}).get(category_key or '', ''),
                            ))
                            object_type = "couch" if 'couch' in table_label.lower() else "table"
                            logger.debug(f"added_missing_{object_type}_from_chart", label=table_label, category=category_label)
                    
                    logger.info("added_missing_tables_couches_from_chart", count=len(missing_tables))
                
                # Recalculate stock for ticket categories after building seats from object-statuses
                if seats and ticket_categories:
                    self._recalculate_ticket_category_stock(seats, ticket_categories)
        else:
            self.add_warning("No chart data found — trying fallback extraction from availability data")
            seats = self._build_seats_from_availability(availability, pricing)

        # Step 8: Skip HTML GA extraction - only use API data (rendering-info)
        # HTML extraction can incorrectly identify section names as GA areas
        # All real GA categories should be in rendering-info from seats.io API
        logger.debug("skipping_html_ga_extraction", reason="only_using_api_data")

        if not seats and not ticket_categories:
            self.add_error("No seats or categories extracted")

        # Build API URLs for output
        api_urls = self._build_api_urls(captured_responses, seatsio_config)

        return self.create_result(
            event_info,
            seats=seats,
            ticket_categories=ticket_categories,
            api_urls=api_urls,
            scrape_method="browser",
            html_pricing=pricing,
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

    def _extract_rendering_info(self, responses: list[CapturedResponse]) -> Optional[Dict[str, Any]]:
        """
        Extract rendering-info from seats.io API (plain JSON, not encoded).

        Returns:
            Dict with 'categories', 'objectCategories', 'forSaleConfigsPerEvent'
        """
        rendering_response = self.find_response_by_pattern(responses, 'rendering-info')
        if not rendering_response or not rendering_response.body:
            logger.debug("no_rendering_info_found")
            return None

        try:
            # rendering-info is plain JSON (not hex-encoded)
            rendering_data = json.loads(rendering_response.body)

            categories = rendering_data.get('categories', [])
            object_categories = rendering_data.get('objectCategories', {})
            for_sale_configs = rendering_data.get('forSaleConfigsPerEvent', {})

            # Build category lookup by key
            category_by_key = {}
            for cat in categories:
                if isinstance(cat, dict) and 'key' in cat:
                    category_by_key[cat['key']] = {
                        'label': cat.get('label', ''),
                        'color': cat.get('color', ''),
                        'accessible': cat.get('accessible', False),
                    }

            logger.info("extracted_rendering_info",
                       categories=len(categories),
                       object_mappings=len(object_categories),
                       category_by_key_count=len(category_by_key))

            return {
                'categories': categories,
                'category_by_key': category_by_key,
                'objectCategories': object_categories,
                'forSaleConfigsPerEvent': for_sale_configs,
            }

        except Exception as e:
            logger.error("rendering_info_extraction_failed", error=str(e))
            return None

    def _extract_chart_data(self, responses: list[CapturedResponse], rendering_info: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Extract chart data from seats.io charts/published API.

        Args:
            responses: Captured API responses
            rendering_info: Optional rendering-info to help distinguish tables from GA areas

        Returns:
            Dict with 'category_map', 'chart_seats', 'ga_areas', and 'table_areas'
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
            
            # Build lookup from rendering-info to distinguish tables from GA areas
            object_categories = {}
            if rendering_info:
                object_categories = rendering_info.get('objectCategories', {})
            
            # Extract category map
            category_map = {}
            categories = chart_data.get('categories', {})
            category_list = categories.get('list', [])
            for cat in category_list:
                if isinstance(cat, dict) and 'key' in cat and 'label' in cat:
                    category_map[str(cat['key'])] = cat['label']

            # Extract seats from rows
            chart_seats = []
            ga_areas = []
            table_areas = []  # Tables stored in generalAdmissionAreas
            
            # FIX: Support both subChart (single floor) and subChartFloors (multi-floor venues)
            venue_type = chart_data.get('venueType', '')
            
            if 'subChartFloors' in chart_data:
                # Multi-floor venue (WITH_SECTIONS_AND_FLOORS)
                logger.info("processing_multi_floor_venue", venue_type=venue_type, floors=len(chart_data['subChartFloors']))
                
                for floor_idx, floor in enumerate(chart_data['subChartFloors']):
                    # Extract sections from this floor
                    floor_sections = floor.get('sections', [])
                    floor_rows = floor.get('rows', [])
                    
                    # Helper function to extract seats from rows
                    def extract_seats_from_rows(rows, section_label_override=None):
                        for row in rows:
                            if not isinstance(row, dict):
                                continue

                            row_label = row.get('label', '')
                            section_label = section_label_override or row.get('sectionLabel', '')
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
                    
                    # Extract from floor-level rows
                    extract_seats_from_rows(floor_rows)
                    
                    # Extract from sections in this floor
                    for section in floor_sections:
                        if not isinstance(section, dict):
                            continue
                        section_label = section.get('label', '')
                        section_sub_chart = section.get('subChart', {})
                        section_rows = section_sub_chart.get('rows', [])
                        extract_seats_from_rows(section_rows, section_label)
                    
                    # Extract GA areas from this floor (may include tables)
                    floor_ga_areas = floor.get('generalAdmissionAreas', [])
                    for ga in floor_ga_areas:
                        if not isinstance(ga, dict):
                            continue
                        label = ga.get('label', '')
                        # Use label to distinguish tables/couches from real GA areas
                        is_table = 'table' in label.lower() or 'couch' in label.lower()
                        
                        area_data = {
                            'label': label,
                            'categoryKey': str(ga.get('categoryKey')) if ga.get('categoryKey') is not None else None,
                            'uuid': ga.get('uuid', ''),
                            'capacity': ga.get('capacity'),
                        }
                        
                        if is_table:
                            table_areas.append(area_data)
                        else:
                            ga_areas.append(area_data)
                
                logger.info("extracted_multi_floor_chart_data",
                           categories=len(category_map),
                           seats=len(chart_seats),
                           ga_areas=len(ga_areas),
                           table_areas=len(table_areas))
            else:
                # Single-floor venue (original subChart logic)
                sub_chart = chart_data.get('subChart', {})
                rows = sub_chart.get('rows', [])
                sections = sub_chart.get('sections', [])

                # Helper function to extract seats from rows (single-floor venue)
                def extract_seats_from_rows(rows, section_label_override=None):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue

                        row_label = row.get('label', '')
                        section_label = section_label_override or row.get('sectionLabel', '')
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

                # Extract from top-level rows
                extract_seats_from_rows(rows)

                # Extract from section subCharts
                for section in sections:
                    if not isinstance(section, dict):
                        continue
                    section_label = section.get('label', '')
                    section_sub_chart = section.get('subChart', {})
                    section_rows = section_sub_chart.get('rows', [])
                    extract_seats_from_rows(section_rows, section_label)

                # Extract GA areas (may include tables/couches)
                general_admission_areas = sub_chart.get('generalAdmissionAreas', [])
                for ga in general_admission_areas:
                    if not isinstance(ga, dict):
                        continue
                    label = ga.get('label', '')
                    # Use label to distinguish tables/couches from real GA areas
                    is_table = 'table' in label.lower() or 'couch' in label.lower()
                    
                    area_data = {
                        'label': label,
                        'categoryKey': str(ga.get('categoryKey')) if ga.get('categoryKey') is not None else None,
                        'uuid': ga.get('uuid', ''),
                        'capacity': ga.get('capacity'),
                    }
                    
                    if is_table:
                        table_areas.append(area_data)
                    else:
                        ga_areas.append(area_data)

                logger.info("extracted_chart_data",
                             categories=len(category_map),
                             seats=len(chart_seats),
                             ga_areas=len(ga_areas),
                             table_areas=len(table_areas))

            return {
                'category_map': category_map,
                'chart_seats': chart_seats,
                'ga_areas': ga_areas,
                'table_areas': table_areas,  # Tables stored in generalAdmissionAreas
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

                # Use objectLabelOrUuid as primary key (it's the label string like "Floor 101-A-1")
                label = seat_data.get('objectLabelOrUuid') or seat_data.get('uuid', '')
                status = seat_data.get('status', 'free')

                if label:
                    availability[label] = status

            logger.info("extracted_availability", count=len(availability))
            if availability:
                logger.debug("availability_sample",
                           sample_keys=list(availability.keys())[:5],
                           sample_values=list(availability.values())[:5])

        except Exception as e:
            logger.error("availability_extraction_failed", error=str(e))

        return availability

    def _build_seats(self, chart_data: Dict[str, Any], availability: Dict[str, str],
                     pricing: Dict[str, Dict[str, Any]],
                     rendering_info: Optional[Dict[str, Any]] = None,
                     responses: Optional[list] = None) -> tuple[List[Seat], List[TicketCategory]]:
        """
        Combine rendering-info + chart seats + availability + pricing into Seat and TicketCategory objects.

        Uses rendering-info for accurate category assignment:
        1. Get seat label from chart (e.g., "Floor 101-A-10")
        2. Look up category key: rendering_info['objectCategories'][seat_label] → category_key
        3. Look up category info: rendering_info['category_by_key'][category_key] → {label, color, accessible}
        4. Use category label for price lookup
        """
        seats = []
        ticket_categories = []
        category_map = chart_data.get('category_map', {})
        chart_seats = chart_data.get('chart_seats', [])
        ga_areas = chart_data.get('ga_areas', [])

        # Extract rendering-info lookups
        object_categories = {}
        category_by_key = {}
        excluded_objects = set()  # Objects not for sale
        not_for_sale_area_places = {}  # GA areas with partial not-for-sale counts

        if rendering_info:
            object_categories = rendering_info.get('objectCategories', {})
            category_by_key = rendering_info.get('category_by_key', {})

            # Build set of excluded objects from forSaleConfigsPerEvent
            for_sale_configs = rendering_info.get('forSaleConfigsPerEvent', {})
            for event_id, config in for_sale_configs.items():
                if isinstance(config, dict) and not config.get('forSale', True):
                    # Add all objects marked as not for sale
                    excluded_objs = config.get('objects', [])
                    if isinstance(excluded_objs, list):
                        excluded_objects.update(excluded_objs)

            # Extract areaPlaces — GA areas with partial not-for-sale counts
            not_for_sale_area_places = {}
            for event_id, config in for_sale_configs.items():
                if isinstance(config, dict) and not config.get('forSale', True):
                    area_places = config.get('areaPlaces', {})
                    if isinstance(area_places, dict):
                        not_for_sale_area_places.update(area_places)

            if not_for_sale_area_places:
                logger.info("not_for_sale_area_places", areas=not_for_sale_area_places)

            if excluded_objects:
                logger.info("excluded_not_for_sale_objects", count=len(excluded_objects),
                           objects=list(excluded_objects))

        # Build seats from chart data
        for cs in chart_seats:
            uuid = cs.get('uuid')
            seat_label = cs.get('seat_label', '')
            category_key = cs.get('categoryKey')

            # Extract seat components for composite label building
            section = cs.get('section', '')
            row = cs.get('row', '')

            # Skip seats from sections/rows marked as not for sale
            if excluded_objects:
                # Check if section, row, or full seat label is in excluded objects
                if section in excluded_objects or row in excluded_objects or seat_label in excluded_objects:
                    logger.debug("skipping_not_for_sale_seat",
                               section=section, row=row, seat=seat_label)
                    continue

            # Build composite labels for both status and category lookup
            # Handle case where row is unknown/missing (sections that ARE rows, e.g., "Row A", "Row B")
            if row in ('?', 'Unknown', ''):
                # Section label IS the row - use 2-component format: "Row A-1"
                composite_labels = [
                    f"{section}-{seat_label}",       # "Row A-1" (section directly to seat)
                    seat_label,                       # Just seat label as fallback
                ]
            else:
                # Normal 3-component format
                composite_labels = [
                    f"{section}-{row}-{seat_label}",    # "Floor 101-A-1" (hyphenated - BEST match per test)
                    f"{section} {row}-{seat_label}",    # "Floor 101 A-1" (spaced variant)
                    seat_label,                          # Just seat label as fallback
                ]

            # Lookup status using composite label (NOT uuid)
            status = SeatStatus.AVAILABLE  # Default: seats not in object-statuses are available
            matched_label = None
            
            for label_variant in composite_labels:
                if label_variant in availability:
                    status = self.parse_seat_status(availability[label_variant])
                    matched_label = label_variant
                    logger.debug("status_matched", seat=label_variant, status=status.value)
                    break

            if not matched_label:
                logger.debug("seat_not_in_availability",
                           seat=f"{section}-{row}-{seat_label}",
                           defaulted_to="available")

            # NEW: Use rendering-info for category assignment (reuse composite_labels)
            category_label = None
            ada_accessible = None

            # Try label variations for category lookup
            lookup_label = None
            for label_variant in composite_labels:
                if label_variant in object_categories:
                    lookup_label = label_variant
                    break

            if lookup_label:
                category_key_from_rendering = object_categories[lookup_label]
                
                # FIX: Normalize to int - rendering-info returns mixed int/string types
                try:
                    category_key_from_rendering = int(category_key_from_rendering)
                except (ValueError, TypeError):
                    pass  # Keep as-is if conversion fails
                
                if category_key_from_rendering in category_by_key:
                    cat_info = category_by_key[category_key_from_rendering]
                    category_label = cat_info.get('label')
                    ada_accessible = cat_info.get('accessible')
                    # Update category_key to use the one from rendering-info
                    category_key = str(category_key_from_rendering)
                    logger.debug("category_matched", seat=lookup_label, category=category_label)

            # Fallback: use chart categoryKey and category_map
            if not category_label and category_key:
                category_label = category_map.get(category_key)

            # Price lookup using category label (more reliable than category key)
            price, ticket_label = self._get_price_for_category(
                category_key, category_map, pricing, category_label
            )

            # Combine ADA info from rendering-info and chart
            ada_flag = cs.get('ADA', False)
            if ada_accessible is not None:
                ada_flag = ada_flag or ada_accessible

            seat = Seat(
                section=cs.get('section', 'Unknown'),
                row=cs.get('row', 'Unknown'),
                seat=seat_label or 'GA',
                price=price,
                status=status,
                price_level=category_label or ticket_label or category_map.get(category_key, 'Unknown'),
                x=cs.get('x'),
                y=cs.get('y'),
                ada=ada_flag if ada_flag else None,
            )
            seats.append(seat)

        # Build ticket categories from rendering-info categories (not just GA areas)
        ticket_categories = []
        if rendering_info and category_by_key:
            categories_list = rendering_info.get('categories', [])

            for cat_def in categories_list:
                if not isinstance(cat_def, dict):
                    continue

                category_key = cat_def.get('key')
                category_label = cat_def.get('label', '')

                if not category_label:
                    continue

                # Get price for this category
                price, ticket_label = self._get_price_for_category(
                    str(category_key), category_map, pricing, category_label
                )

                # Determine capacity and availability
                current_stock = None
                is_sold_out = False

                # Check if this is a GA category by collecting ALL matching GA areas
                # (a single category can span multiple sub-areas, e.g. Lower Lawn has
                # "Left Lower Lawn General Admission" + "Right Lower Lawn General Admission")
                matching_ga_areas = []
                for ga in ga_areas:
                    ga_label = ga.get('label', '')

                    # Skip GA areas marked as not for sale
                    if excluded_objects and ga_label in excluded_objects:
                        logger.debug("skipping_not_for_sale_ga_area", area=ga_label)
                        continue

                    # Look up GA area's category via objectCategories
                    if ga_label in object_categories:
                        ga_cat_key = object_categories[ga_label]
                        # Normalize to int — rendering-info returns mixed int/string types
                        try:
                            ga_cat_key = int(ga_cat_key)
                        except (ValueError, TypeError):
                            pass
                        if ga_cat_key == category_key:
                            matching_ga_areas.append(ga)

                if matching_ga_areas:
                    # GA category - calculate available stock
                    # Formula: available = for_sale_capacity - booked
                    #   for_sale_capacity = chart_capacity - areaPlaces (not-for-sale)
                    #   booked = quantity from object-statuses (booked count)
                    total_capacity = sum(ga.get('capacity') or 0 for ga in matching_ga_areas)
                    total_booked = 0
                    booked_from_api = False
                    all_sold_out = True

                    for matching_ga_area in matching_ga_areas:
                        ga_uuid = matching_ga_area.get('uuid', '')
                        ga_label = matching_ga_area.get('label', '')

                        ga_status_str = availability.get(ga_uuid) or availability.get(ga_label, 'free')
                        if ga_status_str not in ('booked', 'sold', 'taken'):
                            all_sold_out = False

                        # Extract booked quantity from object-statuses for this sub-area
                        if responses:
                            for response in responses:
                                if 'object-statuses' in response.url and response.body:
                                    try:
                                        decoded = decode_seatsio_response(response.body)
                                        if decoded:
                                            parsed = json.loads(decoded)
                                            seat_list = parsed if isinstance(parsed, list) else []
                                            for item in seat_list:
                                                if not isinstance(item, dict):
                                                    continue
                                                item_label = item.get('objectLabelOrUuid', '')
                                                if item_label == ga_uuid or item_label == ga_label:
                                                    quantity = item.get('quantity')
                                                    if quantity is not None:
                                                        total_booked += int(quantity)
                                                        booked_from_api = True
                                                    logger.info("ga_booked_from_api",
                                                              ga_label=ga_label,
                                                              booked=quantity,
                                                              capacity=matching_ga_area.get('capacity'))
                                                    break
                                    except Exception as e:
                                        logger.debug("failed_to_extract_ga_quantity", error=str(e))

                    # Calculate for-sale capacity by subtracting not-for-sale places
                    not_for_sale_total = 0
                    if not_for_sale_area_places:
                        not_for_sale_total = sum(
                            not_for_sale_area_places.get(ga.get('label', ''), 0)
                            for ga in matching_ga_areas
                        )

                    for_sale_capacity = max(0, total_capacity - not_for_sale_total)

                    if booked_from_api:
                        current_stock = max(0, for_sale_capacity - total_booked)
                    else:
                        # No API data — use for_sale_capacity as best estimate
                        current_stock = for_sale_capacity

                    is_sold_out = all_sold_out if current_stock > 0 else True
                    logger.info("ga_category_stock_resolved",
                               category=category_label,
                               sub_areas=len(matching_ga_areas),
                               total_capacity=total_capacity,
                               not_for_sale=not_for_sale_total,
                               for_sale_capacity=for_sale_capacity,
                               booked=total_booked if booked_from_api else 'N/A',
                               current_stock=current_stock,
                               is_sold_out=is_sold_out)
                else:
                    # Regular seat category - count available seats with this category
                    # Stock will be calculated after seats are built
                    current_stock = None
                    is_sold_out = False

                # Skip categories with zero price (likely section/area names, not price categories)
                if price == 0:
                    logger.debug("skipping_zero_price_category_from_rendering_info", 
                               category_label=category_label, category_key=category_key)
                    continue
                
                ticket_categories.append(TicketCategory(
                    category_name=category_label,
                    category_id=str(category_key),
                    price=price,
                    current_stock=current_stock,
                    is_out_of_stock=is_sold_out,
                    group_name=category_map.get(str(category_key), ''),
                    ticket_type=ticket_label or 'Ticket',
                ))

            logger.info("built_ticket_categories_from_rendering_info", count=len(ticket_categories))
        else:
            # Fallback: use old GA areas approach if rendering-info not available
            logger.warning("no_rendering_info_for_categories_falling_back_to_ga_areas")
            for ga in ga_areas:
                category_key = ga.get('categoryKey')
                ga_label = ga.get('label', 'General Admission')

                # Skip GA areas marked as not for sale
                if excluded_objects and ga_label in excluded_objects:
                    logger.debug("skipping_not_for_sale_ga_area_fallback", area=ga_label)
                    continue

                price, ticket_label = self._get_price_for_category(
                    category_key, category_map, pricing, ga_label
                )

                ga_uuid = ga.get('uuid', '')
                ga_status = availability.get(ga_uuid, 'free')
                is_sold_out = ga_status in ('booked', 'sold', 'taken')

                ticket_categories.append(TicketCategory(
                    category_name=ga_label,
                    category_id=category_key or ga_uuid,
                    price=price,
                    current_stock=ga.get('capacity'),
                    is_out_of_stock=is_sold_out,
                    ticket_type=ticket_label or 'Ticket',
                ))

        # Calculate available seat counts per category
        if seats and ticket_categories:
            from collections import Counter

            # Count available seats by price_level (which is the category label)
            available_by_category = Counter(
                seat.price_level for seat in seats
                if seat.status == SeatStatus.AVAILABLE
            )

            # Count booked seats by category
            booked_by_category = Counter(
                seat.price_level for seat in seats
                if seat.status in (SeatStatus.BOOKED, SeatStatus.HELD)
            )

            # Update ticket_categories with seat counts
            for cat in ticket_categories:
                if cat.current_stock is None:  # Only for non-GA categories
                    cat.current_stock = available_by_category.get(cat.category_name, 0)

                    # Update is_out_of_stock if we have booked seats but no available seats
                    if cat.current_stock == 0 and booked_by_category.get(cat.category_name, 0) > 0:
                        cat.is_out_of_stock = True

        logger.info("built_seats", seat_count=len(seats), category_count=len(ticket_categories))
        return seats, ticket_categories

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
        rendering_info: Optional[Dict[str, Any]] = None,
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

        # Extract rendering-info lookups
        object_categories = {}
        category_by_key = {}
        if rendering_info:
            object_categories = rendering_info.get('objectCategories', {})
            category_by_key = rendering_info.get('category_by_key', {})

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

            # NEW: Use rendering-info for category label
            category_label = None
            if rendering_info and label in object_categories:
                cat_key = object_categories[label]
                if cat_key in category_by_key:
                    category_label = category_by_key[cat_key].get('label')
                    category_key = str(cat_key)

            price, level_label = self._get_price_for_category(category_key, category_map, pricing, category_label)

            seats.append(Seat(
                section=section,
                row=row,
                seat=seat_num or 'GA',
                price=price,
                status=status,
                price_level=category_label or level_label or category_map.get(category_key, ''),
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
                                 pricing: Dict[str, Dict[str, Any]],
                                 category_label: Optional[str] = None) -> tuple[Decimal, Optional[str]]:
        """
        Get price for a category by matching through category_label, category_key, and category_map.

        Matching priority:
        1. Exact match by category_label (from rendering-info)
        2. Exact match by category_key
        3. Match via category_map label → pricing key
        4. Partial match by category_label or category_map label
        5. Fallback: first price

        Returns:
            Tuple of (price, ticket_label)
        """
        # NEW: Try category_label first (most reliable from rendering-info)
        if category_label and pricing:
            # Exact match
            if category_label in pricing:
                p = pricing[category_label]
                return p.get('price', Decimal('0')), p.get('label')

            # Case-insensitive match
            for pk, pv in pricing.items():
                if pk.lower() == category_label.lower():
                    return pv.get('price', Decimal('0')), pv.get('label')

        # Direct match: category_key in pricing dict
        if category_key and category_key in pricing:
            p = pricing[category_key]
            return p.get('price', Decimal('0')), p.get('label')

        # Match via category_map label → pricing key
        if category_key:
            mapped_label = category_map.get(category_key, '')
            if mapped_label:
                # Exact match
                if mapped_label in pricing:
                    p = pricing[mapped_label]
                    return p.get('price', Decimal('0')), p.get('label')

                # Case-insensitive match
                for pk, pv in pricing.items():
                    if pk.lower() == mapped_label.lower():
                        return pv.get('price', Decimal('0')), pv.get('label')

        # Partial match with category_label
        if category_label and pricing:
            for pk, pv in pricing.items():
                if category_label.lower() in pk.lower() or pk.lower() in category_label.lower():
                    logger.debug("partial_category_match", category_label=category_label, pricing_key=pk)
                    return pv.get('price', Decimal('0')), pv.get('label')

        # Partial match via category_map
        if category_key:
            mapped_label = category_map.get(category_key, '')
            if mapped_label:
                for pk, pv in pricing.items():
                    if mapped_label.lower() in pk.lower() or pk.lower() in mapped_label.lower():
                        logger.debug("partial_category_map_match", mapped_label=mapped_label, pricing_key=pk)
                        return pv.get('price', Decimal('0')), pv.get('label')

        # No match found — return 0 so category is filtered out
        # (Previously fell back to first price, causing unpriced categories to appear with wrong price)
        logger.debug("no_price_match_found", category_key=category_key, category_label=category_label)
        return Decimal('0'), None

    def _recalculate_ticket_category_stock(self, seats: List[Seat], ticket_categories: List[TicketCategory]):
        """
        Recalculate current_stock for ticket categories based on available seats.
        
        This is needed when seats are built from object-statuses separately from ticket categories.
        """
        from collections import Counter
        
        # Count available seats by price_level (which is the category label)
        available_by_category = Counter(
            seat.price_level for seat in seats
            if seat.status == SeatStatus.AVAILABLE
        )
        
        # Count booked seats by category
        booked_by_category = Counter(
            seat.price_level for seat in seats
            if seat.status in (SeatStatus.BOOKED, SeatStatus.HELD)
        )
        
        # Update ticket_categories with seat counts
        for cat in ticket_categories:
            if cat.current_stock is None:  # Only update if stock wasn't set (non-GA categories)
                cat.current_stock = available_by_category.get(cat.category_name, 0)
                
                # Update is_out_of_stock if we have booked seats but no available seats
                if cat.current_stock == 0 and booked_by_category.get(cat.category_name, 0) > 0:
                    cat.is_out_of_stock = True
        
        logger.info("recalculated_ticket_category_stock", 
                   categories_updated=sum(1 for cat in ticket_categories if cat.current_stock is not None))

    def _extract_ga_categories_from_html(self, page_html: str,
                                          pricing: Dict[str, Dict[str, Any]],
                                          rendering_info: Optional[Dict[str, Any]] = None) -> List[TicketCategory]:
        """
        Extract GA area info from canGASelectionBeIncreased function in page HTML.

        The function lists GA areas with their max selection limits.
        """
        categories = []

        # Extract rendering-info lookups
        object_categories = {}
        category_by_key = {}
        if rendering_info:
            object_categories = rendering_info.get('objectCategories', {})
            category_by_key = rendering_info.get('category_by_key', {})

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

                    # NEW: Try rendering-info first
                    category_label = None
                    if rendering_info and ga_name in object_categories:
                        cat_key = object_categories[ga_name]
                        if cat_key in category_by_key:
                            category_label = category_by_key[cat_key].get('label')

                    # Match GA name to pricing (use category_label if available)
                    lookup_name = category_label or ga_name
                    for pk, pv in pricing.items():
                        if pk.lower() == lookup_name.lower() or lookup_name.lower() in pk.lower():
                            price = pv.get('price', Decimal('0'))
                            label = pv.get('label')
                            break

                    categories.append(TicketCategory(
                        category_name=category_label or ga_name,
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

        # Find rendering-info URL
        for r in responses:
            if 'rendering-info' in r.url:
                api_urls['rendering_info'] = r.url
                break

        if seatsio_config.get('workspace_key'):
            api_urls['workspace_key'] = seatsio_config['workspace_key']
        if seatsio_config.get('event_key'):
            api_urls['event_key'] = seatsio_config['event_key']

        return api_urls if api_urls else None
