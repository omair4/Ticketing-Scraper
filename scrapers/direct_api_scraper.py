"""
Scraper for Scenario 2: Direct tickets.admitone.com API

This handles events that use the direct AdmitONE ticketing system,
such as the Byun Jin Sub event.

API Flow:
1. tickets.admitone.com/events/{id}/seating-plan/availability - seat availability
2. tickets.admitone.com/events/{id} - event details and price bands

Data structure:
- seats array contains individual seat objects with price_band_id
- price_bands array contains price band definitions
- Each seat has block.name (section), row.name (row), name (seat number)
"""

import json
import re
from typing import Optional, Dict, Any, List
from decimal import Decimal
import structlog
import httpx

from scrapers.base import BaseScraper
from models import ScrapeResult, Seat, EventInfo, SeatStatus, PriceBand, TicketCategory, ResaleListing
from browser import CapturedResponse
from utils.decoder import extract_event_id_from_url

logger = structlog.get_logger()


class DirectAPIScraper(BaseScraper):
    """
    Scraper for events using the direct tickets.admitone.com API.
    
    This is Scenario 2 in the implementation plan.
    """
    
    def get_scenario_name(self) -> str:
        return "direct_api"
    
    async def extract(self, captured_responses: list[CapturedResponse],
                     page_html: str, event_url: str) -> ScrapeResult:
        """Extract seats from tickets.admitone.com API responses"""
        
        logger.info("starting_direct_api_extraction", response_count=len(captured_responses))
        
        # Step 1: Get event data (includes price bands)
        event_data, price_bands = await self._extract_event_data(captured_responses)
        
        # Step 2: Get ticket types with actual prices and fees
        ticket_prices, service_fees = await self._extract_ticket_prices_and_fees(captured_responses, event_data)
        
        if not ticket_prices and not price_bands:
            self.add_warning("Could not extract pricing data - prices may be missing")
        
        # Step 3: Get event info
        event_info = await self._extract_event_info(event_data, captured_responses, event_url, page_html)
        
        # Step 4: Get seat availability
        seats = await self._extract_seats(captured_responses, price_bands, ticket_prices, service_fees)
        
        # Step 5: If no seats found, try general admission tickets
        ticket_categories = []
        if not seats:
            ticket_categories = await self._extract_general_admission_tickets(event_data, ticket_prices, service_fees)
            if ticket_categories:
                logger.info("extracted_general_admission_tickets", count=len(ticket_categories))
            else:
                self.add_warning("No seats or general admission tickets found")
        
        # Step 6: Extract resale listings (optional)
        resale_listings = []
        event_id = event_info.event_id
        if event_id and event_id != "unknown":
            resale_listings = await self._extract_resale_listings(captured_responses, event_id)
            if resale_listings:
                logger.info("extracted_resale_listings", count=len(resale_listings))
        
        return self.create_result(event_info, seats, ticket_categories, resale_listings)
    
    async def _extract_event_data(self, responses: list[CapturedResponse]) -> tuple[dict, Dict[int, PriceBand]]:
        """
        Extract event data and price bands from the event API.
        
        URL pattern: tickets.admitone.com/events/{id}
        """
        event_data = {}
        price_bands = {}
        
        # Find event data response (not the availability one)
        for response in responses:
            if 'tickets.admitone.com' in response.url and '/events/' in response.url:
                # Skip availability endpoint
                if 'seating-plan' in response.url or 'availability' in response.url:
                    continue
                
                if response.body:
                    try:
                        data = json.loads(response.body)
                        
                        # Check if this has the event data structure
                        if 'props' in data or 'price_bands' in data or 'seating_plan' in data:
                            event_data = data
                            break
                    except:
                        pass
        
        # Extract price bands
        if event_data:
            # Price bands might be at different paths depending on API structure
            bands_data = event_data.get('price_bands', [])
            
            # Or nested in props
            if not bands_data and 'props' in event_data:
                props = event_data.get('props', {})
                bands_data = props.get('price_bands', [])
            
            # Or in seating_plan
            if not bands_data and 'seating_plan' in event_data:
                sp = event_data.get('seating_plan', {})
                bands_data = sp.get('price_bands', [])
            
            for band in bands_data:
                if isinstance(band, dict) and 'id' in band:
                    band_id = band['id']
                    price_bands[band_id] = PriceBand(
                        id=band_id,
                        name=band.get('name', f'Band {band_id}'),
                        colour=band.get('colour'),
                        sort_order=band.get('sort_order', 0),
                        has_available_tickets=band.get('has_available_tickets', True)
                    )
            
            logger.info("extracted_price_bands", count=len(price_bands))
        
        return event_data, price_bands
    
    async def _extract_ticket_prices_and_fees(self, responses: list[CapturedResponse], 
                                             event_data: dict) -> tuple[Dict[int, Decimal], Dict[int, Decimal]]:
        """
        Extract ticket prices and service fees from the tickets array.
        
        Returns:
            Tuple of (prices_dict, fees_dict) where both map price_band_id -> amount in dollars
        """
        prices = {}  # price_band_id -> price (in dollars)
        fees = {}    # price_band_id -> service_fee (in dollars)
        
        # Extract from props.tickets array (Scenario 2 structure)
        tickets_data = None
        if 'props' in event_data:
            tickets_data = event_data.get('props', {}).get('tickets', [])
        
        if tickets_data and isinstance(tickets_data, list):
            for ticket_group in tickets_data:
                if not isinstance(ticket_group, dict):
                    continue
                
                items = ticket_group.get('items', [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    
                    price_band_id = item.get('price_band_id')
                    if not price_band_id:
                        continue
                    
                    # Get pricing array
                    pricing = item.get('pricing', [])
                    if pricing and isinstance(pricing, list) and len(pricing) > 0:
                        price_info = pricing[0]
                        if isinstance(price_info, dict):
                            # Extract total_price (includes fees) - convert from cents to dollars
                            total_price_cents = price_info.get('total_price')
                            if total_price_cents is not None:
                                prices[price_band_id] = Decimal(str(total_price_cents)) / Decimal('100')
                            
                            # Extract service fees - convert from cents to dollars
                            customer_fees_cents = price_info.get('customer_fees', 0)
                            if customer_fees_cents:
                                fees[price_band_id] = Decimal(str(customer_fees_cents)) / Decimal('100')
                            
                            logger.debug("extracted_price_and_fees", 
                                       price_band_id=price_band_id, 
                                       total_price_cents=total_price_cents,
                                       fees_cents=customer_fees_cents,
                                       price_dollars=float(prices.get(price_band_id, 0)),
                                       fees_dollars=float(fees.get(price_band_id, 0)))
        
        if prices:
            logger.info("extracted_ticket_prices_from_tickets_array", 
                       price_count=len(prices), 
                       fees_count=len(fees))
            return prices, fees
        
        # Fallback: Try old structure
        prices = await self._extract_ticket_prices_fallback(responses, event_data)
        return prices, {}
    
    async def _extract_ticket_prices_fallback(self, responses: list[CapturedResponse], 
                                             event_data: dict) -> Dict[int, Decimal]:
        """
        Fallback method to extract prices from old structure.
        """
        prices = {}  # price_band_id -> price
        
        # Try to find ticket types in event data (old structure)
        ticket_types = event_data.get('ticket_types', [])
        
        if not ticket_types and 'props' in event_data:
            ticket_types = event_data.get('props', {}).get('ticket_types', [])
        
        for tt in ticket_types:
            if isinstance(tt, dict):
                band_id = tt.get('price_band_id')
                price = tt.get('price') or tt.get('face_value')
                if band_id and price:
                    prices[band_id] = Decimal(str(price))
        
        if prices:
            logger.info("extracted_ticket_prices_from_ticket_types", count=len(prices))
            return prices
        
        # Last resort: Look for prices in other API responses
        for response in responses:
            if response.body and 'price' in response.body.lower():
                try:
                    data = json.loads(response.body)
                    self._find_prices_recursive(data, prices)
                except:
                    pass
        
        return prices
    
    def _find_prices_recursive(self, data: Any, prices: dict, depth: int = 0):
        """Recursively search for price data in nested structures"""
        if depth > 10:  # Prevent infinite recursion
            return
        
        if isinstance(data, dict):
            # Check if this dict has price_band_id and price
            if 'price_band_id' in data and ('price' in data or 'face_value' in data):
                band_id = data['price_band_id']
                price = data.get('price') or data.get('face_value')
                if band_id and price:
                    prices[band_id] = Decimal(str(price))
            
            # Recurse into values
            for value in data.values():
                self._find_prices_recursive(value, prices, depth + 1)
                
        elif isinstance(data, list):
            for item in data:
                self._find_prices_recursive(item, prices, depth + 1)
    
    def _extract_prices_from_schema(self, schema: dict, prices: dict):
        """Extract prices from seating plan schema"""
        # Schema might have objects with priceBandId
        objects = schema.get('objects', [])
        for obj in objects:
            if isinstance(obj, dict):
                rows = obj.get('rows', [])
                for row in rows:
                    if isinstance(row, dict):
                        columns = row.get('columns', [])
                        for col in columns:
                            if isinstance(col, dict) and col.get('type') == 'seat':
                                band_id = col.get('priceBandId')
                                if band_id and band_id not in prices:
                                    # We found a band ID but no price - mark for lookup
                                    prices[band_id] = Decimal('0')
    
    async def _extract_event_info(self, event_data: dict, 
                                  responses: list[CapturedResponse],
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
        
        # Priority 2: Try to extract from event data (may override HTML with more structured data)
        if event_data:
            # Event name might be in seating_plan.name or elsewhere
            sp = event_data.get('seating_plan', {})
            if sp:
                name = sp.get('name', '')
                if name:
                    # Parse name like "Byun Jin Sub / Wish List @ QET - Feb 15, 2026"
                    event_name = name.split('@')[0].strip() if '@' in name else name
                    if ' - ' in name:
                        event_date = name.split(' - ')[-1].strip()
            
            # Get from props if available
            props = event_data.get('props', {})
            if 'event' in props:
                ev = props['event']
                event_name = ev.get('name', event_name)
                event_date = ev.get('start_date', event_date)
            
            # Venue info
            venue = sp.get('venue', {}) or props.get('venue', {})
            if venue:
                venue_name = venue.get('name', venue_name)
                venue_address = venue.get('address')
        
        # Try to get event ID from URL patterns
        for response in responses:
            match = re.search(r'/events/(\d+)', response.url)
            if match:
                event_id = match.group(1)
                break
        
        # Priority 3: Fallback - Extract from URL if still unknown
        if event_name == "Unknown Event" or venue_name == "Unknown Venue":
            url_venue, url_event = self._extract_names_from_url(event_url)
            if venue_name == "Unknown Venue" and url_venue:
                venue_name = url_venue
            if event_name == "Unknown Event" and url_event:
                event_name = url_event
        
        return EventInfo(
            event_id=event_id,
            event_name=event_name,
            venue_name=venue_name,
            venue_address=venue_address,
            event_date=event_date,
            event_url=event_url
        )
    
    def _extract_names_from_url(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """
        Extract venue and event names from AdmitONE URL.
        
        URL pattern: /events/{location}/{category}/{subcategory}/{venue}/{event}/{event}/{id}
        Example: /events/toronto/pro/other/queen-elizabeth-theatre/tom-papa/tom-papa/6915118b6b0dede5013c05fe
        
        Strategy:
        - Find the segment that appears twice (the event name)
        - The segment before it is the venue
        - Convert kebab-case to Title Case
        
        Returns:
            Tuple of (venue_name, event_name) or (None, None) if not found
        """
        try:
            from urllib.parse import urlparse
            
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split('/') if p]
            
            # Look for /events/ in the path
            if 'events' not in path_parts:
                return None, None
            
            events_index = path_parts.index('events')
            
            # Need at least 4 parts after /events/: location, category/subcategory, venue, event
            if len(path_parts) < events_index + 4:
                return None, None
            
            # Get segments after /events/ (excluding the last one which is usually an ID)
            relevant_parts = path_parts[events_index + 1:-1] if len(path_parts) > events_index + 1 else path_parts[events_index + 1:]
            
            if len(relevant_parts) < 2:
                return None, None
            
            # Find the first segment that appears more than once (likely the event name)
            segment_counts = {}
            for segment in relevant_parts:
                segment_counts[segment] = segment_counts.get(segment, 0) + 1
            
            # Find the first duplicate segment (event name)
            event_segment = None
            event_index = None
            for i, segment in enumerate(relevant_parts):
                if segment_counts.get(segment, 0) >= 2:
                    event_segment = segment
                    event_index = i
                    break
            
            if event_segment is None or event_index is None or event_index == 0:
                return None, None
            
            # The venue is the segment just before the event
            venue_segment = relevant_parts[event_index - 1]
            
            # Convert kebab-case to Title Case
            def to_title_case(text: str) -> str:
                return ' '.join(word.capitalize() for word in text.split('-'))
            
            venue_name = to_title_case(venue_segment)
            event_name = to_title_case(event_segment)
            
            logger.debug("extracted_names_from_url", venue=venue_name, event_name=event_name)
            return venue_name, event_name
            
        except Exception as e:
            logger.debug("url_parsing_failed", url=url, error=str(e))
            return None, None
    
    async def _extract_seats(self, responses: list[CapturedResponse],
                            price_bands: Dict[int, PriceBand],
                            ticket_prices: Dict[int, Decimal],
                            service_fees: Dict[int, Decimal] = None) -> list[Seat]:
        """
        Extract seats from the seating-plan/availability API.
        
        Each seat has:
        - price_band_id: Links to price band
        - block: {name: "MEZZL", ...}
        - row: {name: "AA", ...}
        - name: "1" (seat number)
        - status_slug: "available" / "sold"
        - display_as: "AA-1 (MEZZL)" (full label)
        """
        seats = []
        
        # Find availability response
        availability_response = None
        for response in responses:
            if 'seating-plan' in response.url and 'availability' in response.url:
                availability_response = response
                break
        
        # Also check for /availability endpoint directly
        if not availability_response:
            availability_response = self.find_response_by_pattern(responses, '/availability')
        
        if not availability_response or not availability_response.body:
            logger.warning("no_availability_response_found")
            return seats
        
        try:
            data = json.loads(availability_response.body)
            
            # Get seats array
            seats_data = data.get('seats', [])
            
            logger.info("parsing_seats", count=len(seats_data))
            
            for seat_data in seats_data:
                try:
                    seat = self._parse_seat(seat_data, price_bands, ticket_prices, service_fees or {})
                    if seat:
                        seats.append(seat)
                except Exception as e:
                    logger.warning("seat_parse_error", error=str(e))
            
        except Exception as e:
            logger.error("availability_parse_failed", error=str(e))
        
        # If we didn't get seats from availability, try parsing from seating plan schema
        if not seats:
            seats = await self._extract_seats_from_schema(responses, price_bands, ticket_prices, service_fees or {})
        
        return seats
    
    def _parse_seat(self, seat_data: dict, price_bands: Dict[int, PriceBand],
                   ticket_prices: Dict[int, Decimal], service_fees: Dict[int, Decimal] = None) -> Optional[Seat]:
        """
        Parse a single seat from availability data.
        
        Example seat_data:
        {
            "id": 13484466,
            "price_band_id": 71302,
            "name": "1",
            "display_as": "AA-1 (MEZZL)",
            "status_slug": "available",
            "is_available": true,
            "block": {"name": "MEZZL", "local_id": 2},
            "row": {"name": "AA", "local_id": 115},
            "price_band": {"local_id": 3, "name": "Mezzanine 1"}
        }
        """
        # Check if seat is deleted/hidden
        if seat_data.get('isDeleted') or seat_data.get('is_deleted'):
            return None
        
        # Extract section from block
        block = seat_data.get('block', {})
        section = block.get('name', '') if isinstance(block, dict) else str(block)
        
        # Extract row
        row_data = seat_data.get('row', {})
        row = row_data.get('name', '') if isinstance(row_data, dict) else str(row_data)
        
        # Extract seat number
        seat_num = str(seat_data.get('name', ''))
        
        # Can also use display_as for parsing if needed
        display_as = seat_data.get('display_as', '')
        
        if not section and display_as:
            # Parse from display_as like "AA-1 (MEZZL)"
            match = re.search(r'\(([^)]+)\)', display_as)
            if match:
                section = match.group(1)
        
        if not row and display_as:
            # Parse row from display_as like "AA-1 (MEZZL)"
            match = re.match(r'([A-Za-z]+)', display_as)
            if match:
                row = match.group(1)
        
        if not section or not row:
            return None
        
        # Get status
        status_slug = seat_data.get('status_slug', '') or seat_data.get('status', '')
        is_available = seat_data.get('is_available', True) or seat_data.get('is_available_for_user', True)
        
        if status_slug:
            status = self.parse_seat_status(status_slug)
        else:
            status = SeatStatus.AVAILABLE if is_available else SeatStatus.BOOKED
        
        # Get price and fees
        price_band_id = seat_data.get('price_band_id')
        price = Decimal('0')
        service_fee = None
        level_name = ""
        
        # First try ticket_prices mapping
        if price_band_id and price_band_id in ticket_prices:
            price = ticket_prices[price_band_id]
        
        # Get service fee if available
        if service_fees and price_band_id and price_band_id in service_fees:
            service_fee = service_fees[price_band_id]
        
        # Get level name from price_bands
        if price_band_id and price_band_id in price_bands:
            level_name = price_bands[price_band_id].name
        
        # Also check embedded price_band in seat data
        embedded_band = seat_data.get('price_band', {})
        if embedded_band:
            if not level_name:
                level_name = embedded_band.get('name', '')
        
        # Extract restricted_view field (0/0.0 → False, 1/1.0 → True, None if not present)
        # Check for both "restricted" (schema format: 0/1) and "is_restricted" (availability API format: boolean)
        restricted_view = None
        if 'restricted' in seat_data:
            restricted_value = seat_data.get('restricted')
            if restricted_value is not None:
                # Convert to proper boolean (handle int 0/1, float 0.0/1.0, bool)
                restricted_view = bool(int(float(restricted_value)))
        elif 'is_restricted' in seat_data:
            # Availability API uses is_restricted (boolean)
            restricted_value = seat_data.get('is_restricted')
            if restricted_value is not None:
                # Ensure it's a proper boolean
                restricted_view = bool(int(float(restricted_value)))
        return Seat(
            section=self.normalize_section_name(section),
            row=row,
            seat=seat_num,
            price=price,
            service_fee=service_fee,
            status=status,
            price_level=level_name,
            price_level_id=str(price_band_id) if price_band_id else None,
            restricted_view=restricted_view
        )
    
    async def _extract_seats_from_schema(self, responses: list[CapturedResponse],
                                         price_bands: Dict[int, PriceBand],
                                         ticket_prices: Dict[int, Decimal],
                                         service_fees: Dict[int, Decimal] = None) -> list[Seat]:
        """
        Extract seats from seating plan schema if availability API didn't have seat details.
        
        The schema contains the visual layout with all seats defined.
        """
        seats = []
        
        # Find event response with schema
        for response in responses:
            if 'tickets.admitone.com' in response.url and response.body:
                try:
                    data = json.loads(response.body)
                    
                    # Navigate to schema
                    schema = None
                    if 'seating_plan' in data:
                        schema = data['seating_plan'].get('schema')
                    elif 'props' in data:
                        sp = data['props'].get('seating_plan', {})
                        schema = sp.get('schema')
                    
                    if schema:
                        seats = self._parse_schema_seats(schema, price_bands, ticket_prices, service_fees)
                        if seats:
                            break
                except:
                    pass
        
        return seats
    
    def _parse_schema_seats(self, schema: dict, price_bands: Dict[int, PriceBand],
                           ticket_prices: Dict[int, Decimal], service_fees: Dict[int, Decimal] = None) -> list[Seat]:
        """Parse seats from the seating plan schema"""
        seats = []
        
        objects = schema.get('objects', [])
        
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            
            # This is a block (section)
            block_label = obj.get('label', '')
            
            rows = obj.get('rows', [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                
                row_label = row.get('label', '')
                columns = row.get('columns', [])
                
                for col in columns:
                    if not isinstance(col, dict):
                        continue
                    
                    # Check if this is a seat (not a row label)
                    if col.get('type') != 'seat':
                        continue
                    
                    # Skip deleted seats
                    if col.get('isDeleted'):
                        continue
                    
                    seat_num = col.get('defaultLabel', '') or str(col.get('name', ''))
                    price_band_id = col.get('priceBandId')
                    
                    # Get price
                    price = Decimal('0')
                    level_name = ""
                    
                    if price_band_id:
                        # Note: schema uses local_id, need to map to actual price_band_id
                        # Try direct lookup first
                        if price_band_id in ticket_prices:
                            price = ticket_prices[price_band_id]
                        
                        if price_band_id in price_bands:
                            level_name = price_bands[price_band_id].name
                    
                    # Get service fee if available
                    fee = None
                    if service_fees and price_band_id and price_band_id in service_fees:
                        fee = service_fees[price_band_id]
                    
                    # Extract restricted_view field (0/0.0 → False, 1/1.0 → True, None if not present)
                    # Check for both "restricted" (schema format: 0/1) and "is_restricted" (if present)
                    restricted_view = None
                    if 'restricted' in col:
                        restricted_value = col.get('restricted')
                        if restricted_value is not None:
                            # Convert to proper boolean (handle int 0/1, float 0.0/1.0, bool)
                            restricted_view = bool(int(float(restricted_value)))
                    elif 'is_restricted' in col:
                        # Some schemas might use is_restricted
                        restricted_value = col.get('is_restricted')
                        if restricted_value is not None:
                            # Ensure it's a proper boolean
                            restricted_view = bool(int(float(restricted_value)))
                    if block_label and row_label:
                        seats.append(Seat(
                            section=self.normalize_section_name(block_label),
                            row=row_label,
                            seat=seat_num,
                            price=price,
                            service_fee=fee,
                            status=SeatStatus.AVAILABLE,  # Schema doesn't have status
                            price_level=level_name,
                            price_level_id=str(price_band_id) if price_band_id else None,
                            restricted_view=restricted_view
                        ))
        
        return seats
    
    async def _extract_general_admission_tickets(self, event_data: dict,
                                                 ticket_prices: Dict[int, Decimal],
                                                 service_fees: Dict[int, Decimal] = None) -> list[TicketCategory]:
        """
        Extract general admission tickets from props.tickets array.
        
        This handles events with no individual seats, just ticket categories.
        
        Structure:
        - props.tickets[] - array of ticket groups
        - Each group has items[] array
        - Each item has:
          - id: item ID
          - name: ticket category name (e.g., "General Admission")
          - type_slug: "ticket" (skip "product" items)
          - current_stock: available quantity
          - is_out_of_stock: boolean
          - pricing[]: price info (in cents)
        """
        categories = []
        
        if not event_data:
            return categories
        
        props = event_data.get('props', {})
        tickets_data = props.get('tickets', [])
        
        if not tickets_data or not isinstance(tickets_data, list):
            return categories
        
        logger.info("extracting_general_admission_tickets", ticket_groups=len(tickets_data))
        
        for ticket_group in tickets_data:
            if not isinstance(ticket_group, dict):
                continue
            
            group_info = ticket_group.get('group', {})
            group_name = group_info.get('name', '') if isinstance(group_info, dict) else ''
            
            items = ticket_group.get('items', [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                
                # Skip non-ticket items (like refund protection)
                type_slug = item.get('type_slug', '')
                if type_slug != 'ticket':
                    continue
                
                # Get ticket category name
                ticket_name = item.get('name', '')
                if not ticket_name:
                    continue
                
                # Get stock status
                current_stock = item.get('current_stock')
                is_out_of_stock = item.get('is_out_of_stock', False)
                
                # Get pricing (in cents)
                pricing = item.get('pricing', [])
                price = Decimal('0')
                service_fee = None
                
                if pricing and isinstance(pricing, list) and len(pricing) > 0:
                    price_info = pricing[0]
                    if isinstance(price_info, dict):
                        # Convert from cents to dollars
                        base_price_cents = price_info.get('base_price', 0)
                        if base_price_cents:
                            price = Decimal(str(base_price_cents)) / Decimal('100')
                        
                        customer_fees_cents = price_info.get('customer_fees', 0)
                        if customer_fees_cents:
                            service_fee = Decimal(str(customer_fees_cents)) / Decimal('100')
                
                # Create ticket category object
                category = TicketCategory(
                    category_name=ticket_name,
                    category_id=str(item.get('id', '')),
                    price=price,
                    service_fee=service_fee,
                    current_stock=current_stock,
                    is_out_of_stock=is_out_of_stock,
                    group_name=group_name if group_name else None
                )
                
                categories.append(category)
                logger.debug("extracted_general_admission_ticket",
                           name=ticket_name,
                           price=float(price),
                           stock=current_stock,
                           is_available=category.is_available)
        
        return categories
    
    async def _extract_resale_listings(self, responses: list[CapturedResponse],
                                      event_id: str) -> list[ResaleListing]:
        """
        Extract resale listings from the resale-listings endpoint.
        
        URL: https://tickets.admitone.com/ajax/events/{event_id}/resale-listings
        
        Structure:
        - listings[] array
        - Each listing has:
          - resale_listing_id: unique ID
          - contracts[]: array of tickets
          - pricing: price info (in cents)
        """
        listings = []
        
        # Try to find resale listings in captured responses
        resale_response = None
        for response in responses:
            if 'resale-listings' in response.url and response.body:
                resale_response = response
                break
        
        # If not found in captured responses, try to fetch it
        if not resale_response:
            try:
                resale_url = f"https://tickets.admitone.com/ajax/events/{event_id}/resale-listings"
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(resale_url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept': 'application/json'
                    })
                    if response.status_code == 200:
                        resale_response = type('obj', (object,), {
                            'body': response.text,
                            'url': resale_url
                        })()
            except Exception as e:
                logger.debug("resale_listings_fetch_failed", event_id=event_id, error=str(e))
                return listings
        
        if not resale_response or not resale_response.body:
            return listings
        
        try:
            data = json.loads(resale_response.body)
            listings_data = data.get('listings', [])
            
            if not listings_data:
                return listings
            
            logger.info("extracting_resale_listings", count=len(listings_data))
            
            for listing in listings_data:
                if not isinstance(listing, dict):
                    continue
                
                resale_listing_id = listing.get('resale_listing_id')
                contracts = listing.get('contracts', [])
                pricing = listing.get('pricing', {})
                
                if not contracts or not isinstance(contracts, list):
                    continue
                
                # Get ticket category name from first contract
                ticket_category = 'General Admission'
                if contracts and isinstance(contracts[0], dict):
                    sale_item = contracts[0].get('sale_item', {})
                    if sale_item:
                        ticket_category = sale_item.get('name', 'General Admission')
                
                # Get pricing info (in cents)
                base_price_cents = pricing.get('base_price', 0)
                total_price_cents = pricing.get('total_price', 0)
                customer_fees_cents = pricing.get('customer_fees', 0)
                average_price_cents = pricing.get('average_price_per_ticket', 0)
                
                # Calculate price per ticket
                if average_price_cents:
                    price_per_ticket = Decimal(str(average_price_cents)) / Decimal('100')
                elif base_price_cents:
                    price_per_ticket = Decimal(str(base_price_cents)) / Decimal('100')
                else:
                    # Fallback: divide total by number of contracts
                    if len(contracts) > 0 and total_price_cents:
                        price_per_ticket = Decimal(str(total_price_cents)) / Decimal(str(len(contracts))) / Decimal('100')
                    else:
                        price_per_ticket = Decimal('0')
                
                # Calculate service fee per ticket
                service_fee_per_ticket = None
                if customer_fees_cents and len(contracts) > 0:
                    service_fee_per_ticket = Decimal(str(customer_fees_cents)) / Decimal(str(len(contracts))) / Decimal('100')
                
                # Create resale listing object (one per listing, not per ticket)
                resale_listing = ResaleListing(
                    listing_id=str(resale_listing_id) if resale_listing_id else '',
                    ticket_category=ticket_category,
                    quantity=len(contracts),
                    price_per_ticket=price_per_ticket,
                    service_fee_per_ticket=service_fee_per_ticket
                )
                
                listings.append(resale_listing)
                logger.debug("extracted_resale_listing",
                           listing_id=resale_listing_id,
                           ticket_category=ticket_category,
                           quantity=len(contracts),
                           price=float(price_per_ticket))
        
        except Exception as e:
            logger.warning("resale_listings_parse_failed", error=str(e))
        
        return listings
