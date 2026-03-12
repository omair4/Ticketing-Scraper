#!/usr/bin/env python3
"""
Run the MGM Resorts Step Function pipeline locally by calling each Lambda handler
one by one. Requires MySQL (e.g. Docker) with schema applied and creds configured.

Usage:
  cd mgmresorts-scraper
  python run_pipeline.py
  python run_pipeline.py --max-shows 2 --max-events 1   # quick test

Setup:
  - Copy local_creds.py to creds.py and set DB password (or symlink).
  - Start MySQL (e.g. docker compose up -d), then: mysql mgmresorts < schema.sql
"""
import argparse
import sys
import time
import os

# Ensure we can import from this directory and inject Lambda dependencies before any handler import
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Stub customrequests (Lambda layer) for local runs
import time as _time
class _CustomRequests:
    @staticmethod
    def sleep(seconds):
        _time.sleep(seconds)
    @staticmethod
    def make_request(method, url, **kwargs):
        import requests
        return getattr(requests, method.lower())(url, **kwargs)

sys.modules["customrequests"] = _CustomRequests

# Use local_creds as creds so handlers find get_db_info / get_proxy_info without /mnt/access
import local_creds
sys.modules["creds"] = local_creds

# Now safe to import handlers
from mgmresorts_show_discovery import lambda_handler as discovery_handler
from mgmresorts_provide_schedule_params_list import lambda_handler as schedule_params_handler
from mgmresorts_event_schedule import lambda_handler as event_schedule_handler
from mgmresorts_provide_inventory_params_list import lambda_handler as inventory_params_handler
from event_inventory_main import lambda_handler as event_inventory_handler


def run(args):
    context = None

    print("=== 1. Show discovery (populate event_discovery) ===")
    result = discovery_handler({}, context)
    print(f"Result: {result}\n")

    print("=== 2. Schedule params (list of shows to refresh) ===")
    schedule_payloads = schedule_params_handler({"start_date": args.start_date}, context)
    if not schedule_payloads:
        print("No shows from event_discovery. Run discovery first and ensure DB has mgm_internal shows.")
        return
    print(f"Got {len(schedule_payloads)} shows")
    if args.max_shows is not None:
        schedule_payloads = schedule_payloads[: args.max_shows]
        print(f"Limited to first {len(schedule_payloads)} shows (--max-shows)\n")
    else:
        print()

    print("=== 3. Event schedule (one call per show → populate event_schedule) ===")
    for i, payload in enumerate(schedule_payloads):
        print(f"  [{i+1}/{len(schedule_payloads)}] {payload.get('show_id')}")
        event_schedule_handler(payload, context)
        if args.delay_between_shows > 0:
            time.sleep(args.delay_between_shows)
    print()

    print("=== 4. Inventory params (list of events to scrape) ===")
    inventory_payloads = inventory_params_handler({
        "days_ahead": args.days_ahead,
        "time_frame": args.time_frame,
    }, context)
    if not inventory_payloads:
        print("No events in range. Adjust days_ahead or run schedule step for more shows.")
        return
    print(f"Got {len(inventory_payloads)} events")
    if args.max_events is not None:
        inventory_payloads = inventory_payloads[: args.max_events]
        print(f"Limited to first {len(inventory_payloads)} events (--max-events)\n")
    else:
        print()

    print("=== 5. Event inventory (one call per event → populate event_inventory) ===")
    for i, payload in enumerate(inventory_payloads):
        print(f"  [{i+1}/{len(inventory_payloads)}] {payload.get('eventId')} {payload.get('event_name', '')[:40]}")
        event_inventory_handler(payload, context)
        if args.delay_between_events > 0:
            time.sleep(args.delay_between_events)
    print("Done.")


def main():
    from datetime import date
    p = argparse.ArgumentParser(description="Run MGM Resorts pipeline handlers locally.")
    p.add_argument("--start-date", default=date.today().strftime("%Y-%m-%d"), help="Start date for schedule (default: today)")
    p.add_argument("--days-ahead", type=int, default=30, help="Days ahead for inventory params (default: 30)")
    p.add_argument("--time-frame", type=int, default=0, help="Time frame label (default: 0)")
    p.add_argument("--max-shows", type=int, default=None, help="Max shows to refresh in step 3 (default: all)")
    p.add_argument("--max-events", type=int, default=None, help="Max events to scrape in step 5 (default: all)")
    p.add_argument("--delay-between-shows", type=float, default=0.5, help="Seconds between schedule calls (default: 0.5)")
    p.add_argument("--delay-between-events", type=float, default=0.3, help="Seconds between inventory calls (default: 0.3)")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
