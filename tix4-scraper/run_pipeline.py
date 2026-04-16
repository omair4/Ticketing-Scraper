#!/usr/bin/env python3
"""
Run the tix4 Step Function pipeline locally by calling each Lambda handler
one by one. Requires MySQL (e.g. Docker) with schema applied and creds configured.

Usage:
  cd tix4-scraper
  python run_pipeline.py
  python run_pipeline.py --max-shows 2 --max-events 1   # quick test

Setup:
  - Create local_creds.py with get_db_info() and get_proxy_info().
  - Start MySQL: docker compose up -d
  - Apply schema: mysql -h 127.0.0.1 -P 3307 -u root -ppassword tix4 < schema.sql
"""
import argparse
import sys
import time
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Use local_creds as creds so handlers find get_db_info / get_proxy_info
import local_creds
sys.modules["creds"] = local_creds

from tix4_show_discovery import lambda_handler as discovery_handler
from tix4_determine_scraper import lambda_handler as determine_handler
from tix4_provide_schedule_params_list import lambda_handler as schedule_params_handler
from tix4_event_schedule import lambda_handler as event_schedule_handler
from tix4_provide_inventory_params_list import lambda_handler as inventory_params_handler
from event_inventory_main import lambda_handler as event_inventory_handler


def run(args):
    context = None

    # === 1. Show discovery ===
    print("=== 1. Show discovery (populate event_discovery) ===")
    result = discovery_handler({}, context)
    print(f"Result: {result}\n")

    # === 2. Determine scraper_type per show ===
    print("=== 2. Determine scraper_type (per show) ===")
    schedule_payloads = schedule_params_handler({"time_frame": args.time_frame}, context)
    if not schedule_payloads:
        print("No active shows. Run discovery first.")
        return
    print(f"Got {len(schedule_payloads)} shows")
    if args.max_shows is not None:
        schedule_payloads = schedule_payloads[:args.max_shows]
        print(f"Limited to {len(schedule_payloads)} shows (--max-shows)\n")

    for i, payload in enumerate(schedule_payloads):
        slug = payload["show_slug"]
        print(f"  [{i+1}/{len(schedule_payloads)}] {slug}")
        try:
            determine_handler({"show_slug": slug}, context)
        except Exception as e:
            print(f"    ERROR: {e!r}")
        time.sleep(args.delay_between_shows)
    print()

    # === 3. Event schedule (per show) ===
    print("=== 3. Event schedule (per show) ===")
    # Re-fetch after determine updated scraper_type
    schedule_payloads = schedule_params_handler({"time_frame": args.time_frame}, context)
    if args.max_shows is not None:
        schedule_payloads = schedule_payloads[:args.max_shows]

    for i, payload in enumerate(schedule_payloads):
        slug = payload["show_slug"]
        print(f"  [{i+1}/{len(schedule_payloads)}] {slug}")
        try:
            event_schedule_handler({"show_slug": slug, "scraper_type": payload.get("scraper_type")}, context)
        except Exception as e:
            print(f"    ERROR: {e!r}")
        time.sleep(args.delay_between_shows)
    print()

    # === 4. Inventory params ===
    print("=== 4. Inventory params ===")
    inventory_payloads = inventory_params_handler({
        "days_ahead": args.days_ahead,
        "time_frame": args.time_frame,
    }, context)
    if not inventory_payloads:
        print("No events in range.")
        return
    print(f"Got {len(inventory_payloads)} events")
    if args.max_events is not None:
        inventory_payloads = inventory_payloads[:args.max_events]
        print(f"Limited to {len(inventory_payloads)} events (--max-events)\n")

    # === 5. Event inventory (per event) ===
    print("=== 5. Event inventory (per event) ===")
    for i, payload in enumerate(inventory_payloads):
        eid = payload.get("eventId", "?")
        name = payload.get("event_name", "")[:40]
        print(f"  [{i+1}/{len(inventory_payloads)}] {eid} {name}")
        try:
            event_inventory_handler(payload, context)
        except Exception as e:
            print(f"    ERROR: {e!r}")
        time.sleep(args.delay_between_events)

    print("\nDone.")


def main():
    from datetime import date
    p = argparse.ArgumentParser(description="Run tix4 pipeline handlers locally.")
    p.add_argument("--days-ahead", type=int, default=15, help="Days ahead for inventory params (default: 15)")
    p.add_argument("--time-frame", type=int, default=0, help="Time frame label (default: 0)")
    p.add_argument("--max-shows", type=int, default=None, help="Max shows to process (default: all)")
    p.add_argument("--max-events", type=int, default=None, help="Max events to scrape (default: all)")
    p.add_argument("--delay-between-shows", type=float, default=0.5, help="Seconds between show calls (default: 0.5)")
    p.add_argument("--delay-between-events", type=float, default=0.3, help="Seconds between inventory calls (default: 0.3)")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
