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

from models import Seat, SeatStatus, ScrapeResult, EventInfo, TicketCategory, ResaleListing
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
        event_data = {}
        
        try:
            response = await self.client.get(event_url)
            response.raise_for_status()
            data = response.json()
            event_data = data  # Store for later use
            
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
        
        # Step 3: If no seats found, try general admission tickets
        ticket_categories = []
        if not seats:
            # Get event data if we don't have it
            if not event_data:
                try:
                    response = await self.client.get(event_url)
                    response.raise_for_status()
                    event_data = response.json()
                except:
                    pass
            
            if event_data:
                ticket_categories = self._extract_general_admission_tickets(event_data, ticket_prices)
                if ticket_categories:
                    logger.info("general_admission_extracted", count=len(ticket_categories))
        
        # Step 4: Extract resale listings (optional)
        resale_listings = await self._extract_resale_listings(event_id)
        if resale_listings:
            logger.info("resale_listings_extracted", count=len(resale_listings))
        
        return ScrapeResult(
            event=EventInfo(
                event_id=event_id,
                event_name=event_name,
                venue_name=venue_name,
                event_url=f"https://tickets.admitone.com/events/{event_id}"
            ),
            scenario_type="direct_api",
            seats=seats,
            ticket_categories=ticket_categories,
            resale_listings=resale_listings,
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
        
        # Position (if present in response)
        x_val = item.get('x')
        y_val = item.get('y')
        if x_val is not None:
            x_val = float(x_val)
        if y_val is not None:
            y_val = float(y_val)
        
        return Seat(
            section=section,
            row=row,
            seat=seat_num,
            price=price,
            service_fee=None,
            status=status,
            price_level=level_name,
            x=x_val,
            y=y_val,
        )
    
    def _extract_general_admission_tickets(self, event_data: dict,
                                          ticket_prices: dict) -> list[TicketCategory]:
        """
        Extract general admission tickets from props.tickets array.
        
        This handles events with no individual seats, just ticket categories.
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
    
    async def _extract_resale_listings(self, event_id: str) -> list[ResaleListing]:
        """
        Extract resale listings from the resale-listings endpoint.
        
        URL: https://tickets.admitone.com/ajax/events/{event_id}/resale-listings
        """
        listings = []
        
        resale_url = f"https://tickets.admitone.com/ajax/events/{event_id}/resale-listings"
        
        try:
            response = await self.client.get(resale_url)
            if response.status_code != 200:
                return listings
            
            data = response.json()
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
            logger.debug("resale_listings_fetch_failed", event_id=event_id, error=str(e))
        
        return listings


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
