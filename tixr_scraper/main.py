#!/usr/bin/env python3
"""
Tixr Ticket Scraper - Main Entry Point

Usage:
    python main.py "<event_url>"
    python main.py "<event_url>" --headless --debug --format json csv -o ./output

Examples:
    python main.py "https://www.tixr.com/groups/greenriverfestival/events/green-river-festival-2026-146792"
    python main.py "https://www.tixr.com/groups/example/events/some-event-12345" --headless
"""

import asyncio
import argparse
import sys
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

from config import ScraperConfig, DEFAULT_CONFIG
from browser import BrowserAutomation
from scrapers import get_scraper_for_scenario
from utils.exporters import export_results, print_summary
from models import ScrapeResult, EventInfo

logger = structlog.get_logger()


async def scrape_event(
    event_url: str,
    config: ScraperConfig = None,
) -> ScrapeResult:
    """
    Main scraping function for a single Tixr event.

    Args:
        event_url: Tixr event URL
        config: Optional scraper configuration

    Returns:
        ScrapeResult with all extracted ticket data
    """
    config = config or DEFAULT_CONFIG

    logger.info("=" * 60)
    logger.info("TIXR TICKET SCRAPER")
    logger.info("=" * 60)
    logger.info("target_url", url=event_url)

    default_event = EventInfo(
        event_id="unknown",
        event_name="Unknown",
        venue_name="Unknown",
        event_url=event_url,
    )

    async with BrowserAutomation(config) as browser:
        logger.info("step_1", description="Navigating to event page...")

        nav_result = await browser.navigate_to_event(event_url)

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

        # Step 2: Run the appropriate scraper
        scenario = nav_result.scenario_detected or "general_admission"
        logger.info("step_2", description=f"Using {scenario} scraper...")

        scraper = get_scraper_for_scenario(scenario)

        result = await scraper.extract(
            captured_responses=nav_result.captured_responses,
            page_html=nav_result.page_html,
            event_url=event_url,
            event_api_data=nav_result.event_api_data,
        )

        return result


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Scrape ticket availability from Tixr events',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "https://www.tixr.com/groups/example/events/some-event-12345"
  %(prog)s "https://www.tixr.com/groups/example/events/some-event-12345" --headless
  %(prog)s "https://www.tixr.com/groups/example/events/some-event-12345" --format json
  %(prog)s "https://www.tixr.com/groups/example/events/some-event-12345" -o ./results --debug
        """
    )

    parser.add_argument('url', help='Tixr event URL to scrape')
    parser.add_argument('--output-dir', '-o', default='./output',
                        help='Directory for output files (default: ./output)')
    parser.add_argument('--format', '-f', nargs='+', choices=['json', 'csv'],
                        default=['json', 'csv'],
                        help='Output format(s) (default: json csv)')
    parser.add_argument('--headless', action='store_true',
                        help='Run browser in headless mode')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    config = ScraperConfig(
        headless=args.headless,
        output_dir=args.output_dir
    )

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        result = await scrape_event(args.url, config)

        print_summary(result)

        has_data = result.seats or result.ticket_categories
        if has_data:
            exported = export_results(result, args.output_dir, args.format)
            print("\nExported files:")
            for fmt, filepath in exported.items():
                print(f"  {fmt.upper()}: {filepath}")
        else:
            print("\nNo data found to export.")
            if result.errors:
                print("Check errors above for details.")

        return 0 if has_data else 1

    except KeyboardInterrupt:
        print("\nScraping cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("scraping_failed", error=str(e))
        return 1


def run():
    """Synchronous entry point"""
    sys.exit(asyncio.run(main()))


if __name__ == '__main__':
    run()
