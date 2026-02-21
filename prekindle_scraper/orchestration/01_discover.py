#!/usr/bin/env python3
"""
Script 1: Event Discovery

Discovers all PreKindle events and initializes state database for bulk scraping.

Usage:
    python orchestration/01_discover.py
    python orchestration/01_discover.py --limit 50 --max-pages 5
    python orchestration/01_discover.py --output state/events_discovered.json
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, UTC

# Ensure parent package (prekindle_scraper/) is on sys.path for bare imports
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

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
from orchestration.state_manager import StateManager
from orchestration.config import OrchestrationConfig, DEFAULT_ORCHESTRATION_CONFIG

logger = structlog.get_logger()


def save_event_list(events: list, output_path: Path):
    """Save discovered events to JSON file"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "discovered_at": datetime.now(UTC).isoformat(),
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

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    logger.info("events_saved_to_json", filepath=str(output_path), count=len(events))


async def discover_events(
    scraper_config: ScraperConfig,
    orch_config: OrchestrationConfig,
    limit: int,
    max_pages: int = None,
    output_path: Path = None
):
    """
    Discover all events and initialize state database.

    Args:
        scraper_config: ScraperConfig for API settings
        orch_config: OrchestrationConfig for paths
        limit: Results per page
        max_pages: Maximum pages to fetch (None = all)
        output_path: Custom output path for JSON file
    """
    logger.info("=" * 60)
    logger.info("SCRIPT 1: EVENT DISCOVERY")
    logger.info("=" * 60)

    # Step 1: Discover events via API
    search = PreKindleEventSearch(scraper_config)
    events = await search.fetch_all_events(
        limit=limit,
        max_pages=max_pages,
        start_page=0,
    )

    logger.info("discovery_complete", total_events=len(events))

    # Print summary stats
    sold_out = sum(1 for e in events if e.is_sold_out)
    available = len(events) - sold_out
    purchasable_count = sum(1 for e in events if e.purchasable)
    not_purchasable_count = len(events) - purchasable_count

    print(f"\n{'=' * 60}")
    print("DISCOVERY SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total events found: {len(events)}")
    print(f"Available: {available}, Sold Out: {sold_out}")
    print(f"Purchasable: {purchasable_count}, Not purchasable (free): {not_purchasable_count}")

    # Show sample events
    if events:
        print(f"\nSample events (first 10):")
        for event in events[:10]:
            status = "[SOLD OUT]" if event.is_sold_out else ""
            free = "[FREE]" if not event.purchasable else ""
            print(f"  {event.title} {status}{free}")
            if event.details_secondary:
                print(f"    {event.details_secondary}")

    # Step 2: Save to JSON file
    json_path = output_path or orch_config.events_json_path
    save_event_list(events, json_path)

    print(f"\nEvents saved to: {json_path}")

    # Step 3: Initialize state file
    state_mgr = StateManager(orch_config)
    inserted = state_mgr.initialize_discovered_events([e.model_dump() for e in events])

    print(f"State file initialized: {orch_config.state_dir / 'scraping.json'}")
    print(f"  - {inserted} new events added")
    print(f"  - All events marked as 'pending'")

    # Print statistics
    stats = state_mgr.get_statistics()
    print(f"\nState File Statistics:")
    print(f"  Total: {stats.total_events}")
    print(f"  Pending: {stats.pending}")
    print(f"  Completed: {stats.completed}")
    print(f"  Failed: {stats.failed}")

    print(f"\n{'=' * 60}")
    print("NEXT STEPS:")
    print("  1. Run Script 2 to scrape all events:")
    print("     python orchestration/02_bulk_scraper.py")
    print("  2. Or scrape a subset:")
    print("     python orchestration/02_bulk_scraper.py --max-events 10")
    print(f"{'=' * 60}\n")

    return events


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Discover all PreKindle events and initialize state database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Discover all events
  %(prog)s --limit 50               # 50 results per page
  %(prog)s --max-pages 1            # First page only (for testing)
  %(prog)s --output custom.json     # Custom output path
        """
    )

    parser.add_argument('--output', '-o', type=str, default=None,
                        help='Output JSON file path (default: orchestration/state/events_discovered.json)')
    parser.add_argument('--limit', type=int, default=50,
                        help='Results per page (default: 50)')
    parser.add_argument('--max-pages', type=int, default=None,
                        help='Maximum pages to fetch (default: all)')
    parser.add_argument('--delay', type=float, default=1.0,
                        help='Seconds to wait between paginated requests (default: 1.0)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Configuration
    scraper_config = ScraperConfig(
        search_limit=args.limit,
        discovery_request_delay=args.delay,
    )

    orch_config = DEFAULT_ORCHESTRATION_CONFIG

    output_path = Path(args.output) if args.output else None

    try:
        await discover_events(
            scraper_config=scraper_config,
            orch_config=orch_config,
            limit=args.limit,
            max_pages=args.max_pages,
            output_path=output_path,
        )
        return 0

    except KeyboardInterrupt:
        print("\nDiscovery cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("discovery_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
