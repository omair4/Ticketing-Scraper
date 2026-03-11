#!/usr/bin/env python3
"""
Script 2: Bulk Event Scraper

Scrapes all discovered events with concurrency control, error handling, and progress tracking.

Usage:
    python orchestration/02_bulk_scraper.py
    python orchestration/02_bulk_scraper.py --concurrency 5 --resume
    python orchestration/02_bulk_scraper.py --filter-purchasable --max-events 100
    python orchestration/02_bulk_scraper.py --retry-failed
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

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

from config import ScraperConfig, DEFAULT_CONFIG
from main import scrape_event
from utils.exporters import export_results
from orchestration.state_manager import StateManager
from orchestration.config import OrchestrationConfig, DEFAULT_ORCHESTRATION_CONFIG

logger = structlog.get_logger()


class BulkScraper:
    """Orchestrates concurrent scraping of multiple events"""

    def __init__(
        self,
        state_mgr: StateManager,
        scraper_config: ScraperConfig,
        orch_config: OrchestrationConfig
    ):
        self.state_mgr = state_mgr
        self.scraper_config = scraper_config
        self.orch_config = orch_config

        # Concurrency control
        self.semaphore = asyncio.Semaphore(orch_config.max_concurrency)

        # Output directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = orch_config.output_base_dir / f"bulk_scrape_{timestamp}"

        # Statistics
        self.total_scraped = 0
        self.total_failed = 0
        self.start_time = None

    def _get_output_path(self, scenario_type: str) -> Path:
        """Get output directory organized by scenario"""
        scenario_dir = self.output_dir / "by_scenario" / scenario_type
        scenario_dir.mkdir(parents=True, exist_ok=True)
        return scenario_dir

    async def scrape_single_event(self, event: Dict[str, Any]) -> bool:
        """
        Scrape one event using existing main.scrape_event()

        Args:
            event: Event dict from state database

        Returns:
            True if successful, False if failed
        """
        event_id = event['event_id']
        ticket_url = event['ticket_url']
        title = event.get('title', 'Unknown')

        async with self.semaphore:
            try:
                # Mark as in progress
                self.state_mgr.mark_in_progress(event_id)

                logger.info("scraping_event", event_id=event_id, title=title, url=ticket_url)

                # Run the main scraper
                result = await scrape_event(ticket_url, self.scraper_config)

                # Check if scraping was successful
                has_data = result.seats or result.ticket_categories
                if not has_data and result.errors:
                    raise Exception(f"Scraping failed: {'; '.join(result.errors)}")

                # Export result
                scenario_type = result.scenario_type or "unknown"
                output_path = self._get_output_path(scenario_type)

                exported = export_results(result, str(output_path), ['json'])
                json_path = exported.get('json', '')

                # Update state
                self.state_mgr.mark_completed(
                    event_id,
                    json_path,
                    scenario_type
                )

                self.total_scraped += 1

                logger.info(
                    "event_scraped_successfully",
                    event_id=event_id,
                    scenario=scenario_type,
                    seats=len(result.seats),
                    categories=len(result.ticket_categories),
                    output=json_path
                )

                # Delay between events
                await asyncio.sleep(self.orch_config.request_delay)

                return True

            except Exception as e:
                error_msg = str(e)[:500]  # Truncate long errors
                logger.error("event_scrape_failed", event_id=event_id, error=error_msg)

                self.state_mgr.mark_failed(event_id, error_msg)
                self.total_failed += 1

                return False

    async def scrape_batch(self, events: list[Dict[str, Any]]):
        """
        Scrape a batch of events concurrently

        Args:
            events: List of event dicts to scrape
        """
        if not events:
            logger.warning("no_events_to_scrape")
            return

        self.start_time = datetime.now()

        logger.info("=" * 60)
        logger.info("BATCH SCRAPING STARTED")
        logger.info("=" * 60)
        logger.info("batch_config",
                    total_events=len(events),
                    concurrency=self.orch_config.max_concurrency,
                    delay=self.orch_config.request_delay)

        # Create tasks for all events
        tasks = [self.scrape_single_event(event) for event in events]

        # Run with progress updates
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            await coro

            # Print progress every 10 events or at end
            if i % 10 == 0 or i == len(events):
                elapsed = (datetime.now() - self.start_time).total_seconds()
                rate = i / elapsed if elapsed > 0 else 0

                print(f"\nProgress: {i}/{len(events)} events | "
                      f"Success: {self.total_scraped} | Failed: {self.total_failed} | "
                      f"Rate: {rate:.2f} events/sec")

                # Show current state statistics
                stats = self.state_mgr.get_statistics()
                print(f"  Overall: Pending: {stats.pending}, "
                      f"Completed: {stats.completed}, Failed: {stats.failed}")

        # Final summary
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"\n{'=' * 60}")
        print("BATCH SCRAPING COMPLETE")
        print(f"{'=' * 60}")
        print(f"Total events processed: {len(events)}")
        print(f"  Successful: {self.total_scraped}")
        print(f"  Failed: {self.total_failed}")
        print(f"Time elapsed: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Average rate: {len(events)/elapsed:.2f} events/sec")
        print(f"Output directory: {self.output_dir}")


async def bulk_scrape(
    scraper_config: ScraperConfig,
    orch_config: OrchestrationConfig,
    max_events: int = None,
    retry_failed: bool = False,
    skip_sold_out: bool = False,
    only_purchasable: bool = False,
):
    """
    Main bulk scraping function

    Args:
        scraper_config: ScraperConfig for browser settings
        orch_config: OrchestrationConfig for orchestration settings
        max_events: Maximum events to scrape (None = all)
        retry_failed: If True, scrape failed events instead of pending
        skip_sold_out: Skip sold out events
        only_purchasable: Only scrape purchasable events
    """
    state_mgr = StateManager(orch_config)

    # Get events to scrape
    if retry_failed:
        events = state_mgr.get_failed_events(max_retries=orch_config.max_retries_per_event)
        logger.info("retrying_failed_events", count=len(events))
        # Reset to pending
        for event in events:
            state_mgr.reset_event(event['event_id'])
    else:
        events = state_mgr.get_pending_events(
            limit=max_events,
            skip_sold_out=skip_sold_out,
            only_purchasable=only_purchasable
        )

    if not events:
        print("No events to scrape!")
        print("\nPossible reasons:")
        print("  - All events already scraped (check with --retry-failed)")
        print("  - No events in database (run Script 1 first)")
        print("  - Filters excluded all events")
        return

    # Print initial statistics
    stats = state_mgr.get_statistics()
    print(f"\n{'=' * 60}")
    print("BULK SCRAPING - INITIAL STATE")
    print(f"{'=' * 60}")
    print(f"Total events in database: {stats.total_events}")
    print(f"  Pending: {stats.pending}")
    print(f"  In Progress: {stats.in_progress}")
    print(f"  Completed: {stats.completed}")
    print(f"  Failed: {stats.failed}")
    print(f"\nEvents to scrape in this batch: {len(events)}")
    print(f"{'=' * 60}\n")

    # Run bulk scraper
    scraper = BulkScraper(state_mgr, scraper_config, orch_config)
    await scraper.scrape_batch(events)

    # Final statistics
    final_stats = state_mgr.get_statistics()
    print(f"\n{'=' * 60}")
    print("FINAL STATISTICS")
    print(f"{'=' * 60}")
    print(f"Total: {final_stats.total_events}")
    print(f"  Pending: {final_stats.pending}")
    print(f"  Completed: {final_stats.completed} ({final_stats.completion_rate:.1f}%)")
    print(f"  Failed: {final_stats.failed} ({final_stats.failure_rate:.1f}%)")
    print(f"\nScenario breakdown:")
    print(f"  seatsio: {final_stats.seatsio_count}")
    print(f"  general_admission: {final_stats.general_admission_count}")
    print(f"  svg_seatmap: {final_stats.svg_seatmap_count}")
    print(f"\n{'=' * 60}")

    if final_stats.failed > 0:
        print("\nTo retry failed events:")
        print("  python orchestration/02_bulk_scraper.py --retry-failed")

    if final_stats.pending > 0:
        print(f"\nTo continue scraping remaining {final_stats.pending} events:")
        print("  python orchestration/02_bulk_scraper.py --resume")

    print(f"\n{'=' * 60}\n")


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Bulk scrape all discovered PreKindle events',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Scrape all pending events
  %(prog)s --max-events 10          # Scrape first 10 events (for testing)
  %(prog)s --concurrency 3          # Use 3 concurrent browsers
  %(prog)s --retry-failed           # Retry previously failed events
  %(prog)s --only-purchasable       # Skip free events
  %(prog)s --skip-sold-out          # Skip sold out events
        """
    )

    parser.add_argument('--max-events', type=int, default=None,
                        help='Maximum events to scrape (default: all)')
    parser.add_argument('--concurrency', type=int, default=5,
                        help='Number of concurrent browser instances (default: 5)')
    parser.add_argument('--delay', type=float, default=3.0,
                        help='Seconds to wait between events (default: 3.0)')
    parser.add_argument('--retry-failed', action='store_true',
                        help='Retry previously failed events')
    parser.add_argument('--resume', action='store_true',
                        help='Resume scraping (alias for default behavior)')
    parser.add_argument('--skip-sold-out', action='store_true',
                        help='Skip sold out events')
    parser.add_argument('--only-purchasable', '--filter-purchasable', action='store_true',
                        help='Only scrape purchasable events (skip free events)')
    parser.add_argument('--headless', action='store_true',
                        help='Run browsers in headless mode')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Configuration
    scraper_config = ScraperConfig(
        headless=args.headless,
    )

    orch_config = OrchestrationConfig(
        max_concurrency=args.concurrency,
        request_delay=args.delay,
    )

    try:
        await bulk_scrape(
            scraper_config=scraper_config,
            orch_config=orch_config,
            max_events=args.max_events,
            retry_failed=args.retry_failed,
            skip_sold_out=args.skip_sold_out,
            only_purchasable=args.only_purchasable,
        )
        return 0

    except KeyboardInterrupt:
        print("\n\nBulk scraping cancelled by user.")
        print("Progress has been saved. Run with --resume to continue.")
        return 130
    except Exception as e:
        logger.exception("bulk_scraping_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
