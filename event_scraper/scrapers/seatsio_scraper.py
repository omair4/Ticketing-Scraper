"""
Scraper for Scenario 1: seats.io integration

This handles events that use seats.io for seat selection, such as the Andrew Callaghan event.

API Flow:
1. seats.io object-statuses API returns seat availability (hex-encoded)
2. embed.admitonelive.com API returns pricing information

Data correlation:
- Seat availability comes from seats.io
- Prices come from the `all_levels` field in the AdmitONE API
- Match seats to prices using level/category names
"""

import json
import re
from typing import Optional, Dict, Any
from decimal import Decimal
import structlog

from scrapers.base import BaseScraper
from models import ScrapeResult, Seat, EventInfo, SeatStatus, PriceLevel
from browser import CapturedResponse
from utils.decoder import decode_seatsio_response, decode_charts_response, parse_json_safe, extract_event_id_from_url

logger = structlog.get_logger()


class SeatsIOScraper(BaseScraper):
    """
    Scraper for events using seats.io seat selection.
    
    This is Scenario 1 in the implementation plan.
    """
    
    def get_scenario_name(self) -> str:
        return "seatsio"
    
    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        """Extract seats from seats.io API responses"""
        
        logger.info("starting_seatsio_extraction", response_count=len(captured_responses))
        
        # Step 1: Get pricing data from embed.admitonelive.com
        price_levels = await self._extract_pricing(captured_responses)
        
        if not price_levels:
            self.add_warning("Could not extract pricing data - prices may be missing")
        
        # Step 2: Get event info (use override when provided)
        event_info = event_info_override or await self._extract_event_info(captured_responses, event_url, page_html)
        
        # Step 3: Get seat availability from seats.io
        seats = await self._extract_seats(captured_responses, price_levels)
        
        if not seats:
            self.add_error("No seats extracted from seats.io API")
        
        return self.create_result(event_info, seats)
    
    async def _extract_pricing(self, responses: list[CapturedResponse]) -> Dict[str, PriceLevel]:
        """
        Extract pricing from the AdmitONE embed API.
        
        The API returns `all_levels` which maps level_id to price info.
        """
        price_levels = {}
        
        # Find the AdmitONE API response - prioritize the /api/events/seated/ endpoint
        admitone_response = self.find_response_by_pattern(responses, 'embed.admitonelive.com/api/events/seated')
        if not admitone_response:
            # Fallback to any embed.admitonelive.com response
            admitone_response = self.find_response_by_pattern(responses, 'embed.admitonelive.com')
        
        if not admitone_response or not admitone_response.body:
            logger.warning("no_admitone_response_found")
            return price_levels
        
        try:
            data = json.loads(admitone_response.body)
            
            # Extract from all_levels
            all_levels = data.get('all_levels', {})
            
            for level_id, level_data in all_levels.items():
                if isinstance(level_data, dict):
                    price_levels[level_id] = PriceLevel(
                        level_id=str(level_id),
                        level_name=level_data.get('level', ''),
                        price=Decimal(str(level_data.get('price', 0))),
                        service_fee=Decimal(str(level_data.get('online_service_fee', 0))),
                        inventory=int(level_data.get('inventory', 0)),
                        active=bool(level_data.get('active', 1))
                    )
            
            logger.info("extracted_price_levels", count=len(price_levels))
            
            # Also create lookup by level name for matching
            self._price_by_name = {}
            for level in price_levels.values():
                name_lower = level.level_name.lower().strip()
                self._price_by_name[name_lower] = level
            
        except Exception as e:
            logger.error("pricing_extraction_failed", error=str(e))
        
        return price_levels
    
    async def _extract_event_info(self, responses: list[CapturedResponse], 
                                  event_url: str, page_html: str = "") -> EventInfo:
        """Extract event metadata"""
        
        event_name = "Unknown Event"
        venue_name = "Unknown Venue"
        venue_address = None
        event_date = None
        event_id = extract_event_id_from_url(event_url) or "unknown"
        
        # Priority 1: Extract from HTML (most reliable, always available)
        if page_html:
            html_info = self._extract_event_info_from_html(page_html)
            if html_info:
                if html_info.get('event_name'):
                    event_name = html_info['event_name']
                if html_info.get('venue_name'):
                    venue_name = html_info['venue_name']
                if html_info.get('venue_address'):
                    venue_address = html_info['venue_address']
                if html_info.get('event_date'):
                    event_date = html_info['event_date']
        
        # Priority 2: Try to get from AdmitONE API (may override HTML with more structured data)
        admitone_response = self.find_response_by_pattern(responses, 'embed.admitonelive.com')
        
        if admitone_response and admitone_response.body:
            try:
                data = json.loads(admitone_response.body)
                # Only override if we don't have values from HTML
                if event_name == "Unknown Event":
                    event_name = data.get('event', event_name)
                
                venue = data.get('venue', {})
                if venue:
                    if venue_name == "Unknown Venue":
                        venue_name = venue.get('venue_name', venue_name)
                    if not venue_address:
                        venue_address = f"{venue.get('address', '')}, {venue.get('city', '')} {venue.get('state', '')} {venue.get('zip', '')}".strip(', ')
                
                event_id = str(data.get('event_id', event_id))
            except:
                pass
        
        return EventInfo(
            event_id=event_id,
            event_name=event_name,
            venue_name=venue_name,
            venue_address=venue_address,
            event_date=event_date,
            event_url=event_url
        )
    
    async def _extract_chart_data(self, responses: list[CapturedResponse]) -> Optional[Dict[str, Any]]:
        """
        Extract chart data from seats.io charts/published API.
        
        Returns:
            Dict with:
            - 'category_map': {categoryKey: category_label}
            - 'chart_seats': list of {section, row, seat_label, categoryKey, uuid}
            Or None if chart data not found
        """
        # Find charts/published response
        charts_response = self.find_response_by_pattern(responses, 'charts/published')
        if not charts_response:
            # Try alternative patterns
            charts_response = self.find_response_by_pattern(responses, 'charts')
        
        if not charts_response or not charts_response.body:
            logger.debug("no_charts_response_found")
            return None
        
        try:
            # Decode the response
            decoded_json = decode_charts_response(charts_response.body)
            if not decoded_json:
                logger.warning("failed_to_decode_charts_response")
                return None
            
            # Parse JSON
            chart_data = json.loads(decoded_json)
            
            # Extract category map: categoryKey -> category label
            category_map = {}
            categories = chart_data.get('categories', {})
            category_list = categories.get('list', [])
            for cat in category_list:
                if isinstance(cat, dict) and 'key' in cat and 'label' in cat:
                    category_map[str(cat['key'])] = cat['label']
            
            # Extract seat data from rows
            chart_seats = []
            sub_chart = chart_data.get('subChart', {})
            rows = sub_chart.get('rows', [])
            
            for row in rows:
                if not isinstance(row, dict):
                    continue
                
                row_label = row.get('label', '')
                section_label = row.get('sectionLabel', '')
                seats = row.get('seats', [])
                
                for seat in seats:
                    if not isinstance(seat, dict):
                        continue
                    
                    seat_label = seat.get('label', '')
                    category_key = seat.get('categoryKey')
                    uuid = seat.get('uuid', '')
                    
                    if not uuid:  # Skip seats without uuid (can't match to availability)
                        continue
                    
                    # Position and ADA from chart seat object
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
                        'seat_label': seat_label or '',
                        'categoryKey': str(category_key) if category_key is not None else None,
                        'uuid': uuid,
                        'x': x_val,
                        'y': y_val,
                        'ADA': ada,
                    })
            
            logger.info("extracted_chart_data", 
                       categories=len(category_map), 
                       seats=len(chart_seats))
            
            return {
                'category_map': category_map,
                'chart_seats': chart_seats
            }
            
        except Exception as e:
            logger.error("chart_extraction_failed", error=str(e))
            return None
    
    async def _extract_seats(self, responses: list[CapturedResponse],
                            price_levels: Dict[str, PriceLevel]) -> list[Seat]:
        """
        Extract seats by combining data from 3 APIs:
        1. Charts API - section, row, seat label, categoryKey, uuid
        2. Object-statuses API - uuid → availability status
        3. Embed API - categoryKey → price (already in price_levels)
        """
        # Step 1: Try to extract chart data (new approach)
        chart_data = await self._extract_chart_data(responses)
        
        if chart_data:
            # Use new 3-API combination approach
            return await self._combine_seats_from_apis(chart_data, responses, price_levels)
        else:
            # Fallback to old approach (using object-statuses labels)
            logger.info("charts_api_not_found_using_fallback")
            return await self._extract_seats_fallback(responses, price_levels)
    
    async def _combine_seats_from_apis(self, chart_data: Dict[str, Any],
                                      responses: list[CapturedResponse],
                                      price_levels: Dict[str, PriceLevel]) -> list[Seat]:
        """
        Combine data from all 3 APIs to build complete seat objects.
        
        Args:
            chart_data: From _extract_chart_data() - contains category_map and chart_seats
            responses: Captured API responses
            price_levels: Pricing data from embed API
        
        Returns:
            List of complete Seat objects
        """
        seats = []
        category_map = chart_data.get('category_map', {})
        chart_seats = chart_data.get('chart_seats', [])
        
        # Step 2: Extract availability from object-statuses API (uuid → status)
        availability_map = await self._extract_availability_map(responses)
        
        # Step 3: Combine all data
        for chart_seat in chart_seats:
            uuid = chart_seat.get('uuid')
            section = chart_seat.get('section', 'Unknown')
            row = chart_seat.get('row', 'Unknown')
            seat_label = chart_seat.get('seat_label', '')
            category_key = chart_seat.get('categoryKey')
            
            # Get availability status by UUID
            status = SeatStatus.UNKNOWN
            if uuid and uuid in availability_map:
                status_str = availability_map[uuid]
                status = self.parse_seat_status(status_str)
            else:
                # Default to available if not found (or log warning)
                status = SeatStatus.AVAILABLE
                logger.debug("uuid_not_found_in_availability", uuid=uuid[:20] if uuid else None)
            
            # Get price by categoryKey
            price, service_fee, level_name, level_id = self._get_price_by_category_key(
                category_key, category_map, price_levels
            )
            
            # Create seat object (include x, y, ADA from chart)
            seat = Seat(
                section=self.normalize_section_name(section),
                row=row,
                seat=seat_label or "GA",
                price=price,
                service_fee=service_fee,
                status=status,
                price_level=level_name,
                price_level_id=level_id,
                x=chart_seat.get('x'),
                y=chart_seat.get('y'),
                ada=chart_seat.get('ADA'),
            )
            seats.append(seat)
        
        logger.info("combined_seats_from_apis", count=len(seats))
        return seats
    
    async def _extract_availability_map(self, responses: list[CapturedResponse]) -> Dict[str, str]:
        """
        Extract availability status from object-statuses API.
        
        Returns:
            Dict mapping uuid -> status string
        """
        availability_map = {}
        
        # Find seats.io response
        seatsio_response = self.find_response_by_pattern(responses, 'object-statuses')
        if not seatsio_response:
            seatsio_response = self.find_response_by_pattern(responses, 'seatsio.net')
        
        if not seatsio_response or not seatsio_response.body:
            logger.warning("no_seatsio_response_for_availability")
            return availability_map
        
        try:
            # Decode response
            decoded_json = decode_seatsio_response(seatsio_response.body)
            if not decoded_json:
                return availability_map
            
            parsed_data = json.loads(decoded_json)
            
            # Handle different response formats
            seat_list = []
            if isinstance(parsed_data, list):
                seat_list = parsed_data
            elif isinstance(parsed_data, dict):
                # Could be grouped by status
                for status_key, items in parsed_data.items():
                    if isinstance(items, list):
                        seat_list.extend(items)
            
            # Build uuid -> status map
            for seat_data in seat_list:
                if not isinstance(seat_data, dict):
                    continue
                
                uuid = seat_data.get('uuid') or seat_data.get('objectLabelOrUuid', '')
                status = seat_data.get('status', 'free')
                
                if uuid:
                    availability_map[uuid] = status
            
            logger.info("extracted_availability_map", count=len(availability_map))
            
        except Exception as e:
            logger.error("availability_extraction_failed", error=str(e))
        
        return availability_map
    
    def _get_price_by_category_key(self, category_key: Optional[str],
                                  category_map: Dict[str, str],
                                  price_levels: Dict[str, PriceLevel]) -> tuple[Decimal, Optional[Decimal], str, Optional[str]]:
        """
        Get price for a seat by matching categoryKey.
        
        Args:
            category_key: Category key from chart data
            category_map: Map of categoryKey -> category label from charts API
            price_levels: Price levels from embed API
        
        Returns:
            Tuple of (price, service_fee, level_name, level_id)
        """
        default_price = Decimal('0')
        
        if not category_key:
            return default_price, None, "Unknown", None
        
        # Step 1: Get category label from category_map
        category_label = category_map.get(category_key, '')
        
        # Step 2: Try to match by categoryKey directly to level_id
        if category_key in price_levels:
            level = price_levels[category_key]
            return level.price, level.service_fee, level.level_name, level.level_id
        
        # Step 3: Try to match category label to level name
        if category_label and hasattr(self, '_price_by_name'):
            label_lower = category_label.lower().strip()
            if label_lower in self._price_by_name:
                level = self._price_by_name[label_lower]
                return level.price, level.service_fee, level.level_name, level.level_id
            
            # Partial match
            for name, level in self._price_by_name.items():
                if label_lower in name or name in label_lower:
                    return level.price, level.service_fee, level.level_name, level.level_id
        
        # Step 4: Fallback - use first active price level
        if price_levels:
            for level in price_levels.values():
                if level.active:
                    self.add_warning(f"Using default price for categoryKey {category_key} (label: {category_label})")
                    return level.price, level.service_fee, level.level_name, level.level_id
        
        return default_price, None, category_label or "Unknown", None
    
    async def _extract_seats_fallback(self, responses: list[CapturedResponse],
                                     price_levels: Dict[str, PriceLevel]) -> list[Seat]:
        """
        Fallback method: Extract seats using old approach (object-statuses labels only).
        Used when charts API is not available.
        """
        seats = []
        
        # Find seats.io response
        seatsio_response = self.find_response_by_pattern(responses, 'object-statuses')
        
        if not seatsio_response:
            # Try alternative pattern
            seatsio_response = self.find_response_by_pattern(responses, 'seatsio.net')
        if not seatsio_response or not seatsio_response.body:
            logger.warning("no_seatsio_response_found")
            return seats
        
        # Decode response if needed
        raw_body = seatsio_response.body
        
        # Use the new unified decoder that handles base64->hex->offset decoding
        decoded_json = decode_seatsio_response(raw_body)
        parsed_data = None
        
        if decoded_json:
            try:
                parsed_data = json.loads(decoded_json)
                logger.info("seatsio_decode_successful")
            except Exception as e:
                pass
        
        if not parsed_data:
            self.add_error("Could not parse seats.io response")
            return seats
        
        # Parse seats from the data
        # Format can be array of objects or dict with status keys
        if isinstance(parsed_data, list):
            seats = self._parse_seat_list(parsed_data, price_levels)
        elif isinstance(parsed_data, dict):
            # Could be grouped by status
            for status_key, seat_list in parsed_data.items():
                if isinstance(seat_list, list):
                    seats.extend(self._parse_seat_list(seat_list, price_levels, 
                                                       default_status=status_key))
        
        logger.info("extracted_seats_fallback", count=len(seats))
        return seats
    
    def _parse_seat_list(self, seat_list: list, price_levels: Dict[str, PriceLevel],
                        default_status: str = 'free') -> list[Seat]:
        """Parse a list of seat objects from seats.io"""
        seats = []
        
        for seat_data in seat_list:
            try:
                seat = self._parse_single_seat(seat_data, price_levels, default_status)
                if seat:
                    seats.append(seat)
            except Exception as e:
                logger.warning("seat_parse_failed", error=str(e), data=str(seat_data)[:100])
        
        return seats
    
    def _parse_single_seat(self, seat_data: dict, price_levels: Dict[str, PriceLevel],
                          default_status: str = 'free') -> Optional[Seat]:
        """
        Parse a single seat from seats.io data.
        
        Seat data format:
        {
            "eventId": 5231032,
            "eventKey": "seating-map-...",
            "objectLabelOrUuid": "Door 3 - Section 203-R-4",
            "status": "free",
            ...
        }
        
        Or with label structure:
        {
            "label": "SEC A-C-11",
            "labels": {
                "own": {"label": "11", "type": "seat"},
                "parent": {"label": "Row C", "type": "row"},
                "section": "Section A"
            },
            "status": "free",
            "categoryKey": "1",
            "categoryLabel": "Premium"
        }
        """
        # Get seat identifier
        label = seat_data.get('objectLabelOrUuid') or seat_data.get('label', '')
        
        if not label:
            return None
        
        # Parse section, row, seat from label
        section, row, seat_num = self._parse_seat_label(label, seat_data)
        
        if not section or not row:
            return None
        
        # Get status
        status_str = seat_data.get('status', default_status)
        status = self.parse_seat_status(status_str)
        
        # Get price from category/level
        price, service_fee, level_name, level_id = self._get_price_for_seat(seat_data, price_levels)
        
        return Seat(
            section=self.normalize_section_name(section),
            row=row,
            seat=seat_num or "GA",  # GA for general admission
            price=price,
            service_fee=service_fee,
            status=status,
            price_level=level_name,
            price_level_id=level_id
        )
    
    def _parse_seat_label(self, label: str, seat_data: dict) -> tuple[str, str, str]:
        """
        Parse section, row, seat number from label string.
        
        Various formats:
        - "Door 3 - Section 203-R-4" -> Section 203, R, 4
        - "Orchestra 1-A-12" -> Orchestra 1, A, 12
        - "Mezzanine" (GA) -> Mezzanine, GA, GA
        """
        section = ""
        row = ""
        seat_num = ""
        
        # First check if we have structured labels
        labels = seat_data.get('labels', {})
        if labels:
            section = labels.get('section', '')
            parent = labels.get('parent', {})
            own = labels.get('own', {})
            
            if isinstance(parent, dict):
                row = parent.get('label', '')
            if isinstance(own, dict):
                seat_num = own.get('label', '')
            
            if section and row:
                return section, row, seat_num
        
        # Parse from label string
        # Format: "Section Name-Row-Seat" or "Door X - Section XXX-Row-Seat"
        
        # Remove door prefix if present
        if ' - ' in label:
            parts = label.split(' - ')
            label = parts[-1]  # Take the last part
        
        # Try common patterns
        # Pattern 1: "SectionName-Row-Seat" (e.g., "Orchestra 1-A-12")
        match = re.match(r'^(.+?)-([A-Za-z]+|\d+)-(\d+)$', label)
        if match:
            return match.group(1), match.group(2), match.group(3)
        
        # Pattern 2: "Section XXX-Row-Seat" (e.g., "Section 203-R-4")  
        match = re.match(r'^Section\s*(\d+)-([A-Za-z]+)-(\d+)$', label, re.IGNORECASE)
        if match:
            return f"Section {match.group(1)}", match.group(2), match.group(3)
        
        # Pattern 3: Just a section name (GA ticket)
        if not '-' in label:
            return label, "GA", "GA"
        
        # Pattern 4: Split by dash and interpret
        parts = label.split('-')
        if len(parts) >= 3:
            section = parts[0]
            row = parts[1]
            seat_num = parts[2]
        elif len(parts) == 2:
            section = parts[0]
            row = parts[1]
            seat_num = "GA"
        else:
            section = label
            row = "GA"
            seat_num = "GA"
        
        return section.strip(), row.strip(), seat_num.strip()
    
    def _get_price_for_seat(self, seat_data: dict, 
                           price_levels: Dict[str, PriceLevel]) -> tuple[Decimal, Optional[Decimal], str, Optional[str]]:
        """
        Get price for a seat by matching category/level.
        
        Returns: (price, service_fee, level_name, level_id)
        """
        default_price = Decimal('0')
        
        # Try to match by category
        category_key = seat_data.get('categoryKey')
        category_label = seat_data.get('categoryLabel', '')
        
        # Try matching by category key as level_id
        if category_key and str(category_key) in price_levels:
            level = price_levels[str(category_key)]
            return level.price, level.service_fee, level.level_name, level.level_id
        
        # Try matching by category label to level name
        if category_label and hasattr(self, '_price_by_name'):
            label_lower = category_label.lower().strip()
            if label_lower in self._price_by_name:
                level = self._price_by_name[label_lower]
                return level.price, level.service_fee, level.level_name, level.level_id
            
            # Partial match
            for name, level in self._price_by_name.items():
                if label_lower in name or name in label_lower:
                    return level.price, level.service_fee, level.level_name, level.level_id
        
        # Try matching by section name from the label
        label = seat_data.get('objectLabelOrUuid') or seat_data.get('label', '')
        if label and hasattr(self, '_price_by_name'):
            label_lower = label.lower()
            for name, level in self._price_by_name.items():
                if name in label_lower or any(part in label_lower for part in name.split()):
                    return level.price, level.service_fee, level.level_name, level.level_id
        
        # If we have price levels but couldn't match, use the first active one
        if price_levels:
            for level in price_levels.values():
                if level.active:
                    self.add_warning(f"Using default price for seat: {label}")
                    return level.price, level.service_fee, level.level_name, level.level_id
        
        return default_price, None, "Unknown", None
