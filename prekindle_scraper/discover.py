#!/usr/bin/env python3
"""
PreKindle Event Discovery - Fetch all events from the search API

Usage:
    python discover.py
    python discover.py --limit 50 --page 0
    python discover.py --output-dir ./output --format json csv

Examples:
    python discover.py                          # Fetch all events
    python discover.py --limit 12               # First 12 results
    python discover.py --max-pages 1            # First page only
    python discover.py --format json --output-dir ./output
"""

import asyncio
import argparse
import json
import csv
import sys
from pathlib import Path
from datetime import datetime
import structlog

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

from config import ScraperConfig
from discovery.event_search import PreKindleEventSearch
from models import PreKindleEventSummary

logger = structlog.get_logger()


def export_events_json(events: list[PreKindleEventSummary], output_dir: str) -> str:
    """Export events to JSON file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"prekindle_events_{timestamp}.json"
    filepath = output_path / filename

    data = {
        "discovered_at": datetime.utcnow().isoformat(),
        "total_events": len(events),
        "events": [e.model_dump(mode='json') for e in events],
    }

    # Remove None values
    def remove_none(obj):
        if isinstance(obj, dict):
            return {k: remove_none(v) for k, v in obj.items() if v is not None}
        elif isinstance(obj, list):
            return [remove_none(i) for i in obj]
        return obj

    data = remove_none(data)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    return str(filepath)


def export_events_csv(events: list[PreKindleEventSummary], output_dir: str) -> str:
    """Export events to CSV file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"prekindle_events_{timestamp}.csv"
    filepath = output_path / filename

    fieldnames = [
        'event_id',
        'title',
        'subtitle',
        'organizer_name',
        'performers',
        'start_time',
        'details_primary',
        'details_secondary',
        'ticket_url',
        'thumbnail_url',
        'is_sold_out',
    ]

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for event in events:
            writer.writerow({
                'event_id': event.event_id,
                'title': event.title,
                'subtitle': event.subtitle or '',
                'organizer_name': event.organizer_name or '',
                'performers': ', '.join(event.performers) if event.performers else '',
                'start_time': event.start_time or '',
                'details_primary': event.details_primary or '',
                'details_secondary': event.details_secondary or '',
                'ticket_url': event.ticket_url or '',
                'thumbnail_url': event.thumbnail_url or '',
                'is_sold_out': event.is_sold_out,
            })

    return str(filepath)


async def main():
    """Main CLI entry point for event discovery"""
    parser = argparse.ArgumentParser(
        description='Discover events from PreKindle search API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Fetch all events
  %(prog)s --limit 50               # 50 results per page
  %(prog)s --max-pages 1            # First page only
  %(prog)s --format json csv        # Export both formats
        """
    )

    parser.add_argument('--output-dir', '-o', default='./output',
                        help='Directory for output files (default: ./output)')
    parser.add_argument('--format', '-f', nargs='+', choices=['json', 'csv'],
                        default=['json', 'csv'],
                        help='Output format(s) (default: json csv)')
    parser.add_argument('--limit', type=int, default=100,
                        help='Results per page (default: 100)')
    parser.add_argument('--max-pages', type=int, default=None,
                        help='Maximum pages to fetch (default: all)')
    parser.add_argument('--page', type=int, default=0,
                        help='Starting page (0-indexed, default: 0)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    config = ScraperConfig(
        search_limit=args.limit,
        output_dir=args.output_dir,
    )

    try:
        print("=" * 60)
        print("PREKINDLE EVENT DISCOVERY")
        print("=" * 60)

        search = PreKindleEventSearch(config)
        events = await search.fetch_all_events(
            limit=args.limit,
            max_pages=args.max_pages,
            start_page=args.page,
        )

        print(f"\nTotal events found: {len(events)}")

        sold_out = sum(1 for e in events if e.is_sold_out)
        available = len(events) - sold_out
        print(f"Available: {available}, Sold Out: {sold_out}")

        # Show sample events
        if events:
            print(f"\nSample events (first 5):")
            for event in events[:5]:
                status = "[SOLD OUT]" if event.is_sold_out else ""
                print(f"  {event.title} {status}")
                if event.details_secondary:
                    print(f"    {event.details_secondary}")
                if event.start_time:
                    print(f"    {event.start_time}")

        # Export
        exported = {}
        if 'json' in args.format:
            exported['json'] = export_events_json(events, args.output_dir)
        if 'csv' in args.format:
            exported['csv'] = export_events_csv(events, args.output_dir)

        if exported:
            print(f"\nExported files:")
            for fmt, filepath in exported.items():
                print(f"  {fmt.upper()}: {filepath}")

        print("=" * 60)
        return 0

    except KeyboardInterrupt:
        print("\nDiscovery cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("discovery_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
