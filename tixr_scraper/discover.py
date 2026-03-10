#!/usr/bin/env python3
"""
Tixr Event Discovery CLI

Discovers all Tixr events by parsing sitemaps for group slugs,
then crawling each group page to enumerate events.

Usage:
    python discover.py
    python discover.py --max-groups 5 --debug
    python discover.py --output discovered_events.json
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, UTC

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
from browser import BrowserAutomation
from discovery.sitemap_client import SitemapClient
from discovery.group_crawler import GroupCrawler

logger = structlog.get_logger()


def save_event_list(events: list, output_path: Path):
    """Save discovered events to JSON file"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "discovered_at": datetime.now(UTC).isoformat(),
        "total_events": len(events),
        "events": [e.model_dump(mode='json') for e in events],
    }

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
    config: ScraperConfig,
    max_groups: int = None,
    output_path: Path = None,
):
    """
    Main discovery flow:
    1. Parse sitemaps → group slugs
    2. Crawl each group → enumerate events
    3. Save results
    """
    logger.info("=" * 60)
    logger.info("TIXR EVENT DISCOVERY")
    logger.info("=" * 60)

    # Step 1: Discover group slugs from sitemaps
    logger.info("step_1", description="Parsing sitemaps for group slugs...")
    sitemap_client = SitemapClient()
    group_slugs = await sitemap_client.fetch_group_slugs()

    print(f"\nFound {len(group_slugs)} group slugs from sitemaps")
    if max_groups:
        print(f"Limiting to first {max_groups} groups")

    # Step 2: Crawl groups to get events
    logger.info("step_2", description="Crawling group pages for events...")

    async with BrowserAutomation(config) as browser:
        crawler = GroupCrawler(browser, config)
        events = await crawler.crawl_groups(group_slugs, max_groups=max_groups)

    # Step 3: Summary and save
    sold_out = sum(1 for e in events if e.is_sold_out)
    seatsio_count = sum(1 for e in events if e.has_seatsio)

    print(f"\n{'=' * 60}")
    print("DISCOVERY SUMMARY")
    print(f"{'=' * 60}")
    print(f"Groups crawled: {min(len(group_slugs), max_groups or len(group_slugs))}")
    print(f"Total events found: {len(events)}")
    print(f"  Available: {len(events) - sold_out}")
    print(f"  Sold Out: {sold_out}")
    print(f"  seats.io events: {seatsio_count}")
    print(f"  GA events: {len(events) - seatsio_count}")

    if events:
        print(f"\nSample events (first 10):")
        for event in events[:10]:
            status = "[SOLD OUT]" if event.is_sold_out else ""
            seatsio = "[seatsio]" if event.has_seatsio else "[GA]"
            print(f"  {event.event_name} {seatsio} {status}")
            print(f"    {event.event_url}")

    # Save
    save_path = output_path or Path("orchestration/state/events_discovered.json")
    save_event_list(events, save_path)
    print(f"\nEvents saved to: {save_path}")

    print(f"\n{'=' * 60}")
    print("NEXT STEPS:")
    print("  1. Initialize state and run bulk scraper:")
    print("     python orchestration/01_discover.py")
    print("  2. Or scrape a single event:")
    print("     python main.py <event_url>")
    print(f"{'=' * 60}\n")

    return events


async def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Discover all Tixr events from sitemaps and group pages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Discover all events
  %(prog)s --max-groups 3 --debug   # Test with 3 groups
  %(prog)s --output custom.json     # Custom output path
  %(prog)s --headless               # Run browser in headless mode
        """
    )

    parser.add_argument('--output', '-o', type=str, default=None,
                        help='Output JSON file path')
    parser.add_argument('--max-groups', type=int, default=None,
                        help='Maximum groups to crawl (default: all)')
    parser.add_argument('--headless', action='store_true',
                        help='Run browser in headless mode')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    config = ScraperConfig(headless=args.headless)
    output_path = Path(args.output) if args.output else None

    try:
        await discover_events(
            config=config,
            max_groups=args.max_groups,
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
