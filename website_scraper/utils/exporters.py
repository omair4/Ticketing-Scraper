"""
Export utilities for saving event scrape results
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import structlog

from models import EventsScrapeResult, EventSummary

logger = structlog.get_logger()


def sanitize_filename(name: str) -> str:
    """Create a safe filename"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "_")
    return name[:100].strip()


def generate_filename(prefix: str, extension: str) -> str:
    """Generate a timestamped filename"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = sanitize_filename(prefix)
    return f"{safe_prefix}_{timestamp}.{extension}"


def _serialize_for_json(obj):
    """Convert objects for JSON serialization"""
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return _serialize_for_json(obj.model_dump(mode="json"))
    return obj


def export_events_to_json(result: EventsScrapeResult, output_dir: str = "./output") -> str:
    """
    Export events result to JSON file.

    Returns:
        Path to created file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = generate_filename("events", "json")
    filepath = output_path / filename

    data = result.model_dump(mode="json")
    data = _serialize_for_json(data)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    logger.info("exported_json", filepath=str(filepath), event_count=len(result.events))
    return str(filepath)


def export_events_to_csv(result: EventsScrapeResult, output_dir: str = "./output") -> str:
    """
    Export events to CSV file.

    Returns:
        Path to created file
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = generate_filename("events", "csv")
    filepath = output_path / filename

    fieldnames = [
        "mongo_event_id",
        "event_id",
        "name",
        "artist_name",
        "subtitle",
        "date",
        "venue_name",
        "city",
        "category",
        "event_type",
        "url",
        "image_url",
        "price_range",
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for event in result.events:
            row = event.model_dump(mode="json")
            row["date"] = event.date.isoformat() if event.date else ""
            writer.writerow(row)

    logger.info("exported_csv", filepath=str(filepath), event_count=len(result.events))
    return str(filepath)


def export_events(
    result: EventsScrapeResult,
    output_dir: str = "./output",
    formats: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Export events to multiple formats.

    Args:
        result: Events scrape result
        output_dir: Directory for output files
        formats: List of formats ('json', 'csv'). Default: both

    Returns:
        Dict mapping format to filepath
    """
    if formats is None:
        formats = ["json", "csv"]

    exported: Dict[str, str] = {}

    if "json" in formats:
        exported["json"] = export_events_to_json(result, output_dir)

    if "csv" in formats:
        exported["csv"] = export_events_to_csv(result, output_dir)

    return exported


def print_summary(result: EventsScrapeResult):
    """Print a human-readable summary of the scrape result"""
    print("\n" + "=" * 60)
    print("EVENTS SCRAPE RESULT SUMMARY")
    print("=" * 60)
    print(f"Total Events: {result.total_events}")
    print(f"Pro Events: {result.pro_events}")
    print(f"Community Events: {result.community_events}")
    print(f"Cities Covered: {', '.join(result.cities_covered)}")
    print("-" * 60)

    if result.errors:
        print(f"Errors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")

    if result.warnings:
        print(f"Warnings ({len(result.warnings)}):")
        for warn in result.warnings:
            print(f"  - {warn}")

    if result.events:
        print(f"\nSample Events (first 5):")
        for event in result.events[:5]:
            print(f"  {event.name} | {event.venue_name} | {event.city} | {event.event_type}")

    print("=" * 60 + "\n")
