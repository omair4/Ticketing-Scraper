"""
General Admission scraper for Tixr events.

Extracts ticket categories from the /api/events/{id} response.
Most Tixr events are GA — ticket data comes from the sales[] array.
"""

from typing import Optional, List
from decimal import Decimal
import structlog

from models import ScrapeResult, TicketCategory, EventInfo
from browser import CapturedResponse
from scrapers.base import BaseScraper

logger = structlog.get_logger()


class GeneralAdmissionScraper(BaseScraper):
    """Scraper for general admission (non-seatsio) Tixr events"""

    def get_scenario_name(self) -> str:
        return "general_admission"

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_api_data: Optional[dict] = None,
    ) -> ScrapeResult:
        """Extract ticket categories from event API data."""

        if not event_api_data:
            self.add_error("No event API data available")
            return self.create_result(
                event_info=EventInfo(
                    event_id=self._extract_event_id_from_url(event_url),
                    event_name="Unknown Event",
                    venue_name="Unknown Venue",
                    event_url=event_url,
                ),
            )

        # Extract event info
        event_info = self.extract_event_info_from_api(event_api_data, event_url)

        # Extract ticket categories from sales[]
        ticket_categories = self._extract_sales(event_api_data)

        logger.info("ga_extraction_complete",
                    categories=len(ticket_categories),
                    event=event_info.event_name)

        # Build API URLs for re-querying
        api_urls = {
            'event_api': f"https://www.tixr.com/api/events/{event_api_data.get('id')}",
        }

        return self.create_result(
            event_info=event_info,
            ticket_categories=ticket_categories,
            api_urls=api_urls,
            raw_event_data=event_api_data,
        )

    def _extract_sales(self, event_data: dict) -> List[TicketCategory]:
        """
        Extract ticket categories from the sales[] array in event API response.

        Each sale has:
        - id, category (GA/VIP/PARKING/CAMPING), state (OPEN/QUEUED/SOLD_OUT/HIDE)
        - tiers[]: name, price (all-in), active, startDate, endDate
        - maxTicketsAllowed, deliveryType, seatingType, resaleState
        """
        categories = []
        sales = event_data.get('sales', []) or []

        for sale in sales:
            sale_id = sale.get('id', '')
            sale_name = sale.get('name', 'Unknown')
            sale_category = sale.get('category', '')
            sale_state = sale.get('state', 'UNKNOWN')
            max_tickets = sale.get('maxTicketsAllowed')
            delivery_type = sale.get('deliveryType')
            seating_type = sale.get('seatingType')

            tiers = sale.get('tiers', []) or []

            if not tiers:
                # Sale with no tiers — create a single category entry
                is_sold_out = sale_state in ('SOLD_OUT', 'HIDE', 'CLOSED')
                categories.append(TicketCategory(
                    category_name=sale_name,
                    category_id=str(sale_id),
                    price=Decimal('0'),
                    is_out_of_stock=is_sold_out,
                    ticket_type=sale_category,
                    sale_state=sale_state,
                    max_tickets=max_tickets,
                    delivery_type=delivery_type,
                    group_name=sale_category,
                ))
                continue

            # Find the active tier (or most relevant one)
            active_tiers = [t for t in tiers if t.get('active')]
            if not active_tiers:
                # No active tier — use all tiers
                active_tiers = tiers

            for tier in active_tiers:
                tier_name = tier.get('name', '')
                tier_price = tier.get('price', 0)

                # Combine sale name and tier name for display
                if tier_name and tier_name != sale_name:
                    display_name = f"{sale_name} - {tier_name}"
                else:
                    display_name = sale_name

                tier_id = tier.get('id', '')
                category_id = f"{sale_id}_{tier_id}" if tier_id else str(sale_id)

                is_sold_out = sale_state in ('SOLD_OUT', 'HIDE', 'CLOSED')

                categories.append(TicketCategory(
                    category_name=display_name,
                    category_id=category_id,
                    price=Decimal(str(tier_price)) if tier_price else Decimal('0'),
                    is_out_of_stock=is_sold_out,
                    ticket_type=sale_category,
                    sale_state=sale_state,
                    tier_name=tier_name,
                    max_tickets=max_tickets,
                    delivery_type=delivery_type,
                    group_name=sale_category,
                ))

        logger.info("sales_extracted",
                    total_sales=len(sales),
                    total_categories=len(categories),
                    open_count=sum(1 for c in categories if c.is_available))

        return categories
