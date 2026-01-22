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
    
    # Convert Decimal to float for JSON serialization
    def convert_decimals(obj):
        if isinstance(obj, dict):
            return {k: convert_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_decimals(item) for item in obj]
        elif hasattr(obj, '__float__'):
            return float(obj)
        return obj
    
    data = convert_decimals(data)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    logger.info("exported_json", filepath=str(filepath), seat_count=len(result.seats))
    return str(filepath)


def export_to_csv(result: ScrapeResult, output_dir: str = "./output") -> str:
    """
    Export seats to CSV file.
    
    Returns:
        Path to created file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    filename = generate_filename(result.event.event_name, "csv")
    filepath = output_path / filename
    
    # Define CSV columns
    fieldnames = [
        'section',
        'row', 
        'seat',
        'price',
        'service_fee',
        'total_price',
        'status',
        'price_level',
        'event_name',
        'venue',
        'event_date'
    ]
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for seat in result.seats:
            writer.writerow({
                'section': seat.section,
                'row': seat.row,
                'seat': seat.seat,
                'price': float(seat.price),
                'service_fee': float(seat.service_fee) if seat.service_fee else '',
                'total_price': float(seat.total_price),
                'status': seat.status.value,
                'price_level': seat.price_level or '',
                'event_name': result.event.event_name,
                'venue': result.event.venue_name,
                'event_date': result.event.event_date or ''
            })
    
    logger.info("exported_csv", filepath=str(filepath), seat_count=len(result.seats))
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
    print(f"Total Seats Found: {result.total_seats_found}")
    print(f"Available Seats: {result.available_seats_count}")
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
    
    print("="*60 + "\n")
