"""
Scraper for PreKindle Scenario 2: General Admission / HTML table tickets

Data Source: Server-rendered HTML table at /find-tickets/id/{eventId} or /checkout/id/{eventId}
The checkout page contains a table with ticket categories, prices, and quantity dropdowns.
No API calls needed — everything is in the HTML.
"""

import re
from typing import Optional, List
from decimal import Decimal
import structlog
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from models import ScrapeResult, TicketCategory, EventInfo
from browser import CapturedResponse

logger = structlog.get_logger()

# Names that are delivery methods, not ticket categories — exclude from ticket_categories
DELIVERY_METHOD_NAMES = frozenset({
    'will call', 'print at home', 'mobile', 'mail', 'e-ticket', 'e-tickets',
    'will call pickup', 'print at home ticket', 'mobile ticket', 'mail ticket',
})


def _is_delivery_method_name(name: str) -> bool:
    """True if name is a delivery method label (e.g. Will Call), not a ticket type."""
    if not name or not name.strip():
        return False
    return name.strip().lower() in DELIVERY_METHOD_NAMES or any(
        d in name.strip().lower() for d in ('will call', 'print at home', 'mobile delivery', 'e-ticket')
    )


class GeneralAdmissionScraper(BaseScraper):
    """Scraper for PreKindle events with HTML table ticket selection."""

    def get_scenario_name(self) -> str:
        return "general_admission"

    async def extract(
        self,
        captured_responses: list[CapturedResponse],
        page_html: str,
        event_url: str,
        event_info_override: Optional[EventInfo] = None,
    ) -> ScrapeResult:
        """Extract ticket categories from HTML table."""

        logger.info("starting_general_admission_extraction")

        # Step 1: Get event info
        event_info = event_info_override or EventInfo(
            event_id=self._extract_event_id(event_url),
            event_name="Unknown Event",
            venue_name="Unknown Venue",
            event_url=event_url,
        )

        # Step 2: Parse ticket table from HTML
        categories = self._parse_ticket_table(page_html)

        if not categories:
            self.add_warning("No ticket categories found in HTML table — trying alternative parsing")
            categories = self._parse_ticket_table_alt(page_html)

        if not categories:
            self.add_error("Could not extract ticket categories from page HTML")

        # Step 3: Parse delivery methods
        delivery_methods = self._parse_delivery_methods(page_html)

        return self.create_result(
            event_info,
            ticket_categories=categories,
            scrape_method="html_parse",
            delivery_methods=delivery_methods,
        )

    def _parse_ticket_table(self, page_html: str) -> List[TicketCategory]:
        """
        Parse the "How many would you like?" ticket table.

        Each row typically has:
        - category name (e.g., "General Admission")
        - ticket type (e.g., "Ticket", "RSVP", "VIP")
        - price (e.g., "$46.51 Total Price")
        - quantity dropdown (<select> with options 0-N)
        """
        categories = []

        try:
            soup = BeautifulSoup(page_html, 'html.parser')

            # Find the ticket table — look for tables with select dropdowns
            tables = soup.find_all('table')

            for table in tables:
                rows = table.find_all('tr')

                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) < 2:
                        continue

                    # Try to extract data from cells
                    category = self._parse_ticket_row(cells)
                    if category:
                        categories.append(category)

            # Also try div-based layouts (some PreKindle pages use divs not tables)
            if not categories:
                categories = self._parse_div_ticket_layout(soup)

        except Exception as e:
            logger.error("ticket_table_parse_failed", error=str(e))

        logger.info("parsed_ticket_categories", count=len(categories))
        return categories

    def _parse_ticket_row(self, cells) -> Optional[TicketCategory]:
        """Parse a single row of the ticket table."""
        try:
            # Collect all text from cells
            cell_texts = [cell.get_text(strip=True) for cell in cells]

            # Find the category name (usually first non-empty cell)
            category_name = ''
            ticket_type = ''
            price = Decimal('0')
            max_qty = None
            is_sold_out = False

            for i, text in enumerate(cell_texts):
                if not text:
                    continue

                # Check for price pattern: "$XX.XX" or "$XX.XX Total Price"
                price_match = re.search(r'\$(\d+(?:\.\d{2})?)', text)
                if price_match and price == Decimal('0'):
                    price = Decimal(price_match.group(1))
                    continue

                # Check for common ticket type keywords
                text_lower = text.lower()
                if text_lower in ('ticket', 'rsvp', 'vip', 'early bird', 'general'):
                    if not ticket_type:
                        ticket_type = text
                    continue

                # Otherwise it's likely the category name
                if not category_name and len(text) > 1:
                    category_name = text

            # Find select dropdown in any cell for max quantity
            has_select = False
            for cell in cells:
                select = cell.find('select')
                if select:
                    has_select = True
                    options = select.find_all('option')
                    if options:
                        # Max quantity is the highest option value
                        max_vals = []
                        for opt in options:
                            val = opt.get('value', opt.get_text(strip=True))
                            try:
                                max_vals.append(int(val))
                            except (ValueError, TypeError):
                                continue
                        if max_vals:
                            max_qty = max(max_vals)
                            if max_qty == 0:
                                is_sold_out = True

                    # Check if select is disabled
                    if select.get('disabled') is not None:
                        is_sold_out = True

            # Filter out rows without select dropdowns (e.g., disclaimer rows)
            if not has_select:
                return None

            if not category_name:
                return None

            if _is_delivery_method_name(category_name):
                return None

            # If no explicit ticket type found, default
            if not ticket_type:
                ticket_type = 'Ticket'

            return TicketCategory(
                category_name=category_name,
                category_id=category_name.lower().replace(' ', '_'),
                price=price,
                current_stock=None,  # Dropdown max is UI limit, not actual inventory
                is_out_of_stock=is_sold_out,
                ticket_type=ticket_type,
            )

        except Exception as e:
            logger.debug("ticket_row_parse_failed", error=str(e))
            return None

    def _parse_div_ticket_layout(self, soup: BeautifulSoup) -> List[TicketCategory]:
        """
        Alternative parser for div-based ticket layouts.

        Some PreKindle pages use div structures instead of tables.
        Look for patterns with price text and select dropdowns.
        """
        categories = []

        try:
            # Look for howManyPanel (Wicket component name)
            panel = soup.find(id=re.compile(r'howMany', re.IGNORECASE))
            if not panel:
                # Try class-based search
                panel = soup.find(class_=re.compile(r'ticket|howmany', re.IGNORECASE))
            if not panel:
                panel = soup  # Search entire page

            # Find all select elements (quantity dropdowns)
            selects = panel.find_all('select')
            for select in selects:
                # Go up to find the containing row/div
                container = select.parent
                # Walk up a few levels to find meaningful text
                for _ in range(5):
                    if container is None:
                        break
                    text = container.get_text(strip=True)
                    if len(text) > 10:  # Found a container with enough text
                        break
                    container = container.parent

                if container is None:
                    continue

                full_text = container.get_text(' ', strip=True)

                # Extract price
                price = Decimal('0')
                price_match = re.search(r'\$(\d+(?:\.\d{2})?)', full_text)
                if price_match:
                    price = Decimal(price_match.group(1))

                # Extract category name — text before the price
                category_name = ''
                if price_match:
                    before_price = full_text[:price_match.start()].strip()
                    # Remove common noise
                    before_price = re.sub(r'\s+', ' ', before_price).strip()
                    if before_price:
                        category_name = before_price

                if not category_name:
                    category_name = "General Admission"

                # Extract ticket type from text
                ticket_type = 'Ticket'
                for tt in ['RSVP', 'VIP', 'Early Bird', 'Ticket']:
                    if tt.lower() in full_text.lower():
                        ticket_type = tt
                        break

                # Max quantity from select
                max_qty = None
                is_sold_out = False
                options = select.find_all('option')
                if options:
                    max_vals = []
                    for opt in options:
                        val = opt.get('value', opt.get_text(strip=True))
                        try:
                            max_vals.append(int(val))
                        except (ValueError, TypeError):
                            continue
                    if max_vals:
                        max_qty = max(max_vals)
                        if max_qty == 0:
                            is_sold_out = True

                if select.get('disabled') is not None:
                    is_sold_out = True

                if _is_delivery_method_name(category_name):
                    continue

                categories.append(TicketCategory(
                    category_name=category_name,
                    category_id=category_name.lower().replace(' ', '_'),
                    price=price,
                    current_stock=None,  # Dropdown max is UI limit, not actual inventory
                    is_out_of_stock=is_sold_out,
                    ticket_type=ticket_type,
                ))

        except Exception as e:
            logger.debug("div_ticket_parse_failed", error=str(e))

        return categories

    def _parse_ticket_table_alt(self, page_html: str) -> List[TicketCategory]:
        """
        Alternative parser that looks for price and select patterns directly in HTML.
        """
        categories = []

        try:
            soup = BeautifulSoup(page_html, 'html.parser')

            # Find all elements containing "$X.XX Total Price" pattern
            price_elements = soup.find_all(string=re.compile(r'\$\d+\.\d{2}'))

            for price_el in price_elements:
                price_text = price_el.strip()
                price_match = re.search(r'\$(\d+(?:\.\d{2})?)', price_text)
                if not price_match:
                    continue

                price = Decimal(price_match.group(1))

                # Walk up to find containing row
                parent = price_el.parent
                for _ in range(8):
                    if parent is None:
                        break
                    # Check if this container has a select dropdown
                    if parent.find('select'):
                        break
                    parent = parent.parent

                if parent is None:
                    continue

                # Get full text of the row
                row_text = parent.get_text(' ', strip=True)

                # Extract category name (text before price)
                cat_parts = row_text.split('$')[0].strip()
                category_name = re.sub(r'\s+', ' ', cat_parts).strip() or "General Admission"

                # Max quantity
                max_qty = None
                is_sold_out = False
                select = parent.find('select')
                if select:
                    options = select.find_all('option')
                    max_vals = []
                    for opt in options:
                        val = opt.get('value', opt.get_text(strip=True))
                        try:
                            max_vals.append(int(val))
                        except (ValueError, TypeError):
                            continue
                    if max_vals:
                        max_qty = max(max_vals)
                        if max_qty == 0:
                            is_sold_out = True

                # Detect ticket type
                ticket_type = 'Ticket'
                for tt in ['RSVP', 'VIP', 'Early Bird']:
                    if tt.lower() in row_text.lower():
                        ticket_type = tt
                        break

                if _is_delivery_method_name(category_name):
                    continue

                categories.append(TicketCategory(
                    category_name=category_name,
                    category_id=category_name.lower().replace(' ', '_'),
                    price=price,
                    current_stock=None,  # Dropdown max is UI limit, not actual inventory
                    is_out_of_stock=is_sold_out,
                    ticket_type=ticket_type,
                ))

        except Exception as e:
            logger.debug("alt_ticket_parse_failed", error=str(e))

        return categories

    def _parse_delivery_methods(self, page_html: str) -> Optional[List[str]]:
        """
        Parse delivery methods from the checkout page.

        Looks for "Print at Home", "Mobile", "Will Call", etc. in labels, spans,
        and table cells (td/th) so we catch delivery options shown in tables.
        """
        methods = []
        seen_lower = set()

        try:
            soup = BeautifulSoup(page_html, 'html.parser')
            delivery_keywords = ['print at home', 'mobile', 'will call', 'mail', 'e-ticket']

            def add_if_delivery(el) -> None:
                text = el.get_text(strip=True)
                if len(text) > 80:
                    return
                text_lower = text.lower()
                for keyword in delivery_keywords:
                    if keyword in text_lower:
                        if text_lower not in seen_lower:
                            seen_lower.add(text_lower)
                            methods.append(text)
                        return

            # Leaf-level label/span (original behavior)
            for el in soup.find_all(['label', 'span']):
                child_elements = el.find_all(['label', 'span', 'div'], recursive=False)
                if child_elements:
                    continue
                add_if_delivery(el)

            # Table cells (delivery section often uses a table: Will Call, Print at Home, etc.)
            # Only check first cell of each row to avoid capturing descriptions
            for el in soup.find_all(['td', 'th']):
                parent_row = el.find_parent('tr')
                if parent_row:
                    first_cell = parent_row.find(['td', 'th'])
                    if first_cell == el:  # Only process if this IS the first cell
                        add_if_delivery(el)
                else:
                    # Not in a table row, process as-is
                    add_if_delivery(el)

        except Exception as e:
            logger.debug("delivery_methods_parse_failed", error=str(e))

        return methods if methods else None
