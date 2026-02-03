#!/usr/bin/env python3
"""
AdmitONE Website Scraper - Main Entry Point

Usage:
    python main.py
    python main.py --output-dir ./output --format json csv
    python main.py --cities toronto calgary --headless

Examples:
    python main.py
    python main.py --output-dir ./results --format json
    python main.py --cities toronto --headless
"""

import argparse
import asyncio
import logging
import sys
from typing import List, Optional

import structlog

from config import ScraperConfig, DEFAULT_CONFIG
from models import EventsScrapeResult
from discovery.graphql_scraper import GraphQLEventsScraper
from discovery.browser_scraper import BrowserEventsScraper
from discovery.cities import KNOWN_CITIES
from utils.exporters import export_events, print_summary

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
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")

logger = structlog.get_logger()


async def scrape_events(
    cities: Optional[List[str]] = None,
    event_type: Optional[str] = None,
    canada_wide: bool = True,
    config: ScraperConfig = None,
) -> EventsScrapeResult:
    """
    Main scraping function for event discovery.

    Args:
        cities: List of city slugs (default: KNOWN_CITIES)
        event_type: 'pro', 'community', or None for both
        canada_wide: If True, use Canada-wide radius query; else per-city
        config: Optional scraper configuration

    Returns:
        EventsScrapeResult with discovered events
    """
    config = config or DEFAULT_CONFIG
    cities = cities or KNOWN_CITIES

    logger.info("=" * 60)
    logger.info("ADMITONE WEBSITE SCRAPER")
    logger.info("=" * 60)
    logger.info("scrape_config", cities=cities, event_type=event_type, canada_wide=canada_wide)

    # Try GraphQL scraper first
    graphql_scraper = GraphQLEventsScraper(config)
    result = await graphql_scraper.fetch_events(
        cities=cities,
        event_type=event_type,
        canada_wide=canada_wide,
    )

    # If no events from GraphQL (schema not documented), use browser scraper
    if result.total_events == 0 and not result.errors:
        logger.info("graphql_empty", msg="Falling back to browser scraper...")
        browser_scraper = BrowserEventsScraper(config)
        result = await browser_scraper.fetch_events(cities=cities, event_type=event_type)

    return result


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Scrape all events from AdmitONE website",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s --output-dir ./results
  %(prog)s --format json
  %(prog)s --cities toronto calgary
  %(prog)s --headless
        """,
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        default="./output",
        help="Directory for output files (default: ./output)",
    )
    parser.add_argument(
        "--format",
        "-f",
        nargs="+",
        choices=["json", "csv"],
        default=["json", "csv"],
        help="Output format(s) (default: json csv)",
    )
    parser.add_argument(
        "--cities",
        "-c",
        nargs="+",
        default=None,
        help=f"Cities to scrape (default: all known cities: {', '.join(KNOWN_CITIES[:5])}...)",
    )
    parser.add_argument(
        "--event-type",
        "-t",
        choices=["pro", "community"],
        default=None,
        help="Filter by event type (default: both)",
    )
    parser.add_argument(
        "--per-city",
        action="store_true",
        help="Query per city instead of Canada-wide (slower but city-specific)",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    config = ScraperConfig(
        headless=args.headless,
        output_dir=args.output_dir,
    )

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        result = await scrape_events(
            cities=args.cities,
            event_type=args.event_type,
            canada_wide=not args.per_city,
            config=config,
        )

        print_summary(result)

        if result.events:
            exported = export_events(result, args.output_dir, args.format)
            print("\nExported files:")
            for fmt, filepath in exported.items():
                print(f"  {fmt.upper()}: {filepath}")
        else:
            print("\nNo events found to export.")
            if result.errors:
                print("Check errors above for details.")

        return 0 if result.events else 1

    except KeyboardInterrupt:
        print("\nScraping cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("scraping_failed", error=str(e))
        return 1


def run():
    """Synchronous entry point"""
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    run()
