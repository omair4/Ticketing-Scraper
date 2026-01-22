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
from utils.decoder import decode_seatsio_response, parse_json_safe, extract_event_id_from_url

logger = structlog.get_logger()


class SeatsIOScraper(BaseScraper):
    """
    Scraper for events using seats.io seat selection.
    
    This is Scenario 1 in the implementation plan.
    """
    
    def get_scenario_name(self) -> str:
        return "seatsio"
    
    async def extract(self, captured_responses: list[CapturedResponse],
                     page_html: str, event_url: str) -> ScrapeResult:
        """Extract seats from seats.io API responses"""
        
        # #region agent log
        import json as json_module
        target_urls = [
            'object-statuses',
            'embed.admitonelive.com/api/events/seated',
            'seatsio.net'
        ]
        matching_urls = [r.url for r in captured_responses if any(pattern in r.url for pattern in target_urls)]
        with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"C","location":"seatsio_scraper.py:40","message":"extract_entry","data":{"total_responses":len(captured_responses),"matching_urls":matching_urls,"all_urls":[r.url for r in captured_responses[:10]]},"timestamp":int(__import__('time').time()*1000)}) + '\n')
        # #endregion
        
        logger.info("starting_seatsio_extraction", response_count=len(captured_responses))
        
        # Step 1: Get pricing data from embed.admitonelive.com
        price_levels = await self._extract_pricing(captured_responses)
        
        if not price_levels:
            self.add_warning("Could not extract pricing data - prices may be missing")
        
        # Step 2: Get event info
        event_info = await self._extract_event_info(captured_responses, event_url)
        
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
        # #region agent log
        import json as json_module
        with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"seatsio_scraper.py:63","message":"_extract_pricing_entry","data":{"response_count":len(responses)},"timestamp":int(__import__('time').time()*1000)}) + '\n')
        # #endregion
        
        price_levels = {}
        
        # Find the AdmitONE API response - prioritize the /api/events/seated/ endpoint
        admitone_response = self.find_response_by_pattern(responses, 'embed.admitonelive.com/api/events/seated')
        if not admitone_response:
            # Fallback to any embed.admitonelive.com response
            admitone_response = self.find_response_by_pattern(responses, 'embed.admitonelive.com')
        
        # #region agent log
        with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"seatsio_scraper.py:72","message":"admitone_response_found","data":{"found":admitone_response is not None,"url":admitone_response.url if admitone_response else None,"has_body":admitone_response.body is not None if admitone_response else False,"body_length":len(admitone_response.body) if admitone_response and admitone_response.body else 0,"body_preview":admitone_response.body[:200] if admitone_response and admitone_response.body else None},"timestamp":int(__import__('time').time()*1000)}) + '\n')
        # #endregion
        
        if not admitone_response or not admitone_response.body:
            logger.warning("no_admitone_response_found")
            return price_levels
        
        try:
            # #region agent log
            with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"seatsio_scraper.py:79","message":"before_json_parse","data":{"body_type":type(admitone_response.body).__name__,"body_length":len(admitone_response.body),"body_preview":admitone_response.body[:500]},"timestamp":int(__import__('time').time()*1000)}) + '\n')
            # #endregion
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
            # #region agent log
            import json as json_module
            with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"seatsio_scraper.py:104","message":"pricing_extraction_exception","data":{"error":str(e),"error_type":type(e).__name__,"body_preview":admitone_response.body[:500] if admitone_response and admitone_response.body else None},"timestamp":int(__import__('time').time()*1000)}) + '\n')
            # #endregion
            logger.error("pricing_extraction_failed", error=str(e))
        
        # #region agent log
        import json as json_module
        with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"seatsio_scraper.py:106","message":"_extract_pricing_exit","data":{"price_levels_count":len(price_levels)},"timestamp":int(__import__('time').time()*1000)}) + '\n')
        # #endregion
        return price_levels
    
    async def _extract_event_info(self, responses: list[CapturedResponse], 
                                  event_url: str) -> EventInfo:
        """Extract event metadata"""
        
        event_name = "Unknown Event"
        venue_name = "Unknown Venue"
        venue_address = None
        event_date = None
        event_id = extract_event_id_from_url(event_url) or "unknown"
        
        # Try to get from AdmitONE API
        admitone_response = self.find_response_by_pattern(responses, 'embed.admitonelive.com')
        
        if admitone_response and admitone_response.body:
            try:
                data = json.loads(admitone_response.body)
                event_name = data.get('event', event_name)
                
                venue = data.get('venue', {})
                if venue:
                    venue_name = venue.get('venue_name', venue_name)
                    venue_address = f"{venue.get('address', '')}, {venue.get('city', '')} {venue.get('state', '')} {venue.get('zip', '')}"
                
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
    
    async def _extract_seats(self, responses: list[CapturedResponse],
                            price_levels: Dict[str, PriceLevel]) -> list[Seat]:
        """
        Extract seats from seats.io object-statuses API.
        
        The response may be hex-encoded and needs decoding.
        """
        seats = []
        
        # Find seats.io response
        seatsio_response = self.find_response_by_pattern(responses, 'object-statuses')
        
        # #region agent log
        import json as json_module
        with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"seatsio_scraper.py:154","message":"seatsio_response_search","data":{"found_by_object_statuses":seatsio_response is not None,"url":seatsio_response.url if seatsio_response else None},"timestamp":int(__import__('time').time()*1000)}) + '\n')
        # #endregion
        
        if not seatsio_response:
            # Try alternative pattern
            seatsio_response = self.find_response_by_pattern(responses, 'seatsio.net')
            # #region agent log
            with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"seatsio_scraper.py:158","message":"seatsio_response_search_alt","data":{"found_by_seatsio_net":seatsio_response is not None,"url":seatsio_response.url if seatsio_response else None},"timestamp":int(__import__('time').time()*1000)}) + '\n')
            # #endregion
        
        if not seatsio_response or not seatsio_response.body:
            logger.warning("no_seatsio_response_found")
            return seats
        
        # Decode response if needed
        raw_body = seatsio_response.body
        
        # #region agent log
        with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
            body_preview = raw_body[:500] if raw_body else None
            is_hex = all(c in '0123456789abcdefABCDEF' for c in raw_body.strip()) if raw_body else False
            f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"seatsio_scraper.py:215","message":"raw_body_analysis","data":{"body_length":len(raw_body) if raw_body else 0,"body_preview":body_preview,"is_hex":is_hex,"url":seatsio_response.url},"timestamp":int(__import__('time').time()*1000)}) + '\n')
        # #endregion
        
        # Use the new unified decoder that handles base64->hex->offset decoding
        decoded_json = decode_seatsio_response(raw_body)
        parsed_data = None
        
        if decoded_json:
            try:
                parsed_data = json.loads(decoded_json)
                logger.info("seatsio_decode_successful")
                # #region agent log
                with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
                    f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"seatsio_scraper.py:228","message":"seatsio_decode_success","data":{"parsed_type":type(parsed_data).__name__,"data_length":len(decoded_json)},"timestamp":int(__import__('time').time()*1000)}) + '\n')
                # #endregion
            except Exception as e:
                # #region agent log
                with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
                    f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"seatsio_scraper.py:233","message":"decoded_json_parse_failed","data":{"error":str(e)},"timestamp":int(__import__('time').time()*1000)}) + '\n')
                # #endregion
                pass
        
        if not parsed_data:
            # #region agent log
            import json as json_module
            with open('/Users/mac/Documents/Upwork/admitone_scraper/.cursor/debug.log', 'a') as f:
                f.write(json_module.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"seatsio_scraper.py:183","message":"parsed_data_is_none","data":{"raw_body_length":len(raw_body) if raw_body else 0},"timestamp":int(__import__('time').time()*1000)}) + '\n')
            # #endregion
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
        
        logger.info("extracted_seats", count=len(seats))
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
        price, service_fee, level_name = self._get_price_for_seat(seat_data, price_levels)
        
        return Seat(
            section=self.normalize_section_name(section),
            row=row,
            seat=seat_num or "GA",  # GA for general admission
            price=price,
            service_fee=service_fee,
            status=status,
            price_level=level_name
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
                           price_levels: Dict[str, PriceLevel]) -> tuple[Decimal, Optional[Decimal], str]:
        """
        Get price for a seat by matching category/level.
        
        Returns: (price, service_fee, level_name)
        """
        default_price = Decimal('0')
        
        # Try to match by category
        category_key = seat_data.get('categoryKey')
        category_label = seat_data.get('categoryLabel', '')
        
        # Try matching by category key as level_id
        if category_key and str(category_key) in price_levels:
            level = price_levels[str(category_key)]
            return level.price, level.service_fee, level.level_name
        
        # Try matching by category label to level name
        if category_label and hasattr(self, '_price_by_name'):
            label_lower = category_label.lower().strip()
            if label_lower in self._price_by_name:
                level = self._price_by_name[label_lower]
                return level.price, level.service_fee, level.level_name
            
            # Partial match
            for name, level in self._price_by_name.items():
                if label_lower in name or name in label_lower:
                    return level.price, level.service_fee, level.level_name
        
        # Try matching by section name from the label
        label = seat_data.get('objectLabelOrUuid') or seat_data.get('label', '')
        if label and hasattr(self, '_price_by_name'):
            label_lower = label.lower()
            for name, level in self._price_by_name.items():
                if name in label_lower or any(part in label_lower for part in name.split()):
                    return level.price, level.service_fee, level.level_name
        
        # If we have price levels but couldn't match, use the first active one
        if price_levels:
            for level in price_levels.values():
                if level.active:
                    self.add_warning(f"Using default price for seat: {label}")
                    return level.price, level.service_fee, level.level_name
        
        return default_price, None, "Unknown"
