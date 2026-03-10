#!/usr/bin/env python3
"""
Script 1: Event Discovery

Discovers all Tixr events via sitemaps + group crawling,
then initializes state database for bulk scraping.

Usage:
    python orchestration/01_discover.py
    python orchestration/01_discover.py --max-groups 5 --debug
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, UTC

_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import structlog

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
    max_groups: int = None,
    output_path: Path = None,
):
    """Discover all events and initialize state database."""
    logger.info("=" * 60)
    logger.info("SCRIPT 1: TIXR EVENT DISCOVERY")
    logger.info("=" * 60)

    # Step 1: Parse sitemaps
    sitemap_client = SitemapClient()
    group_slugs = await sitemap_client.fetch_group_slugs()
    logger.info("sitemap_complete", groups=len(group_slugs))

    # Step 2: Crawl groups
    async with BrowserAutomation(scraper_config) as browser:
        crawler = GroupCrawler(browser, scraper_config)
        events = await crawler.crawl_groups(group_slugs, max_groups=max_groups)

    logger.info("discovery_complete", total_events=len(events))

    # Summary
    sold_out = sum(1 for e in events if e.is_sold_out)
    seatsio_count = sum(1 for e in events if e.has_seatsio)

    print(f"\n{'=' * 60}")
    print("DISCOVERY SUMMARY")
    print(f"{'=' * 60}")
    print(f"Groups crawled: {min(len(group_slugs), max_groups or len(group_slugs))}")
    print(f"Total events found: {len(events)}")
    print(f"  Available: {len(events) - sold_out}, Sold Out: {sold_out}")
    print(f"  seats.io: {seatsio_count}, GA: {len(events) - seatsio_count}")

    if events:
        print(f"\nSample events (first 10):")
        for event in events[:10]:
            status = "[SOLD OUT]" if event.is_sold_out else ""
            print(f"  {event.event_name} {status}")

    # Save events JSON
    json_path = output_path or orch_config.events_json_path
    save_event_list(events, json_path)
    print(f"\nEvents saved to: {json_path}")

    # Initialize state
    state_mgr = StateManager(orch_config)
    inserted = state_mgr.initialize_discovered_events([e.model_dump() for e in events])

    print(f"State file initialized: {orch_config.state_dir / 'scraping.json'}")
    print(f"  - {inserted} new events added")

    stats = state_mgr.get_statistics()
    print(f"\nState Statistics:")
    print(f"  Total: {stats.total_events}, Pending: {stats.pending}")

    print(f"\n{'=' * 60}")
    print("NEXT STEPS:")
    print("  python orchestration/02_bulk_scraper.py")
    print("  python orchestration/02_bulk_scraper.py --max-events 10")
    print(f"{'=' * 60}\n")

    return events


async def main():
    parser = argparse.ArgumentParser(
        description='Discover all Tixr events and initialize state database',
    )
    parser.add_argument('--output', '-o', type=str, default=None)
    parser.add_argument('--max-groups', type=int, default=None)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--debug', action='store_true')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper_config = ScraperConfig(headless=args.headless)
    orch_config = DEFAULT_ORCHESTRATION_CONFIG
    output_path = Path(args.output) if args.output else None

    try:
        await discover_events(scraper_config, orch_config, args.max_groups, output_path)
        return 0
    except KeyboardInterrupt:
        print("\nDiscovery cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("discovery_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
