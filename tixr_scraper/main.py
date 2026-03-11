#!/usr/bin/env python3
"""
Tixr Ticket Scraper - Main Entry Point

Usage:
    python main.py <event_url>
    python main.py <event_url> --output-dir ./results --format json
"""

import argparse
import asyncio
import sys
from typing import Optional

import structlog

from .config import ScraperConfig, DEFAULT_CONFIG
from .browser import BrowserAutomation
from .scrapers import get_scraper_for_scenario
from .utils.exporters import export_results
from .models import ScrapeResult, EventInfo


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

logger = structlog.get_logger()


async def scrape_event(
    event_url: str,
    config: Optional[ScraperConfig] = None,
    event_info_override: Optional[EventInfo] = None,
) -> ScrapeResult:
    """
    Main scraping function for a single Tixr event.
    """
    config = config or DEFAULT_CONFIG

    logger.info("=" * 60)
    logger.info("TIXR TICKET SCRAPER")
    logger.info("=" * 60)
    logger.info("target_url", url=event_url)

    default_event = event_info_override or EventInfo(
        event_id="unknown",
        event_name="Unknown Tixr Event",
        venue_name="Unknown Venue",
        event_url=event_url,
    )

    async with BrowserAutomation(config) as browser:
        logger.info("step_1", description="Navigating to Tixr event page...")

        nav_result = await browser.navigate_to_tickets(event_url)

        if not nav_result.success:
            logger.error("navigation_failed", error=nav_result.error)
            return ScrapeResult(
                event=default_event,
                scenario_type="unknown",
                errors=[f"Navigation failed: {nav_result.error}"],
            )

        logger.info(
            "navigation_complete",
            scenario=nav_result.scenario_detected,
            responses_captured=len(nav_result.captured_responses),
        )

        for resp in nav_result.captured_responses:
            logger.debug("captured_api", url=resp.url[:80], status=resp.status)

        scenario = nav_result.scenario_detected or "tixr_stub"
        logger.info("step_2", description=f"Using {scenario} scraper...")

        scraper = get_scraper_for_scenario(scenario)

        logger.info("step_3", description="Extracting ticket data...")

        result = await scraper.extract(
            captured_responses=nav_result.captured_responses,
            page_html=nav_result.page_html,
            event_url=event_url,
            event_info_override=event_info_override,
        )

        return result


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape ticket availability from Tixr events",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "https://www.tixr.com/..."
  %(prog)s "https://www.tixr.com/..." --output-dir ./results
  %(prog)s "https://www.tixr.com/..." --format json
  %(prog)s "https://www.tixr.com/..." --headless
        """,
    )

    parser.add_argument("url", help="Tixr event URL to scrape")
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
        choices=["json"],
        default=["json"],
        help="Output format(s) (default: json)",
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run browser in headless mode"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    args = parser.parse_args()

    config = ScraperConfig(
        headless=args.headless,
        output_dir=args.output_dir,
    )

    if args.debug:
        import logging

        logging.getLogger().setLevel(logging.DEBUG)

    try:
        result = await scrape_event(args.url, config)

        has_data = bool(result.seats or result.ticket_categories)
        exported = export_results(result, args.output_dir, args.format)

        print("\nExported files:")
        for fmt, filepath in exported.items():
            print(f"  {fmt.upper()}: {filepath}")

        return 0 if exported or has_data else 1
    except KeyboardInterrupt:
        print("\nScraping cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("scraping_failed", error=str(e))
        return 1


def run():
    """Synchronous entry point."""
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    run()

