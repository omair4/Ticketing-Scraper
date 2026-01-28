"""
Scraper for Embed Widget Scenario: embed.admitonelive.com/event/{slug}

This handles events that load ticket data directly in HTML, such as:
- Jade Eagleson event
- Events using embed.admitonelive.com widget

Data Sources:
1. HTML table structure with ticket selection form
2. JavaScript variables (EVENT, level_limits)
3. Google Tag Manager dataLayer (optional)
"""

import json
import re
from typing import Optional, Dict, Any, List
from decimal import Decimal
import structlog
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from models import ScrapeResult, EventInfo, TicketCategory
from browser import CapturedResponse
from utils.decoder import extract_event_id_from_url

logger = structlog.get_logger()


class EmbedWidgetScraper(BaseScraper):
    """
    Scraper for events using embed.admitonelive.com widget.
    
    This handles events like Jade Eagleson that load a ticketing widget
    in an iframe from embed.admitonelive.com/event/{slug}
    """
    
    def get_scenario_name(self) -> str:
        return "embed_widget"
    
    async def extract(self, captured_responses: list[CapturedResponse],
                     page_html: str, event_url: str) -> ScrapeResult:
        """Extract ticket categories from HTML and JavaScript variables"""
        
        logger.info("starting_embed_widget_extraction", 
                   response_count=len(captured_responses),
                   html_length=len(page_html) if page_html else 0)
        
        if not page_html:
            self.add_error("No HTML content available")
            event_info = await self._extract_event_info_from_html("", event_url)
            return self.create_result(event_info, ticket_categories=[])
        
        # Step 1: Extract event info from HTML and JavaScript
        event_info = await self._extract_event_info_from_html(page_html, event_url)
        
        # Step 2: Extract ticket categories from HTML table
        ticket_categories = await self._extract_ticket_categories_from_html(page_html)
        
        if not ticket_categories:
            self.add_warning("No ticket categories found in HTML")
        
        return self.create_result(event_info, ticket_categories=ticket_categories)
    
    async def _extract_event_info_from_html(self, page_html: str, event_url: str) -> EventInfo:
        """Extract event metadata from HTML and JavaScript variables"""
        
        event_name = "Unknown Event"
        venue_name = "Unknown Venue"
        venue_address = None
        event_date = None
        event_id = "unknown"
        
        if not page_html:
            return EventInfo(
                event_id=extract_event_id_from_url(event_url) or "unknown",
                event_name=event_name,
                venue_name=venue_name,
                event_url=event_url
            )
        
        try:
            soup = BeautifulSoup(page_html, 'html.parser')
            
            # Priority 1: Extract from JavaScript EVENT variable
            event_js_data = self._parse_javascript_variable(page_html, 'EVENT')
            if event_js_data and isinstance(event_js_data, dict):
                event_id = str(event_js_data.get('event_id', event_id))
                event_name = event_js_data.get('event', event_name)
                currency = event_js_data.get('currency', 'CAD')
                logger.debug("extracted_from_event_js", event_id=event_id, event_name=event_name)
            elif event_js_data:
                logger.debug("event_js_data_not_dict", type=type(event_js_data).__name__)
            
            # Priority 2: Extract from HTML structure
            # Event name from h1
            h1 = soup.find('h1', class_='showtitle')
            if h1:
                event_name = h1.get_text(strip=True)
            
            # Event date/time
            event_date_elem = soup.find('div', class_='event_date')
            if event_date_elem:
                event_date = event_date_elem.get_text(strip=True)
            
            # Venue name
            venue_name_elem = soup.find('span', class_='venuename')
            if venue_name_elem:
                venue_name = venue_name_elem.get_text(strip=True)
            
            # Venue address
            venue_info = soup.find('div', id='venue_info')
            if venue_info:
                # Look for address spans
                address_spans = venue_info.find_all('span')
                address_parts = []
                for span in address_spans:
                    text = span.get_text(strip=True)
                    # Skip venue name and other non-address text
                    if text and text != venue_name and len(text) > 5:
                        # Check if it looks like an address (contains numbers or common address words)
                        if any(char.isdigit() for char in text) or any(word in text.lower() for word in ['ave', 'st', 'street', 'road', 'rd']):
                            address_parts.append(text)
                if address_parts:
                    venue_address = ', '.join(address_parts)
            
            # Fallback: Try to extract event ID from URL if not found
            if event_id == "unknown":
                extracted_id = extract_event_id_from_url(event_url)
                if extracted_id:
                    event_id = extracted_id
                else:
                    # Try to extract from EVENT_ID JavaScript variable
                    event_id_js = self._parse_javascript_variable(page_html, 'EVENT_ID')
                    if event_id_js:
                        event_id = str(event_id_js).strip("'\"")
            
            logger.info("extracted_event_info",
                       event_id=event_id,
                       event_name=event_name,
                       venue_name=venue_name)
            
        except Exception as e:
            logger.error("event_info_extraction_failed", error=str(e), exc_info=True)
            # Ensure we have at least basic event info even if extraction failed
            if event_id == "unknown":
                event_id = extract_event_id_from_url(event_url) or "unknown"
        
        return EventInfo(
            event_id=event_id,
            event_name=event_name,
            venue_name=venue_name,
            venue_address=venue_address,
            event_date=event_date,
            event_url=event_url
        )
    
    async def _extract_ticket_categories_from_html(self, page_html: str) -> List[TicketCategory]:
        """Extract ticket categories from HTML table structure"""
        
        categories = []
        
        if not page_html:
            return categories
        
        try:
            soup = BeautifulSoup(page_html, 'html.parser')
            
            # Find ticket selection table
            # Look for table with id="ticket-selection-genad" or class="formtab"
            ticket_table = soup.find('table', id='ticket-selection-genad')
            if not ticket_table:
                ticket_table = soup.find('table', class_='formtab')
            
            if not ticket_table:
                logger.warning("ticket_table_not_found")
                return categories
            
            # Extract level_limits from JavaScript for quantity limits
            level_limits = self._parse_javascript_variable(page_html, 'level_limits') or {}
            
            # Find all ticket rows (tr with data-level-id attribute)
            ticket_rows = ticket_table.find_all('tr', attrs={'data-level-id': True})
            
            if not ticket_rows:
                logger.warning("no_ticket_rows_found")
                return categories
            
            logger.info("found_ticket_rows", count=len(ticket_rows))
            
            for row in ticket_rows:
                try:
                    # Extract level ID
                    level_id = row.get('data-level-id', '').strip()
                    if not level_id:
                        continue
                    
                    # Extract ticket name from product_name span
                    product_name_span = row.find('span', class_='product_name')
                    if not product_name_span:
                        continue
                    
                    # Clone the span and remove fee_disclosure spans to get clean text
                    product_name_clone = BeautifulSoup(str(product_name_span), 'html.parser').find('span', class_='product_name')
                    if product_name_clone:
                        # Remove fee_disclosure spans
                        for fee_span in product_name_clone.find_all('span', class_='fee_disclosure'):
                            fee_span.decompose()
                        category_name = product_name_clone.get_text(strip=True)
                    else:
                        # Fallback: extract text and clean manually
                        category_name = product_name_span.get_text(strip=True)
                        # Remove fee disclosure patterns
                        category_name = re.sub(r'\d+\.\d+\s*\$?\s*\+\s*\d+\.\d+\s*\$?\s*Service Fee', '', category_name).strip()
                    
                    # Clean up any extra whitespace
                    category_name = re.sub(r'\s+', ' ', category_name).strip()
                    
                    if not category_name:
                        continue
                    
                    # Extract pricing from fee_disclosure span
                    fee_disclosure = row.find('span', class_='fee_disclosure')
                    base_price = None
                    service_fee = None
                    
                    if fee_disclosure:
                        fee_text = fee_disclosure.get_text(strip=True)
                        # Format: "30.00 $ + 11.00 $ Service Fee"
                        # Or: "30.00 $ + 11.00 $ Service Fee"
                        price_match = re.search(r'([\d.]+)\s*\$?\s*\+\s*([\d.]+)\s*\$?\s*Service Fee', fee_text)
                        if price_match:
                            base_price = Decimal(price_match.group(1))
                            service_fee = Decimal(price_match.group(2))
                        else:
                            # Try simpler format: just base price
                            price_match = re.search(r'([\d.]+)\s*\$', fee_text)
                            if price_match:
                                base_price = Decimal(price_match.group(1))
                    
                    # Fallback: Extract from data attributes on select element
                    select_elem = row.find('select', class_='ticket-select')
                    if select_elem:
                        if base_price is None:
                            data_price = select_elem.get('data-price')
                            if data_price:
                                try:
                                    # Total price from data-price attribute
                                    total_price = Decimal(str(data_price))
                                    # If we have service fee, subtract it to get base price
                                    if service_fee:
                                        base_price = total_price - service_fee
                                    else:
                                        base_price = total_price
                                except:
                                    pass
                        
                        # Verify category name from data-name attribute
                        data_name = select_elem.get('data-name')
                        if data_name and not category_name:
                            category_name = data_name
                    
                    # Fallback: Extract total price from price-td span
                    if base_price is None:
                        price_td = row.find('td', class_='price-td')
                        if price_td:
                            price_span = price_td.find('span')
                            if price_span:
                                price_text = price_span.get_text(strip=True)
                                # Extract number from text like "41.00 CAD $"
                                price_match = re.search(r'([\d.]+)', price_text)
                                if price_match:
                                    total_price = Decimal(price_match.group(1))
                                    if service_fee:
                                        base_price = total_price - service_fee
                                    else:
                                        base_price = total_price
                    
                    if base_price is None:
                        logger.warning("could_not_extract_price", level_id=level_id, category_name=category_name)
                        continue
                    
                    # Extract quantity limits from level_limits JavaScript variable
                    current_stock = None
                    is_out_of_stock = False
                    
                    if level_id in level_limits:
                        limits = level_limits[level_id]
                        max_qty = limits.get('max')
                        if max_qty:
                            try:
                                current_stock = int(max_qty)
                            except:
                                pass
                    
                    # Check for sold out indicators in HTML
                    # Look for text like "SOLD OUT" or "-- SOLD OUT --"
                    row_text = row.get_text()
                    if 'sold out' in row_text.lower() or 'sales closed' in row_text.lower():
                        is_out_of_stock = True
                        current_stock = 0
                    
                    # Create ticket category
                    category = TicketCategory(
                        category_name=category_name,
                        category_id=str(level_id),
                        price=base_price,
                        service_fee=service_fee,
                        current_stock=current_stock,
                        is_out_of_stock=is_out_of_stock
                    )
                    
                    categories.append(category)
                    logger.debug("extracted_ticket_category",
                               name=category_name,
                               level_id=level_id,
                               price=float(base_price),
                               service_fee=float(service_fee) if service_fee else None,
                               stock=current_stock,
                               is_available=category.is_available)
                    
                except Exception as e:
                    logger.warning("ticket_row_extraction_failed", 
                                 error=str(e),
                                 row_html=str(row)[:200])
                    continue
            
            logger.info("extracted_ticket_categories", count=len(categories))
            
        except Exception as e:
            logger.error("ticket_category_extraction_failed", error=str(e))
            self.add_error(f"Failed to extract ticket categories: {str(e)}")
        
        return categories
    
    def _parse_javascript_variable(self, html: str, var_name: str) -> Optional[Any]:
        """
        Parse a JavaScript variable value from HTML script tags.
        
        Examples:
        - var EVENT = {"event_id":"10125856",...};
        - var level_limits = {"50210056":{"min":0,"max":"8",...}};
        - var EVENT_ID = '10125856';
        
        Returns:
            Parsed value (dict, list, string, number, etc.) or None
        """
        try:
            # Find script tags that might contain the variable
            soup = BeautifulSoup(html, 'html.parser')
            scripts = soup.find_all('script', type=lambda x: x in ['text/javascript', None])
            
            for script in scripts:
                if not script.string:
                    continue
                script_text = script.string
                
                # Look for the variable assignment in this script
                pattern = rf'var\s+{var_name}\s*=\s*'
                match = re.search(pattern, script_text, re.IGNORECASE)
                if not match:
                    continue
                
                # Extract the value after the = sign
                start_pos = match.end()
                remaining = script_text[start_pos:]
                
                # Try to find balanced braces/brackets
                if remaining.strip().startswith('{'):
                    # Find matching closing brace
                    brace_count = 0
                    in_string = False
                    escape_next = False
                    string_char = None
                    value_str = ""
                    
                    for char in remaining:
                        if escape_next:
                            escape_next = False
                            value_str += char
                            continue
                        
                        if char == '\\':
                            escape_next = True
                            value_str += char
                            continue
                        
                        if not in_string and (char == '"' or char == "'"):
                            in_string = True
                            string_char = char
                            value_str += char
                            continue
                        
                        if in_string and char == string_char:
                            in_string = False
                            string_char = None
                            value_str += char
                            continue
                        
                        if not in_string:
                            if char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                            value_str += char
                            if brace_count == 0:
                                break
                        else:
                            value_str += char
                    
                    value_str = value_str.strip()
                    break
                elif remaining.strip().startswith('['):
                    # Find matching closing bracket
                    bracket_count = 0
                    in_string = False
                    escape_next = False
                    string_char = None
                    value_str = ""
                    
                    for char in remaining:
                        if escape_next:
                            escape_next = False
                            value_str += char
                            continue
                        
                        if char == '\\':
                            escape_next = True
                            value_str += char
                            continue
                        
                        if not in_string and (char == '"' or char == "'"):
                            in_string = True
                            string_char = char
                            value_str += char
                            continue
                        
                        if in_string and char == string_char:
                            in_string = False
                            string_char = None
                            value_str += char
                            continue
                        
                        if not in_string:
                            if char == '[':
                                bracket_count += 1
                            elif char == ']':
                                bracket_count -= 1
                            value_str += char
                            if bracket_count == 0:
                                break
                        else:
                            value_str += char
                    
                    value_str = value_str.strip()
                    break
                else:
                    # Simple value (string or number)
                    # Extract until semicolon
                    semicolon_pos = remaining.find(';')
                    if semicolon_pos >= 0:
                        value_str = remaining[:semicolon_pos].strip()
                        break
            
            # Fallback: try regex pattern matching
            if not value_str:
                pattern = rf'var\s+{var_name}\s*=\s*([^;]+);'
                match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
                if match:
                    value_str = match.group(1).strip()
                else:
                    return None
            
            # Remove trailing || {} or || [] patterns
            value_str = re.sub(r'\s*\|\|\s*\{\}\s*$', '', value_str)
            value_str = re.sub(r'\s*\|\|\s*\[\]\s*$', '', value_str)
            value_str = value_str.strip()
            
            # Try to parse as JSON (for objects and arrays)
            if value_str.startswith('{') or value_str.startswith('['):
                try:
                    # First try direct JSON parsing
                    return json.loads(value_str)
                except json.JSONDecodeError as e:
                    # JSON might have unescaped quotes or HTML entities in strings
                    # Try to extract just the JSON part by finding balanced braces
                    if value_str.startswith('{'):
                        # Find the matching closing brace (handle nested braces in strings)
                        brace_count = 0
                        in_string = False
                        escape_next = False
                        string_char = None
                        end_pos = -1
                        
                        for i, char in enumerate(value_str):
                            if escape_next:
                                escape_next = False
                                continue
                            
                            if char == '\\':
                                escape_next = True
                                continue
                            
                            if not in_string and (char == '"' or char == "'"):
                                in_string = True
                                string_char = char
                            elif in_string and char == string_char:
                                in_string = False
                                string_char = None
                            elif not in_string:
                                if char == '{':
                                    brace_count += 1
                                elif char == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        end_pos = i + 1
                                        break
                        
                        if end_pos > 0:
                            json_str = value_str[:end_pos]
                            try:
                                return json.loads(json_str)
                            except json.JSONDecodeError as e2:
                                logger.debug("json_parse_failed_after_brace_extraction", 
                                           error=str(e2),
                                           json_str=json_str[:200])
                    elif value_str.startswith('['):
                        # Find the matching closing bracket
                        bracket_count = 0
                        in_string = False
                        escape_next = False
                        string_char = None
                        end_pos = -1
                        
                        for i, char in enumerate(value_str):
                            if escape_next:
                                escape_next = False
                                continue
                            
                            if char == '\\':
                                escape_next = True
                                continue
                            
                            if not in_string and (char == '"' or char == "'"):
                                in_string = True
                                string_char = char
                            elif in_string and char == string_char:
                                in_string = False
                                string_char = None
                            elif not in_string:
                                if char == '[':
                                    bracket_count += 1
                                elif char == ']':
                                    bracket_count -= 1
                                    if bracket_count == 0:
                                        end_pos = i + 1
                                        break
                        
                        if end_pos > 0:
                            json_str = value_str[:end_pos]
                            try:
                                return json.loads(json_str)
                            except json.JSONDecodeError as e2:
                                logger.debug("json_parse_failed_after_bracket_extraction",
                                           error=str(e2),
                                           json_str=json_str[:200])
                    logger.debug("json_parse_failed", 
                               var_name=var_name,
                               error=str(e),
                               value_preview=value_str[:200])
            
            # Try to parse as string (quoted)
            if (value_str.startswith("'") and value_str.endswith("'")) or \
               (value_str.startswith('"') and value_str.endswith('"')):
                return value_str[1:-1]
            
            # Try to parse as number
            try:
                if '.' in value_str:
                    return float(value_str)
                else:
                    return int(value_str)
            except ValueError:
                pass
            
            # Return as string if nothing else works
            return value_str
            
        except Exception as e:
            logger.debug("javascript_variable_parse_failed", 
                        var_name=var_name, 
                        error=str(e))
            return None
