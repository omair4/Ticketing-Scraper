#!/usr/bin/env python3
"""
Script 3: Inventory Refresh

Refreshes seat availability for seatsio events via API-only (no browser).
For GA events, re-navigates to event page to intercept fresh API response.

Usage:
    python orchestration/03_refresh_inventory.py
    python orchestration/03_refresh_inventory.py --input output/bulk_scrape_*/by_scenario/seatsio/
    python orchestration/03_refresh_inventory.py --concurrency 50
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import structlog
import httpx

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

from models import ScrapeResult, EventInfo, Seat, SeatStatus
from browser import CapturedResponse
from utils.exporters import export_results
from orchestration.config import OrchestrationConfig, DEFAULT_ORCHESTRATION_CONFIG

logger = structlog.get_logger()


class InventoryRefresher:
    """Refreshes seatsio event inventory via API-only (no browser)"""

    def __init__(self, orch_config: OrchestrationConfig):
        self.orch_config = orch_config
        self.semaphore = asyncio.Semaphore(orch_config.refresh_concurrency)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = orch_config.output_base_dir / f"refresh_{timestamp}"

        self.total_refreshed = 0
        self.total_failed = 0

    async def refresh_seatsio_event(
        self,
        previous_result_path: Path,
        httpx_client: httpx.AsyncClient,
    ) -> Optional[ScrapeResult]:
        """Refresh seat availability using saved API URLs."""
        async with self.semaphore:
            try:
                with open(previous_result_path, 'r', encoding='utf-8') as f:
                    prev_data = json.load(f)

                api_urls = prev_data.get('api_urls')
                scenario_type = prev_data.get('scenario_type')

                if scenario_type != 'seatsio':
                    return None
                if not api_urls:
                    logger.warning("no_api_urls", path=str(previous_result_path))
                    return None

                event_id = prev_data['event']['event_id']
                event_name = prev_data['event']['event_name']

                object_statuses_url = api_urls.get('object_statuses')
                chart_url = api_urls.get('chart_published')
                rendering_info_url = api_urls.get('rendering_info')

                if not all([object_statuses_url, chart_url, rendering_info_url]):
                    raise Exception("Missing required API URLs")

                # Fetch fresh data from seats.io CDN (no DataDome)
                http_responses = await asyncio.gather(
                    httpx_client.get(object_statuses_url, timeout=30.0),
                    httpx_client.get(chart_url, timeout=30.0),
                    httpx_client.get(rendering_info_url, timeout=30.0),
                )

                object_statuses_body = http_responses[0].content.decode('latin-1')
                chart_body = http_responses[1].content.decode('latin-1')
                captured_responses = [
                    CapturedResponse(
                        url=str(http_responses[0].url),
                        status=http_responses[0].status_code,
                        content_type=http_responses[0].headers.get('content-type', ''),
                        body=object_statuses_body,
                    ),
                    CapturedResponse(
                        url=str(http_responses[1].url),
                        status=http_responses[1].status_code,
                        content_type=http_responses[1].headers.get('content-type', ''),
                        body=chart_body,
                    ),
                    CapturedResponse(
                        url=str(http_responses[2].url),
                        status=http_responses[2].status_code,
                        content_type=http_responses[2].headers.get('content-type', ''),
                        body=http_responses[2].text,
                    ),
                ]

                # Re-extract using SeatsIOScraper
                from scrapers.seatsio_scraper import SeatsIOScraper
                scraper = SeatsIOScraper()

                rendering_info = scraper._extract_rendering_info(captured_responses)
                chart_data = scraper._extract_chart_data(captured_responses, rendering_info)
                availability = scraper._extract_availability(captured_responses)

                # Use pricing from previous result's raw_event_data
                pricing = {}
                raw_event_data = prev_data.get('raw_event_data')
                if raw_event_data:
                    pricing = scraper._extract_pricing_from_api(raw_event_data)

                seats = []
                ticket_categories = []

                if chart_data:
                    seats, ticket_categories = scraper._build_seats(
                        chart_data, availability, pricing,
                        rendering_info, captured_responses
                    )
                    if not seats and availability:
                        seats = scraper._build_seats_from_object_statuses(
                            captured_responses, pricing,
                            chart_data.get('category_map', {}),
                            rendering_info,
                        )
                        if seats and ticket_categories:
                            scraper._recalculate_ticket_category_stock(seats, ticket_categories)

                event_info = EventInfo(**prev_data['event'])

                result = ScrapeResult(
                    event=event_info,
                    scenario_type="seatsio",
                    seats=seats,
                    ticket_categories=ticket_categories,
                    api_urls=api_urls,
                    scrape_method="api_refresh",
                    raw_event_data=raw_event_data,
                )

                self.total_refreshed += 1
                logger.info("event_refreshed", event_id=event_id, seats=len(seats))

                await asyncio.sleep(self.orch_config.refresh_delay)
                return result

            except Exception as e:
                logger.error("refresh_failed", path=str(previous_result_path), error=str(e))
                self.total_failed += 1
                return None

    async def refresh_batch(self, result_files: list[Path]):
        if not result_files:
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"INVENTORY REFRESH: {len(result_files)} files")
        print(f"{'=' * 60}\n")

        start_time = datetime.now()

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [self.refresh_seatsio_event(fp, client) for fp in result_files]

            for i, coro in enumerate(asyncio.as_completed(tasks), 1):
                result = await coro
                if result:
                    exported = export_results(result, str(self.output_dir), ['json'])

                if i % 10 == 0 or i == len(result_files):
                    elapsed = (datetime.now() - start_time).total_seconds()
                    print(f"Progress: {i}/{len(result_files)} | "
                          f"Success: {self.total_refreshed} | Failed: {self.total_failed}")

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n{'=' * 60}")
        print(f"REFRESH COMPLETE: {self.total_refreshed} refreshed, {self.total_failed} failed")
        print(f"Time: {elapsed:.1f}s, Output: {self.output_dir}")
        print(f"{'=' * 60}\n")


async def refresh_inventory(
    orch_config: OrchestrationConfig,
    input_dir: Optional[Path] = None,
    event_id: Optional[str] = None,
):
    if input_dir:
        search_dir = input_dir
    else:
        bulk_dirs = sorted(orch_config.output_base_dir.glob("bulk_scrape_*"), reverse=True)
        if not bulk_dirs:
            print("No bulk scrape results found! Run Script 2 first.")
            return
        search_dir = bulk_dirs[0] / "by_scenario" / "seatsio"
        if not search_dir.exists():
            print(f"No seatsio results in {bulk_dirs[0]}")
            return

    result_files = list(search_dir.glob("*.json"))

    if event_id:
        result_files = [f for f in result_files if event_id in f.stem]

    if not result_files:
        print(f"No JSON files found in {search_dir}")
        return

    print(f"Found {len(result_files)} seatsio events to refresh")

    refresher = InventoryRefresher(orch_config)
    await refresher.refresh_batch(result_files)


async def main():
    parser = argparse.ArgumentParser(description='Refresh seatsio inventory (API-only)')
    parser.add_argument('--input', '-i', type=str, default=None)
    parser.add_argument('--event-id', type=str, default=None)
    parser.add_argument('--concurrency', type=int, default=50)
    parser.add_argument('--delay', type=float, default=0.1)
    parser.add_argument('--debug', action='store_true')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    orch_config = OrchestrationConfig(
        refresh_concurrency=args.concurrency,
        refresh_delay=args.delay,
    )

    input_dir = Path(args.input) if args.input else None

    try:
        await refresh_inventory(orch_config, input_dir, args.event_id)
        return 0
    except KeyboardInterrupt:
        print("\nRefresh cancelled.")
        return 130
    except Exception as e:
        logger.exception("refresh_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
