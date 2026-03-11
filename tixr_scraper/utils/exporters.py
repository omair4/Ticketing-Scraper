"""
Export utilities for saving scrape results to JSON and CSV
"""

import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
import structlog

from models import ScrapeResult

logger = structlog.get_logger()


def sanitize_filename(name: str) -> str:
    """Create a safe filename from event name"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name[:100].strip()


def generate_filename(event_name: str, extension: str) -> str:
    """Generate a timestamped filename"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = sanitize_filename(event_name)
    return f"{safe_name}_{timestamp}.{extension}"


def _convert_for_json(obj):
    """Convert Decimal to float and remove None values for JSON serialization"""
    if isinstance(obj, dict):
        return {k: _convert_for_json(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_convert_for_json(item) for item in obj]
    elif isinstance(obj, bool):
        return obj
    elif hasattr(obj, '__float__'):
        return float(obj)
    return obj


def export_to_json(result: ScrapeResult, output_dir: str = "./output") -> str:
    """Export scrape result to JSON file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = generate_filename(result.event.event_name, "json")
    filepath = output_path / filename

    data = result.model_dump(mode='json')
    data = _convert_for_json(data)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    logger.info("exported_json", filepath=str(filepath),
                seat_count=len(result.seats),
                category_count=len(result.ticket_categories))
    return str(filepath)


def export_to_csv(result: ScrapeResult, output_dir: str = "./output") -> str:
    """Export seats and ticket categories to CSV file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = generate_filename(result.event.event_name, "csv")
    filepath = output_path / filename

    fieldnames = [
        'type',
        'category_name',
        'section',
        'row',
        'seat',
        'x',
        'y',
        'ADA',
        'price',
        'service_fee',
        'total_price',
        'status',
        'quantity',
        'current_stock',
        'is_out_of_stock',
        'price_level',
        'ticket_type',
        'sale_state',
        'tier_name',
        'max_tickets',
        'delivery_type',
        'event_name',
        'venue',
        'event_date',
    ]

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for seat in result.seats:
            writer.writerow({
                'type': 'seat',
                'category_name': '',
                'section': seat.section,
                'row': seat.row,
                'seat': seat.seat,
                'x': seat.x if seat.x is not None else '',
                'y': seat.y if seat.y is not None else '',
                'ADA': seat.ada if seat.ada is not None else '',
                'price': float(seat.price),
                'service_fee': float(seat.service_fee) if seat.service_fee else '',
                'total_price': float(seat.total_price),
                'status': seat.status.value,
                'quantity': '',
                'current_stock': '',
                'is_out_of_stock': '',
                'price_level': seat.price_level or '',
                'ticket_type': '',
                'sale_state': '',
                'tier_name': '',
                'max_tickets': '',
                'delivery_type': '',
                'event_name': result.event.event_name,
                'venue': result.event.venue_name,
                'event_date': result.event.event_date or '',
            })

        for category in result.ticket_categories:
            writer.writerow({
                'type': 'ticket_category',
                'category_name': category.category_name,
                'section': '',
                'row': '',
                'seat': '',
                'x': '',
                'y': '',
                'ADA': '',
                'price': float(category.price),
                'service_fee': float(category.service_fee) if category.service_fee else '',
                'total_price': float(category.total_price),
                'status': 'available' if category.is_available else 'out_of_stock',
                'quantity': category.current_stock if category.current_stock is not None else '',
                'current_stock': category.current_stock if category.current_stock is not None else '',
                'is_out_of_stock': category.is_out_of_stock,
                'price_level': category.group_name or category.category_name,
                'ticket_type': category.ticket_type or '',
                'sale_state': category.sale_state or '',
                'tier_name': category.tier_name or '',
                'max_tickets': category.max_tickets if category.max_tickets is not None else '',
                'delivery_type': category.delivery_type or '',
                'event_name': result.event.event_name,
                'venue': result.event.venue_name,
                'event_date': result.event.event_date or '',
            })

    total_items = len(result.seats) + len(result.ticket_categories)
    logger.info("exported_csv", filepath=str(filepath),
                seat_count=len(result.seats),
                category_count=len(result.ticket_categories),
                total_items=total_items)
    return str(filepath)


def export_results(result: ScrapeResult, output_dir: str = "./output",
                   formats: list[str] = None) -> dict[str, str]:
    """Export results to multiple formats."""
    if formats is None:
        formats = ['json', 'csv']

    exported = {}
    if 'json' in formats:
        exported['json'] = export_to_json(result, output_dir)
    if 'csv' in formats:
        exported['csv'] = export_to_csv(result, output_dir)

    return exported


def print_summary(result: ScrapeResult):
    """Print a human-readable summary of the scrape result"""
    print("\n" + "=" * 60)
    print("TIXR SCRAPE RESULT SUMMARY")
    print("=" * 60)
    print(f"Event: {result.event.event_name}")
    print(f"Venue: {result.event.venue_name}")
    if result.event.venue_address:
        print(f"Address: {result.event.venue_address}")
    print(f"Date: {result.event.event_date or 'N/A'}")
    if result.event.doors_date:
        print(f"Doors: {result.event.doors_date}")
    if result.event.group_name:
        print(f"Group: {result.event.group_name}")
    if result.event.event_category:
        print(f"Category: {result.event.event_category}")
    print(f"Scenario: {result.scenario_type}")
    print(f"Method: {result.scrape_method}")
    print("-" * 60)

    if result.seats:
        print(f"Total Seats Found: {result.total_seats_found}")
        print(f"Available Seats: {result.available_seats_count}")
    if result.ticket_categories:
        available_categories = sum(1 for c in result.ticket_categories if c.is_available)
        print(f"Ticket Categories: {len(result.ticket_categories)} (Available: {available_categories})")
    if result.min_price and result.max_price:
        print(f"Price Range: ${result.min_price:.2f} - ${result.max_price:.2f}")
    print("-" * 60)

    if result.errors:
        print(f"Errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")

    if result.warnings:
        print(f"Warnings ({len(result.warnings)}):")
        for warn in result.warnings:
            print(f"  - {warn}")

    if result.seats:
        print("\nSample Seats (first 5):")
        for seat in result.seats[:5]:
            print(f"  {seat.to_display_string()} - ${seat.total_price:.2f} ({seat.status.value})")

    if result.ticket_categories:
        print(f"\nTicket Categories ({len(result.ticket_categories)}):")
        for category in result.ticket_categories:
            stock_info = f" (Stock: {category.current_stock})" if category.current_stock is not None else ""
            type_info = f" [{category.ticket_type}]" if category.ticket_type else ""
            state_info = f" ({category.sale_state})" if category.sale_state else ""
            print(f"  {category.category_name}{type_info} - ${category.total_price:.2f}{stock_info}{state_info}")

    if result.event.lineup:
        print(f"\nLineup ({len(result.event.lineup)}):")
        for act in result.event.lineup[:10]:
            print(f"  {act.get('name', 'Unknown')} ({act.get('type', '')})")

    if result.api_urls:
        print("\nAPI URLs (for direct re-querying):")
        for key, val in result.api_urls.items():
            print(f"  {key}: {val}")

    print("=" * 60 + "\n")
