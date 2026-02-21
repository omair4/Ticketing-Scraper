#!/usr/bin/env python3
"""
Script 3: Inventory Refresh (API-only)

Refreshes seat availability for seatsio events WITHOUT browser automation.
Uses saved API URLs from previous scrapes to fetch fresh data directly.

Usage:
    python orchestration/03_refresh_inventory.py
    python orchestration/03_refresh_inventory.py --input output/bulk_scrape_20260219/by_scenario/seatsio/
    python orchestration/03_refresh_inventory.py --event-id -2852852648567343600
    python orchestration/03_refresh_inventory.py --concurrency 50
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Ensure parent package (prekindle_scraper/) is on sys.path for bare imports
_parent = str(Path(__file__).resolve().parent.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import structlog
import httpx

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

from models import ScrapeResult, EventInfo, Seat, TicketCategory, SeatStatus
from utils.decoder import decode_seatsio_response
from utils.exporters import export_results
from orchestration.config import OrchestrationConfig, DEFAULT_ORCHESTRATION_CONFIG

logger = structlog.get_logger()


class InventoryRefresher:
    """Refreshes seatsio event inventory via API-only (no browser)"""

    def __init__(self, orch_config: OrchestrationConfig):
        self.orch_config = orch_config
        self.semaphore = asyncio.Semaphore(orch_config.refresh_concurrency)

        # Output directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = orch_config.output_base_dir / f"refresh_{timestamp}"

        # Statistics
        self.total_refreshed = 0
        self.total_failed = 0

    async def refresh_seatsio_event(
        self,
        previous_result_path: Path,
        httpx_client: httpx.AsyncClient
    ) -> Optional[ScrapeResult]:
        """
        Refresh seat availability using saved API URLs.

        Args:
            previous_result_path: Path to previous JSON scrape result
            httpx_client: Async HTTP client

        Returns:
            Refreshed ScrapeResult or None if failed
        """
        async with self.semaphore:
            try:
                # Step 1: Load previous result
                with open(previous_result_path, 'r', encoding='utf-8') as f:
                    prev_data = json.load(f)

                api_urls = prev_data.get('api_urls')
                html_pricing = prev_data.get('html_pricing')
                scenario_type = prev_data.get('scenario_type')

                if scenario_type != 'seatsio':
                    logger.warning("not_seatsio_scenario",
                                   path=str(previous_result_path),
                                   scenario=scenario_type)
                    return None

                if not api_urls:
                    logger.warning("no_api_urls", path=str(previous_result_path))
                    return None

                event_id = prev_data['event']['event_id']
                event_name = prev_data['event']['event_name']

                logger.info("refreshing_event",
                            event_id=event_id,
                            event_name=event_name)

                # Step 2: Fetch fresh data from seatsio APIs concurrently
                object_statuses_url = api_urls.get('object_statuses')
                chart_url = api_urls.get('chart_published')
                rendering_info_url = api_urls.get('rendering_info')

                if not all([object_statuses_url, chart_url, rendering_info_url]):
                    raise Exception("Missing required API URLs")

                responses = await asyncio.gather(
                    httpx_client.get(object_statuses_url, timeout=30.0),
                    httpx_client.get(chart_url, timeout=30.0),
                    httpx_client.get(rendering_info_url, timeout=30.0),
                )

                # Step 3: Decode responses
                object_statuses_data = decode_seatsio_response(responses[0].text)
                chart_data = decode_seatsio_response(responses[1].text)
                rendering_info = responses[2].json()  # Plain JSON

                # Step 4: Rebuild seats using logic similar to seatsio_scraper
                # Import the scraper to reuse parsing logic
                from scrapers.seatsio_scraper import SeatsIOScraper
                scraper = SeatsIOScraper()

                # Parse availability
                availability = {}
                if object_statuses_data and 'objects' in object_statuses_data:
                    for obj_id, obj_data in object_statuses_data['objects'].items():
                        status = obj_data.get('status', 'available')
                        # Map seatsio status to our SeatStatus enum
                        if status == 'free':
                            availability[obj_id] = SeatStatus.AVAILABLE
                        elif status == 'booked':
                            availability[obj_id] = SeatStatus.BOOKED
                        elif status in ['hold', 'held']:
                            availability[obj_id] = SeatStatus.HELD
                        else:
                            availability[obj_id] = SeatStatus.UNKNOWN

                # Parse chart structure - reuse scraper's private method
                # We'll create a minimal chart_data dict that matches the scraper's expectations
                chart_parsed = {
                    'rows': [],
                    'ga_areas': [],
                    'table_areas': [],
                    'sections': []
                }

                # Parse chart structure
                if chart_data:
                    if 'rows' in chart_data:
                        chart_parsed['rows'] = chart_data.get('rows', [])
                    if 'generalAdmissionAreas' in chart_data:
                        chart_parsed['ga_areas'] = chart_data.get('generalAdmissionAreas', [])
                    if 'tables' in chart_data:
                        chart_parsed['table_areas'] = chart_data.get('tables', [])
                    if 'booths' in chart_data:
                        chart_parsed['table_areas'].extend(chart_data.get('booths', []))
                    if 'sections' in chart_data:
                        chart_parsed['sections'] = chart_data.get('sections', [])

                # Build seats - we need to reconstruct this from the data
                seats = []
                ticket_categories = []

                # Use scraper's build_seats method (we'll need to adapt this)
                # For now, implement simplified version
                seats, ticket_categories = await self._build_refreshed_seats(
                    chart_parsed,
                    availability,
                    html_pricing,
                    rendering_info
                )

                # Step 5: Create refreshed result
                event_info = EventInfo(**prev_data['event'])

                result = ScrapeResult(
                    event=event_info,
                    scenario_type="seatsio",
                    seats=seats,
                    ticket_categories=ticket_categories,
                    api_urls=api_urls,
                    html_pricing=html_pricing,
                    scrape_method="api_refresh",  # Indicates this was API-only
                )

                self.total_refreshed += 1

                logger.info("event_refreshed",
                            event_id=event_id,
                            seats=len(seats),
                            categories=len(ticket_categories))

                # Delay between requests
                await asyncio.sleep(self.orch_config.refresh_delay)

                return result

            except Exception as e:
                logger.error("refresh_failed",
                             path=str(previous_result_path),
                             error=str(e))
                self.total_failed += 1
                return None

    async def _build_refreshed_seats(
        self,
        chart_data: Dict[str, Any],
        availability: Dict[str, SeatStatus],
        html_pricing: Optional[Dict],
        rendering_info: Optional[Dict]
    ):
        """
        Simplified seat building for refresh (reuses pricing from original scrape).

        This is a simplified version - for production, we'd import and reuse
        the full SeatsIOScraper._build_seats() method.
        """
        seats = []
        ticket_categories = []

        # Build category mappings from rendering_info
        category_map = {}
        category_by_key = {}

        if rendering_info:
            category_map = rendering_info.get('objectCategories', {})

            # Build category_by_key lookup
            for cat in rendering_info.get('categories', []):
                key = cat.get('key')
                if key:
                    category_by_key[key] = cat

        # Process rows (individual seats)
        for row_data in chart_data.get('rows', []):
            section_label = row_data.get('sectionLabel', '')
            row_label = row_data.get('label', '')

            for seat_data in row_data.get('seats', []):
                seat_label = seat_data.get('label', '')
                seat_uuid = seat_data.get('uuid', '')

                # Build composite label for status lookup
                composite_label = f"{section_label}-{row_label}-{seat_label}"

                # Try different label formats
                status = (
                    availability.get(composite_label) or
                    availability.get(f"{section_label} {row_label}-{seat_label}") or
                    availability.get(seat_label) or
                    SeatStatus.AVAILABLE  # Default
                )

                # Get category and price
                category_key = category_map.get(composite_label) or category_map.get(seat_label)
                category_info = category_by_key.get(category_key, {}) if category_key else {}
                category_label = category_info.get('label', '')

                # Lookup price from html_pricing
                price_data = html_pricing.get(category_label, {}) if html_pricing else {}
                price = price_data.get('price', 0)

                seat = Seat(
                    section=section_label,
                    row=row_label,
                    seat=seat_label,
                    price=price,
                    status=status,
                    price_level=category_label,
                    x=seat_data.get('x'),
                    y=seat_data.get('y'),
                    ada=category_info.get('accessible', False)
                )
                seats.append(seat)

        # Process GA areas
        for ga_area in chart_data.get('ga_areas', []):
            area_label = ga_area.get('label', '')
            capacity = ga_area.get('capacity', 0)

            # Get category and price
            category_key = category_map.get(area_label)
            category_info = category_by_key.get(category_key, {}) if category_key else {}
            category_label = category_info.get('label', area_label)

            price_data = html_pricing.get(category_label, {}) if html_pricing else {}
            price = price_data.get('price', 0)

            category = TicketCategory(
                category_name=area_label,
                category_id=category_key or area_label,
                price=price,
                current_stock=capacity,
                is_out_of_stock=False
            )
            ticket_categories.append(category)

        return seats, ticket_categories

    async def refresh_batch(self, result_files: list[Path]):
        """Refresh a batch of events"""
        if not result_files:
            logger.warning("no_files_to_refresh")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("=" * 60)
        logger.info("INVENTORY REFRESH STARTED")
        logger.info("=" * 60)
        logger.info("refresh_config",
                    total_files=len(result_files),
                    concurrency=self.orch_config.refresh_concurrency,
                    delay=self.orch_config.refresh_delay)

        start_time = datetime.now()

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [
                self.refresh_seatsio_event(file_path, client)
                for file_path in result_files
            ]

            refreshed_results = []

            for i, coro in enumerate(asyncio.as_completed(tasks), 1):
                result = await coro
                if result:
                    refreshed_results.append(result)

                    # Export refreshed result
                    exported = export_results(result, str(self.output_dir), ['json'])
                    logger.info("result_exported",
                                event_id=result.event.event_id,
                                path=exported.get('json'))

                # Print progress
                if i % 10 == 0 or i == len(result_files):
                    elapsed = (datetime.now() - start_time).total_seconds()
                    rate = i / elapsed if elapsed > 0 else 0

                    print(f"\nProgress: {i}/{len(result_files)} files | "
                          f"Success: {self.total_refreshed} | Failed: {self.total_failed} | "
                          f"Rate: {rate:.2f} events/sec")

        # Final summary
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n{'=' * 60}")
        print("INVENTORY REFRESH COMPLETE")
        print(f"{'=' * 60}")
        print(f"Total files processed: {len(result_files)}")
        print(f"  Successfully refreshed: {self.total_refreshed}")
        print(f"  Failed: {self.total_failed}")
        print(f"Time elapsed: {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
        print(f"Average rate: {len(result_files)/elapsed:.2f} events/sec")
        print(f"Output directory: {self.output_dir}")
        print(f"{'=' * 60}\n")


async def refresh_inventory(
    orch_config: OrchestrationConfig,
    input_dir: Optional[Path] = None,
    event_id: Optional[str] = None
):
    """
    Main inventory refresh function

    Args:
        orch_config: OrchestrationConfig
        input_dir: Directory containing previous scrape results
        event_id: Specific event ID to refresh (optional)
    """
    # Find seatsio result files
    if input_dir:
        search_dir = input_dir
    else:
        # Find most recent bulk_scrape output
        bulk_dirs = sorted(orch_config.output_base_dir.glob("bulk_scrape_*"), reverse=True)
        if not bulk_dirs:
            print("No bulk scrape results found!")
            print("Please run Script 2 first or specify --input directory")
            return

        search_dir = bulk_dirs[0] / "by_scenario" / "seatsio"
        if not search_dir.exists():
            print(f"No seatsio results found in {bulk_dirs[0]}")
            return

    print(f"Searching for seatsio results in: {search_dir}")

    # Collect JSON files
    result_files = list(search_dir.glob("*.json"))

    # Filter by event_id if specified
    if event_id:
        result_files = [
            f for f in result_files
            if event_id in f.stem or event_id in open(f).read()
        ]

    if not result_files:
        print(f"No seatsio JSON files found in {search_dir}")
        return

    print(f"Found {len(result_files)} seatsio events to refresh")

    # Run refresher
    refresher = InventoryRefresher(orch_config)
    await refresher.refresh_batch(result_files)


async def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Refresh seat inventory for seatsio events (API-only, no browser)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Refresh latest bulk scrape results
  %(prog)s --input output/bulk_scrape_20260219/by_scenario/seatsio/
  %(prog)s --event-id -2852852648567343600
  %(prog)s --concurrency 50         # 50 concurrent API requests
        """
    )

    parser.add_argument('--input', '-i', type=str, default=None,
                        help='Input directory containing seatsio JSON results')
    parser.add_argument('--event-id', type=str, default=None,
                        help='Refresh specific event ID only')
    parser.add_argument('--concurrency', type=int, default=50,
                        help='Number of concurrent API requests (default: 50)')
    parser.add_argument('--delay', type=float, default=0.1,
                        help='Seconds to wait between requests (default: 0.1)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Configuration
    orch_config = OrchestrationConfig(
        refresh_concurrency=args.concurrency,
        refresh_delay=args.delay,
    )

    input_dir = Path(args.input) if args.input else None

    try:
        await refresh_inventory(
            orch_config=orch_config,
            input_dir=input_dir,
            event_id=args.event_id
        )
        return 0

    except KeyboardInterrupt:
        print("\nInventory refresh cancelled by user.")
        return 130
    except Exception as e:
        logger.exception("refresh_failed", error=str(e))
        return 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
