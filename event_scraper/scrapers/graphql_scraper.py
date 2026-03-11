"""
Scraper for Scenario 3: GraphQL API

This handles events that use GraphQL API for ticket information,
such as the Drinkurwater Bass Bottle Tour event.

API Flow:
1. Event page has "Get Tickets" button (not "Buy Now")
2. Clicking opens checkout.admitone.com in a new tab
3. Checkout page makes GraphQL request to graphql.admitone.com
4. Query: OrderSummaryEvent with eventId variable
5. Response contains ticket categories with pricing

Data structure:
- ticketCategories array contains ticket category objects
- Each category has:
  - categoryId: unique ID
  - name: category name (e.g., "Early Bird Tickets")
  - basePrice: base price in cents
  - serviceFee: service fee in cents
  - processingFee: processing fee in cents
  - inventory: available quantity
  - quantityLimit: maximum quantity
  - status: "Active", "Sold Out", "Hidden"
  - unitPrice: array of price components
"""

import json
import re
from typing import Optional, Dict, Any, List
from decimal import Decimal
import structlog

from scrapers.base import BaseScraper
from models import ScrapeResult, EventInfo, TicketCategory
from browser import CapturedResponse
from utils.decoder import extract_event_id_from_url

logger = structlog.get_logger()


class GraphQLScraper(BaseScraper):
    """
    Scraper for events using GraphQL API.
    
    This is Scenario 3 in the implementation plan.
    """
    
    def get_scenario_name(self) -> str:
        return "graphql"
    
    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        """Extract ticket categories from GraphQL API responses"""
        
        logger.info("starting_graphql_extraction", response_count=len(captured_responses))
        
        # Step 1: Find GraphQL response
        graphql_data = await self._extract_graphql_data(captured_responses)
        
        if not graphql_data:
            self.add_error("Could not find GraphQL response")
            # Use override or extract event info from HTML as fallback
            event_info = event_info_override or await self._extract_event_info_from_html(page_html, event_url)
            return self.create_result(event_info, ticket_categories=[])
        
        # Step 2: Extract event info (use override when provided)
        event_info = event_info_override or await self._extract_event_info(graphql_data, captured_responses, event_url, page_html)
        
        # Step 3: Extract ticket categories
        ticket_categories = await self._extract_ticket_categories(graphql_data)
        
        if not ticket_categories:
            self.add_warning("No ticket categories found in GraphQL response")
        
        return self.create_result(event_info, ticket_categories=ticket_categories)
    
    async def _extract_graphql_data(self, responses: list[CapturedResponse]) -> Optional[Dict[str, Any]]:
        """
        Extract GraphQL response data.
        
        URL pattern: graphql.admitone.com/
        """
        # Find GraphQL response - prioritize the one with ticketCategories (largest body)
        graphql_response = None
        largest_body_size = 0
        
        for response in responses:
            if 'graphql.admitone.com' in response.url and response.body:
                body_size = len(response.body)
                # Prioritize responses with larger bodies (likely contain full event data)
                if body_size > largest_body_size:
                    largest_body_size = body_size
                    graphql_response = response
        
        if not graphql_response or not graphql_response.body:
            logger.warning("no_graphql_response_found")
            return None
        
        try:
            data = json.loads(graphql_response.body)
            
            # GraphQL responses typically have 'data' field
            if 'data' in data and 'event' in data['data']:
                event_data = data['data']['event']
                logger.info("extracted_graphql_event_data", 
                          event_id=event_data.get('id'),
                          short_event_id=event_data.get('shortEventId'))
                return event_data
            
            # Fallback: return the whole data structure
            logger.warning("unexpected_graphql_structure", keys=list(data.keys()) if isinstance(data, dict) else [])
            return data if isinstance(data, dict) else None
            
        except json.JSONDecodeError as e:
            logger.error("graphql_json_parse_failed", error=str(e))
            return None
        except Exception as e:
            logger.error("graphql_extraction_failed", error=str(e))
            return None
    
    async def _extract_event_info(self, graphql_data: Dict[str, Any],
                                  responses: list[CapturedResponse],
                                  event_url: str, page_html: str = "") -> EventInfo:
        """Extract event metadata from GraphQL data"""
        
        event_name = "Unknown Event"
        venue_name = "Unknown Venue"
        venue_address = None
        event_date = None
        event_id = "unknown"
        
        # Priority 1: Extract from GraphQL data
        if graphql_data:
            event_name = graphql_data.get('title', event_name)
            
            # Extract venue from address
            address = graphql_data.get('address', {})
            if address:
                venue_name = address.get('businessName', address.get('formattedAddress', venue_name))
                venue_address = address.get('formattedAddress')
            
            # Extract date
            start_date = graphql_data.get('startDate')
            if start_date:
                # Parse ISO date format: "2026-05-09T01:00:00Z"
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    event_date = dt.strftime('%B %d, %Y')
                except:
                    event_date = start_date
            
            # Extract event ID
            event_id = graphql_data.get('id', graphql_data.get('shortEventId', event_id))
        
        # Priority 2: Extract from HTML (fallback)
        if event_name == "Unknown Event" or venue_name == "Unknown Venue":
            html_info = self._extract_event_info_from_html(page_html)
            if html_info:
                if event_name == "Unknown Event" and html_info.get('event_name'):
                    event_name = html_info['event_name']
                if venue_name == "Unknown Venue" and html_info.get('venue_name'):
                    venue_name = html_info['venue_name']
                if not venue_address and html_info.get('venue_address'):
                    venue_address = html_info['venue_address']
                if not event_date and html_info.get('event_date'):
                    event_date = html_info['event_date']
        
        # Try to extract event ID from URL
        if event_id == "unknown":
            # Try to extract short event ID from URL (e.g., FW9EUR)
            short_id = self._extract_short_event_id_from_url(event_url)
            if short_id:
                event_id = short_id
            else:
                # Fallback to standard extraction
                extracted_id = extract_event_id_from_url(event_url)
                if extracted_id:
                    event_id = extracted_id
        
        return EventInfo(
            event_id=event_id,
            event_name=event_name,
            venue_name=venue_name,
            venue_address=venue_address,
            event_date=event_date,
            event_url=event_url
        )
    
    def _extract_short_event_id_from_url(self, url: str) -> Optional[str]:
        """
        Extract short event ID from URL.
        
        Example: https://admitone.com/events/.../FW9EUR
        """
        try:
            # Look for pattern like /FW9EUR at the end of the path
            match = re.search(r'/([A-Z0-9]{6,})$', url)
            if match:
                return match.group(1)
        except Exception as e:
            logger.debug("short_event_id_extraction_failed", url=url, error=str(e))
        
        return None
    
    async def _extract_ticket_categories(self, graphql_data: Dict[str, Any]) -> List[TicketCategory]:
        """
        Extract ticket categories from GraphQL response.
        
        Structure:
        - ticketCategories: array of category objects
        - Each category has:
          - categoryId: unique ID
          - name: category name
          - basePrice: {amount: cents, currency: "CAD"}
          - serviceFee: {amount: cents, currency: "CAD"}
          - processingFee: {amount: cents, currency: "CAD"}
          - inventory: available quantity
          - quantityLimit: maximum quantity
          - quantityDistributed: sold quantity
          - status: "Active", "Sold Out", "Hidden"
          - visibility: "visible", "hidden"
        """
        categories = []
        
        if not graphql_data:
            return categories
        
        ticket_categories_data = graphql_data.get('ticketCategories', [])
        
        if not ticket_categories_data or not isinstance(ticket_categories_data, list):
            logger.warning("no_ticket_categories_in_graphql")
            return categories
        
        logger.info("extracting_ticket_categories", count=len(ticket_categories_data))
        
        for category_data in ticket_categories_data:
            if not isinstance(category_data, dict):
                continue
            
            # Skip hidden categories
            visibility = category_data.get('visibility', 'visible')
            if visibility == 'hidden':
                logger.debug("skipping_hidden_category", name=category_data.get('name'))
                continue
            
            # Extract category name
            category_name = category_data.get('name', '')
            if not category_name:
                continue
            
            # Extract category ID
            category_id = category_data.get('categoryId', '')
            
            # Extract pricing (all amounts are in cents)
            base_price_data = category_data.get('basePrice', {})
            base_price_cents = base_price_data.get('amount', 0) if isinstance(base_price_data, dict) else 0
            
            # Convert from cents to dollars
            price = Decimal(str(base_price_cents)) / Decimal('100')
            
            # Extract service fee
            service_fee_data = category_data.get('serviceFee', {})
            service_fee_cents = service_fee_data.get('amount', 0) if isinstance(service_fee_data, dict) else 0
            service_fee = Decimal(str(service_fee_cents)) / Decimal('100') if service_fee_cents > 0 else None
            
            # Also check unitPrice array for additional fees
            # The unitPrice array contains all price components
            unit_prices = category_data.get('unitPrice', [])
            if unit_prices and isinstance(unit_prices, list):
                # Look for service fee in unitPrice array
                for unit_price in unit_prices:
                    if isinstance(unit_price, dict):
                        charge = unit_price.get('charge', {})
                        if isinstance(charge, dict):
                            charge_amount = charge.get('amount', 0)
                            charge_name = unit_price.get('name', '')
                            # If it's a service fee and we don't have one yet, use it
                            if 'service' in charge_name.lower() and service_fee is None and charge_amount > 0:
                                service_fee = Decimal(str(charge_amount)) / Decimal('100')
            
            # Extract inventory information
            inventory = category_data.get('inventory')
            quantity_limit = category_data.get('quantityLimit', 0)
            quantity_distributed = category_data.get('quantityDistributed', 0)
            quantity_issued = category_data.get('quantityIssued', 0)
            
            # Calculate available stock
            # inventory is the available quantity
            # If inventory is 0 or None, check status
            status = category_data.get('status', '')
            is_out_of_stock = False
            
            if status == 'Sold Out':
                is_out_of_stock = True
                current_stock = 0
            elif inventory is not None:
                current_stock = inventory
                if current_stock == 0:
                    is_out_of_stock = True
            else:
                # Calculate from quantityLimit and quantityDistributed
                if quantity_limit > 0:
                    current_stock = quantity_limit - quantity_distributed
                    if current_stock <= 0:
                        is_out_of_stock = True
                else:
                    current_stock = None
            
            # Create ticket category object
            category = TicketCategory(
                category_name=category_name,
                category_id=str(category_id) if category_id else '',
                price=price,
                service_fee=service_fee,
                current_stock=current_stock,
                is_out_of_stock=is_out_of_stock
            )
            
            categories.append(category)
            logger.debug("extracted_ticket_category",
                       name=category_name,
                       price=float(price),
                       service_fee=float(service_fee) if service_fee else None,
                       stock=current_stock,
                       is_available=category.is_available)
        
        return categories

