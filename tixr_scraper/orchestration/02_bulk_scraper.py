#!/usr/bin/env python3
"""
Script 2: Bulk Event Scraper

Scrapes all discovered events with concurrency control and DataDome-safe delays.

Usage:
    python orchestration/02_bulk_scraper.py
    python orchestration/02_bulk_scraper.py --max-events 10 --debug
    python orchestration/02_bulk_scraper.py --retry-failed
"""

import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

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

from config import ScraperConfig, DEFAULT_CONFIG
from main import scrape_event
from utils.exporters import export_results
from orchestration.state_manager import StateManager
from orchestration.config import OrchestrationConfig, DEFAULT_ORCHESTRATION_CONFIG

logger = structlog.get_logger()


class BulkScraper:
    """Orchestrates concurrent scraping of multiple Tixr events"""

    def __init__(self, state_mgr: StateManager, scraper_config: ScraperConfig,
                 orch_config: OrchestrationConfig):
        self.state_mgr = state_mgr
        self.scraper_config = scraper_config
        self.orch_config = orch_config
        self.semaphore = asyncio.Semaphore(orch_config.max_concurrency)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = orch_config.output_base_dir / f"bulk_scrape_{timestamp}"

        self.total_scraped = 0
        self.total_failed = 0
        self.start_time = None

    def _get_output_path(self, scenario_type: str) -> Path:
        scenario_dir = self.output_dir / "by_scenario" / scenario_type
        scenario_dir.mkdir(parents=True, exist_ok=True)
        return scenario_dir

    async def scrape_single_event(self, event: Dict[str, Any]) -> bool:
        event_id = event['event_id']
        event_url = event['event_url']
        event_name = event.get('event_name', 'Unknown')

        async with self.semaphore:
            try:
                self.state_mgr.mark_in_progress(event_id)
                logger.info("scraping_event", event_id=event_id, name=event_name)

                result = await scrape_event(event_url, self.scraper_config)

                has_data = result.seats or result.ticket_categories
                if not has_data and result.errors:
                    raise Exception(f"Scraping failed: {'; '.join(result.errors)}")

                scenario_type = result.scenario_type or "unknown"
                output_path = self._get_output_path(scenario_type)
                exported = export_results(result, str(output_path), ['json'])
                json_path = exported.get('json', '')

                self.state_mgr.mark_completed(event_id, json_path, scenario_type)
                self.total_scraped += 1

                logger.info("event_scraped_successfully",
                            event_id=event_id,
                            scenario=scenario_type,
                            seats=len(result.seats),
                            categories=len(result.ticket_categories))

                # DataDome-safe delay
                await asyncio.sleep(self.orch_config.request_delay)
                return True

            except Exception as e:
                error_msg = str(e)[:500]
                logger.error("event_scrape_failed", event_id=event_id, error=error_msg)
                self.state_mgr.mark_failed(event_id, error_msg)
                self.total_failed += 1
                return False

    async def scrape_batch(self, events: list[Dict[str, Any]]):
        if not events:
            logger.warning("no_events_to_scrape")
            return

        self.start_time = datetime.now()

        print(f"\n{'=' * 60}")
        print(f"BATCH SCRAPING: {len(events)} events")
        print(f"Concurrency: {self.orch_config.max_concurrency}, Delay: {self.orch_config.request_delay}s")
        print(f"{'=' * 60}\n")

        tasks = [self.scrape_single_event(event) for event in events]

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            await coro
            if i % 5 == 0 or i == len(events):
                elapsed = (datetime.now() - self.start_time).total_seconds()
                rate = i / elapsed if elapsed > 0 else 0
                print(f"Progress: {i}/{len(events)} | "
                      f"Success: {self.total_scraped} | Failed: {self.total_failed} | "
                      f"Rate: {rate:.2f}/sec")

        elapsed = (datetime.now() - self.start_time).total_seconds()
        print(f"\n{'=' * 60}")
        print("BATCH COMPLETE")
        print(f"{'=' * 60}")
        print(f"Processed: {len(events)}, Success: {self.total_scraped}, Failed: {self.total_failed}")
        print(f"Time: {elapsed:.1f}s ({elapsed/60:.1f}min)")
        print(f"Output: {self.output_dir}")


async def bulk_scrape(
    scraper_config: ScraperConfig,
    orch_config: OrchestrationConfig,
    max_events: int = None,
    retry_failed: bool = False,
    skip_sold_out: bool = False,
):
    state_mgr = StateManager(orch_config)

    if retry_failed:
        events = state_mgr.get_failed_events(max_retries=orch_config.max_retries_per_event)
        for event in events:
            state_mgr.reset_event(event['event_id'])
    else:
        events = state_mgr.get_pending_events(limit=max_events, skip_sold_out=skip_sold_out)

    if not events:
        print("No events to scrape! Run Script 1 first or use --retry-failed.")
        return

    stats = state_mgr.get_statistics()
    print(f"\nState: Total={stats.total_events}, Pending={stats.pending}, "
          f"Completed={stats.completed}, Failed={stats.failed}")
    print(f"Batch size: {len(events)}")

    scraper = BulkScraper(state_mgr, scraper_config, orch_config)
    await scraper.scrape_batch(events)

    final_stats = state_mgr.get_statistics()
    print(f"\nFinal: Completed={final_stats.completed} ({final_stats.completion_rate:.1f}%), "
          f"Failed={final_stats.failed}")
    print(f"Scenarios: seatsio={final_stats.seatsio_count}, GA={final_stats.general_admission_count}")

    if final_stats.failed > 0:
        print("\nRetry failed: python orchestration/02_bulk_scraper.py --retry-failed")


async def main():
    parser = argparse.ArgumentParser(description='Bulk scrape Tixr events')
    parser.add_argument('--max-events', type=int, default=None)
    parser.add_argument('--concurrency', type=int, default=3)
    parser.add_argument('--delay', type=float, default=5.0)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--skip-sold-out', action='store_true')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--debug', action='store_true')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper_config = ScraperConfig(headless=args.headless)
    orch_config = OrchestrationConfig(
        max_concurrency=args.concurrency,
        request_delay=args.delay,
    )

    try:
        await bulk_scrape(scraper_config, orch_config, args.max_events,
                          args.retry_failed, args.skip_sold_out)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled. Progress saved — run with --resume to continue.")
        return 130
    except Exception as e:
        logger.exception("bulk_scraping_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
