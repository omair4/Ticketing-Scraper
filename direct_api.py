#!/usr/bin/env python3
"""
Direct API Scraper - Call APIs directly without browser automation

This script is useful when you already know the API endpoints and event IDs.
It's faster than the browser-based approach but requires knowing the correct endpoints.

Usage:
    # Scenario 1: seats.io
    python direct_api.py seatsio --event-id 10031723

    # Scenario 2: Direct API
    python direct_api.py direct --event-id 144917
"""

import asyncio
import argparse
import json
import httpx
from decimal import Decimal
from typing import Optional, Dict, Any
import structlog

from models import Seat, SeatStatus, ScrapeResult, EventInfo
from utils.decoder import decode_hex_offset
from utils.exporters import export_results, print_summary

# Configure logging
structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer()
    ],
)
logger = structlog.get_logger()


class DirectAPICaller:
    """Call AdmitONE APIs directly without browser automation"""
    
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9'
            }
        )
    
    async def close(self):
        await self.client.aclose()
    
    async def scrape_seatsio_event(self, event_id: str, 
                                   public_key: Optional[str] = None) -> ScrapeResult:
        """
        Scrape a seats.io event by calling APIs directly.
        
        Args:
            event_id: The AdmitONE event ID (e.g., "10031723")
            public_key: Optional seats.io public key
        """
        seats = []
        errors = []
        
        # Step 1: Get pricing data from AdmitONE embed API
        pricing_url = (
            f"https://embed.admitonelive.com/api/events/seated/{event_id}"
            f"?methods[]=all_levels"
            f"&methods[]=seatedPriceLevelFees"
            f"&methods[]=event"
            f"&follow[]=venue"
        )
        
        logger.info("fetching_pricing", url=pricing_url)
        
        event_name = f"Event {event_id}"
        venue_name = "Unknown Venue"
        price_levels = {}
        
        try:
            response = await self.client.get(pricing_url)
            response.raise_for_status()
            data = response.json()
            
            # Extract event info
            event_name = data.get('event', event_name)
            venue = data.get('venue', {})
            venue_name = venue.get('venue_name', venue_name)
            
            # Extract price levels
            all_levels = data.get('all_levels', {})
            for level_id, level_data in all_levels.items():
                if isinstance(level_data, dict):
                    price_levels[level_id] = {
                        'name': level_data.get('level', ''),
                        'price': Decimal(str(level_data.get('price', 0))),
                        'service_fee': Decimal(str(level_data.get('online_service_fee', 0)))
                    }
            
            logger.info("pricing_extracted", levels=len(price_levels))
            
            # Get seatsio chart key if available
            if not public_key:
                public_key = data.get('seatingChartPublicKey')
            
        except Exception as e:
            errors.append(f"Failed to fetch pricing: {e}")
            logger.error("pricing_fetch_failed", error=str(e))
        
        # Step 2: Get seat availability from seats.io (if we have the key)
        if public_key:
            status_url = (
                f"https://cdn-eu.seatsio.net/system/public/{public_key}"
                f"/events/object-statuses?event_key={event_id}"
            )
            
            logger.info("fetching_seats", url=status_url[:80])
            
            try:
                response = await self.client.get(status_url)
                response.raise_for_status()
                
                raw_body = response.text
                
                # Try to decode if hex-encoded
                try:
                    seat_data = json.loads(raw_body)
                except json.JSONDecodeError:
                    decoded = decode_hex_offset(raw_body, offset=41)
                    seat_data = json.loads(decoded)
                
                # Parse seats
                if isinstance(seat_data, list):
                    for item in seat_data:
                        seat = self._parse_seatsio_seat(item, price_levels)
                        if seat:
                            seats.append(seat)
                elif isinstance(seat_data, dict):
                    for status, items in seat_data.items():
                        if isinstance(items, list):
                            for item in items:
                                seat = self._parse_seatsio_seat(item, price_levels, status)
                                if seat:
                                    seats.append(seat)
                
                logger.info("seats_extracted", count=len(seats))
                
            except Exception as e:
                errors.append(f"Failed to fetch seats: {e}")
                logger.error("seats_fetch_failed", error=str(e))
        else:
            errors.append("No seats.io public key found - cannot fetch seat data")
        
        return ScrapeResult(
            event=EventInfo(
                event_id=event_id,
                event_name=event_name,
                venue_name=venue_name,
                event_url=f"https://admitone.com/events/{event_id}"
            ),
            scenario_type="seatsio",
            seats=seats,
            errors=errors
        )
    
    def _parse_seatsio_seat(self, item: dict, price_levels: dict, 
                           default_status: str = 'free') -> Optional[Seat]:
        """Parse a seat from seats.io data"""
        label = item.get('objectLabelOrUuid') or item.get('label', '')
        if not label:
            return None
        
        # Parse section-row-seat from label
        parts = label.replace(' - ', '-').split('-')
        if len(parts) >= 3:
            section = parts[0]
            row = parts[-2]
            seat_num = parts[-1]
        elif len(parts) == 2:
            section = parts[0]
            row = parts[1]
            seat_num = 'GA'
        else:
            section = label
            row = 'GA'
            seat_num = 'GA'
        
        # Get price
        category = item.get('categoryLabel', '')
        price = Decimal('0')
        service_fee = None
        
        for level in price_levels.values():
            if category.lower() in level['name'].lower() or level['name'].lower() in category.lower():
                price = level['price']
                service_fee = level['service_fee']
                break
        
        status_str = item.get('status', default_status)
        status = SeatStatus.AVAILABLE if status_str.lower() in ('free', 'available') else SeatStatus.BOOKED
        
        return Seat(
            section=section,
            row=row,
            seat=seat_num,
            price=price,
            service_fee=service_fee,
            status=status,
            price_level=category
        )
    
    async def scrape_direct_api_event(self, event_id: str) -> ScrapeResult:
        """
        Scrape a direct API event (tickets.admitone.com).
        
        Args:
            event_id: The event ID (e.g., "144917")
        """
        seats = []
        errors = []
        
        # Step 1: Get event data with price bands
        event_url = f"https://tickets.admitone.com/events/{event_id}"
        
        logger.info("fetching_event_data", url=event_url)
        
        event_name = f"Event {event_id}"
        venue_name = "Unknown Venue"
        price_bands = {}
        ticket_prices = {}
        
        try:
            response = await self.client.get(event_url)
            response.raise_for_status()
            data = response.json()
            
            # Extract event info
            sp = data.get('seating_plan', {}) or data.get('props', {}).get('seating_plan', {})
            if sp:
                name = sp.get('name', '')
                event_name = name.split('@')[0].strip() if '@' in name else name
            
            # Extract price bands
            bands = data.get('price_bands', [])
            if not bands and 'props' in data:
                bands = data['props'].get('price_bands', [])
            
            for band in bands:
                if isinstance(band, dict) and 'id' in band:
                    price_bands[band['id']] = band.get('name', f"Band {band['id']}")
            
            # Extract ticket prices
            ticket_types = data.get('ticket_types', [])
            if not ticket_types and 'props' in data:
                ticket_types = data['props'].get('ticket_types', [])
            
            for tt in ticket_types:
                band_id = tt.get('price_band_id')
                price = tt.get('price') or tt.get('face_value')
                if band_id and price:
                    ticket_prices[band_id] = Decimal(str(price))
            
            logger.info("event_data_extracted", bands=len(price_bands), prices=len(ticket_prices))
            
        except Exception as e:
            errors.append(f"Failed to fetch event data: {e}")
            logger.error("event_fetch_failed", error=str(e))
        
        # Step 2: Get seat availability
        availability_url = (
            f"https://tickets.admitone.com/events/{event_id}"
            f"/seating-plan/availability?force_refresh=0&with_areas=1"
        )
        
        logger.info("fetching_availability", url=availability_url)
        
        try:
            response = await self.client.get(availability_url)
            response.raise_for_status()
            data = response.json()
            
            seats_data = data.get('seats', [])
            
            for item in seats_data:
                seat = self._parse_direct_api_seat(item, price_bands, ticket_prices)
                if seat:
                    seats.append(seat)
            
            logger.info("seats_extracted", count=len(seats))
            
        except Exception as e:
            errors.append(f"Failed to fetch availability: {e}")
            logger.error("availability_fetch_failed", error=str(e))
        
        return ScrapeResult(
            event=EventInfo(
                event_id=event_id,
                event_name=event_name,
                venue_name=venue_name,
                event_url=f"https://tickets.admitone.com/events/{event_id}"
            ),
            scenario_type="direct_api",
            seats=seats,
            errors=errors
        )
    
    def _parse_direct_api_seat(self, item: dict, price_bands: dict,
                               ticket_prices: dict) -> Optional[Seat]:
        """Parse a seat from direct API data"""
        if item.get('isDeleted'):
            return None
        
        # Extract section, row, seat
        block = item.get('block', {})
        section = block.get('name', '') if isinstance(block, dict) else str(block)
        
        row_data = item.get('row', {})
        row = row_data.get('name', '') if isinstance(row_data, dict) else str(row_data)
        
        seat_num = str(item.get('name', ''))
        
        if not section or not row:
            return None
        
        # Get status
        status_slug = item.get('status_slug', '')
        is_available = item.get('is_available', True)
        status = SeatStatus.AVAILABLE if (status_slug == 'available' or is_available) else SeatStatus.BOOKED
        
        # Get price
        price_band_id = item.get('price_band_id')
        price = ticket_prices.get(price_band_id, Decimal('0'))
        level_name = price_bands.get(price_band_id, '')
        
        # Check embedded price_band
        embedded_band = item.get('price_band', {})
        if embedded_band and not level_name:
            level_name = embedded_band.get('name', '')
        
        return Seat(
            section=section,
            row=row,
            seat=seat_num,
            price=price,
            service_fee=None,
            status=status,
            price_level=level_name
        )


async def main():
    parser = argparse.ArgumentParser(description='Direct API scraper for AdmitONE')
    parser.add_argument('scenario', choices=['seatsio', 'direct'],
                       help='API scenario type')
    parser.add_argument('--event-id', '-e', required=True,
                       help='Event ID')
    parser.add_argument('--public-key', '-k',
                       help='seats.io public key (for seatsio scenario)')
    parser.add_argument('--output-dir', '-o', default='./output',
                       help='Output directory')
    
    args = parser.parse_args()
    
    caller = DirectAPICaller()
    
    try:
        if args.scenario == 'seatsio':
            result = await caller.scrape_seatsio_event(args.event_id, args.public_key)
        else:
            result = await caller.scrape_direct_api_event(args.event_id)
        
        print_summary(result)
        
        if result.seats:
            exported = export_results(result, args.output_dir)
            print("\nExported files:")
            for fmt, filepath in exported.items():
                print(f"  {fmt.upper()}: {filepath}")
        
    finally:
        await caller.close()


if __name__ == '__main__':
    asyncio.run(main())
