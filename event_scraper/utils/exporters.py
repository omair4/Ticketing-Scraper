"""
Export utilities for saving scrape results to JSON and CSV
"""

import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
import structlog

from models import ScrapeResult, Seat

logger = structlog.get_logger()


def sanitize_filename(name: str) -> str:
    """Create a safe filename from event name"""
    # Remove/replace invalid characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    # Limit length and strip
    return name[:100].strip()


def generate_filename(event_name: str, extension: str) -> str:
    """Generate a timestamped filename"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = sanitize_filename(event_name)
    return f"{safe_name}_{timestamp}.{extension}"


def export_to_json(result: ScrapeResult, output_dir: str = "./output") -> str:
    """
    Export scrape result to JSON file.
    
    Returns:
        Path to created file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    filename = generate_filename(result.event.event_name, "json")
    filepath = output_path / filename
    
    # Convert to dict with proper serialization
    data = result.model_dump(mode='json')
    
    # Convert Decimal to float for JSON serialization and remove None values
    def convert_decimals_and_remove_none(obj):
        if isinstance(obj, dict):
            # Remove None values and recursively process remaining values
            return {k: convert_decimals_and_remove_none(v) 
                   for k, v in obj.items() if v is not None}
        elif isinstance(obj, list):
            return [convert_decimals_and_remove_none(item) for item in obj]
        elif isinstance(obj, bool):
            # Preserve booleans as-is (don't convert to float)
            return obj
        elif hasattr(obj, '__float__'):
            # Convert Decimal and other numeric types to float
            return float(obj)
        return obj
    
    data = convert_decimals_and_remove_none(data)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    logger.info("exported_json", filepath=str(filepath), seat_count=len(result.seats))
    return str(filepath)


def export_to_csv(result: ScrapeResult, output_dir: str = "./output") -> str:
    """
    Export seats, ticket categories, and resale listings to CSV file.
    
    Returns:
        Path to created file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    filename = generate_filename(result.event.event_name, "csv")
    filepath = output_path / filename
    
    # Define CSV columns - unified format for all types
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
        'event_name',
        'venue',
        'event_date'
    ]
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        # Export seats
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
                'event_name': result.event.event_name,
                'venue': result.event.venue_name,
                'event_date': result.event.event_date or ''
            })
        
        # Export ticket categories
        for category in result.ticket_categories:
            writer.writerow({
                'type': 'ticket_category',
                'category_name': category.category_name,
                'section': '',
                'row': '',
                'seat': '',
                'price': float(category.price),
                'service_fee': float(category.service_fee) if category.service_fee else '',
                'total_price': float(category.total_price),
                'status': 'available' if category.is_available else 'out_of_stock',
                'quantity': category.current_stock if category.current_stock is not None else '',
                'current_stock': category.current_stock if category.current_stock is not None else '',
                'is_out_of_stock': category.is_out_of_stock,
                'price_level': category.group_name or category.category_name,
                'event_name': result.event.event_name,
                'venue': result.event.venue_name,
                'event_date': result.event.event_date or ''
            })
        
        # Export resale listings
        for listing in result.resale_listings:
            writer.writerow({
                'type': 'resale_listing',
                'category_name': listing.ticket_category,
                'section': '',
                'row': '',
                'seat': '',
                'price': float(listing.price_per_ticket),
                'service_fee': float(listing.service_fee_per_ticket) if listing.service_fee_per_ticket else '',
                'total_price': float(listing.total_price_per_ticket),
                'status': 'available',
                'quantity': listing.quantity,
                'current_stock': '',
                'is_out_of_stock': '',
                'price_level': listing.ticket_category,
                'event_name': result.event.event_name,
                'venue': result.event.venue_name,
                'event_date': result.event.event_date or ''
            })
    
    total_items = len(result.seats) + len(result.ticket_categories) + len(result.resale_listings)
    logger.info("exported_csv", filepath=str(filepath), 
               seat_count=len(result.seats),
               category_count=len(result.ticket_categories),
               resale_count=len(result.resale_listings),
               total_items=total_items)
    return str(filepath)


def export_external_redirect(result: ScrapeResult, output_dir: str = "./output") -> str:
    """
    Export external redirect result to JSON file.

    Args:
        result: ScrapeResult with external_redirect_url set
        output_dir: Directory for output files

    Returns:
        Path to created file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = generate_filename(result.event.event_name, "json")
    filepath = output_path / filename

    data = {
        "scenario_type": result.scenario_type,
        "external_ticket_url": result.external_redirect_url,
        "event": {
            "event_id": result.event.event_id,
            "event_name": result.event.event_name,
            "venue_name": result.event.venue_name,
            "venue_address": result.event.venue_address,
            "event_date": result.event.event_date,
            "event_url": result.event.event_url,
        },
        "scraped_at": result.scraped_at.isoformat(),
        "warnings": result.warnings,
    }

    # Remove None values
    data["event"] = {k: v for k, v in data["event"].items() if v is not None}

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    logger.info("exported_external_redirect", filepath=str(filepath))
    return str(filepath)


def export_results(result: ScrapeResult, output_dir: str = "./output",
                   formats: list[str] = None) -> dict[str, str]:
    """
    Export results to multiple formats.
    
    Args:
        result: Scrape result to export
        output_dir: Directory for output files
        formats: List of formats ('json', 'csv'). Default: both
    
    Returns:
        Dict mapping format to filepath
    """
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
    print("\n" + "="*60)
    print(f"SCRAPE RESULT SUMMARY")
    print("="*60)
    print(f"Event: {result.event.event_name}")
    print(f"Venue: {result.event.venue_name}")
    print(f"Date: {result.event.event_date or 'N/A'}")
    print(f"Scenario: {result.scenario_type}")
    print("-"*60)
    if result.scenario_type == 'external_redirect':
        print(f"External Ticket URL: {result.external_redirect_url}")
        print("-"*60)
        if result.warnings:
            print(f"Warnings ({len(result.warnings)}):")
            for warn in result.warnings:
                print(f"  - {warn}")
        print("="*60 + "\n")
        return
    if result.seats:
        print(f"Total Seats Found: {result.total_seats_found}")
        print(f"Available Seats: {result.available_seats_count}")
    if result.ticket_categories:
        available_categories = sum(1 for c in result.ticket_categories if c.is_available)
        print(f"Ticket Categories: {len(result.ticket_categories)} (Available: {available_categories})")
    if result.resale_listings:
        total_resale_tickets = sum(l.quantity for l in result.resale_listings)
        print(f"Resale Listings: {len(result.resale_listings)} (Total Tickets: {total_resale_tickets})")
    if result.min_price and result.max_price:
        print(f"Price Range: ${result.min_price:.2f} - ${result.max_price:.2f}")
    print("-"*60)
    
    if result.errors:
        print(f"Errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")
    
    if result.warnings:
        print(f"Warnings ({len(result.warnings)}):")
        for warn in result.warnings:
            print(f"  - {warn}")
    
    # Show sample seats
    if result.seats:
        print("\nSample Seats (first 5):")
        for seat in result.seats[:5]:
            print(f"  {seat.to_display_string()} - ${seat.total_price:.2f} ({seat.status.value})")
    
    # Show ticket categories
    if result.ticket_categories:
        print(f"\nTicket Categories ({len(result.ticket_categories)}):")
        for category in result.ticket_categories:
            stock_info = f" (Stock: {category.current_stock})" if category.current_stock is not None else ""
            print(f"  {category.category_name} - ${category.total_price:.2f}{stock_info}")
    
    # Show resale listings
    if result.resale_listings:
        print(f"\nResale Listings ({len(result.resale_listings)}):")
        for listing in result.resale_listings[:5]:
            print(f"  {listing.ticket_category} - ${listing.total_price_per_ticket:.2f} per ticket (Qty: {listing.quantity})")
        if len(result.resale_listings) > 5:
            print(f"  ... and {len(result.resale_listings) - 5} more")
    
    print("="*60 + "\n")
