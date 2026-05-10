#!/usr/bin/env python3
"""Run the AAA Northeast pipeline locally by calling each Lambda handler one
by one. Mirrors `tix4-scraper/run_pipeline.py`.

Usage:
  cd aaanortheast-scraper
  python run_pipeline.py
  python run_pipeline.py --max-shows 2 --qty 6 --days-ahead 30

Setup:
  - Copy local_creds.py.example to local_creds.py and edit if needed.
  - `uv sync` and `uv run python -m camoufox fetch` once.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Inject local_creds as `creds` so Lambdas find get_db_info / get_proxy_info.
try:
    import local_creds  # noqa: E402
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "local_creds.py not found. Copy local_creds.py.example to local_creds.py."
    ) from e
sys.modules["creds"] = local_creds


from aaanortheast_show_discovery import lambda_handler as discover_handler  # noqa: E402
from aaanortheast_provide_schedule_params_list import lambda_handler as schedule_params_handler  # noqa: E402
from aaanortheast_provide_inventory_params_list import lambda_handler as inventory_params_handler  # noqa: E402
from event_inventory_main import lambda_handler as event_inventory_handler  # noqa: E402

OUTPUT_DIR = Path("output")


def run(args) -> int:
    context = None
    run_id = time.strftime("%Y%m%d_%H%M%S")
    print(f"=== run_id={run_id} ===\n")

    # === 1. Show discovery ===
    print("=== 1. Show discovery (with red-text filter) ===")
    disc = discover_handler({"run_id": run_id, "save_html": True}, context)
    n_total = disc.get("num_total", 0)
    n_filtered = disc.get("num_filtered", 0)
    print(f"  All listings: {n_total}    With red text: {n_filtered}")
    if disc.get("error"):
        print(f"  ERROR: {disc['error']}")
        return 2

    shows = disc.get("shows", [])
    if args.max_shows is not None:
        shows = shows[: args.max_shows]
        print(f"  Limited to {len(shows)} shows (--max-shows)")
    if not shows:
        print("  No shows to process. Exiting.")
        return 1
    print()

    # === 2 + 3. Determine + schedule fan-out ===
    print(f"=== 2. Determine scraper_type + extract schedules ({len(shows)} shows) ===")
    events = schedule_params_handler({"shows": shows, "save_html": True}, context)
    by_type: dict[str, int] = {}
    for ev in events:
        by_type[ev.get("scraper_type") or "?"] = by_type.get(ev.get("scraper_type") or "?", 0) + 1
    print(f"  Events with showtimes: {sum(1 for e in events if e.get('showtimes'))}/{len(events)}")
    print(f"  Scraper-type breakdown: {by_type}")
    print()

    # === 4. (passthrough) -- skip in run_pipeline; Stage 4 reads cached HTML
    # which we already produced in Stage 2. Each `event` already has its
    # showtimes inlined. Stage 4 is exercised by test_local.py instead.

    # === 5. Inventory params ===
    print("=== 5. Build inventory jobs ===")
    jobs = inventory_params_handler(
        {
            "events": events,
            "qty": args.qty,
            "time_frame": args.time_frame,
            "days_ahead": args.days_ahead,
        },
        context,
    )
    print(f"  Jobs (one per showtime within {args.days_ahead}d): {len(jobs)}")
    if args.max_jobs is not None:
        jobs = jobs[: args.max_jobs]
        print(f"  Limited to {len(jobs)} jobs (--max-jobs)")
    print()

    # === 6. Event inventory ===
    print("=== 6. Scrape inventory ===")
    n_ok = 0
    n_err = 0
    n_rows_total = 0
    for i, job in enumerate(jobs, start=1):
        label = f"{job['eventId']} {job['event_datetime']}"
        print(f"  [{i}/{len(jobs)}] {label} ({job['scraper_type']})")
        try:
            res = event_inventory_handler({**job, "run_id": run_id}, context)
            n_ok += 1
            n_rows_total += int(res.get("rows", 0))
            print(f"    rows={res.get('rows')} stock={res.get('total_stock')}")
        except Exception as e:
            n_err += 1
            print(f"    ERROR: {type(e).__name__}: {e}")
        time.sleep(args.delay_between_jobs)

    print()
    print(f"=== Summary ===")
    print(f"  jobs OK: {n_ok}/{len(jobs)}    errors: {n_err}    total rows: {n_rows_total}")
    print(f"  CSV:   output/inventory_{run_id}.csv")
    print(f"  Logs:  output/logs_{run_id}.json")
    return 0 if n_err == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Run AAA Northeast pipeline locally.")
    p.add_argument("--qty", type=int, default=6, help="Quantity to request per showtime (default 6)")
    p.add_argument("--time-frame", type=int, default=0, help="time_frame label written to rows (default 0)")
    p.add_argument("--max-shows", type=int, default=None, help="Max shows to process")
    p.add_argument("--max-jobs", type=int, default=None, help="Max inventory jobs to scrape")
    p.add_argument("--days-ahead", type=int, default=90, help="Showtime window in days (default 90)")
    p.add_argument("--delay-between-jobs", type=float, default=0.5, help="Sleep between inventory jobs (default 0.5s)")
    args = p.parse_args()
    try:
        return run(args)
    finally:
        from shared import close_singleton_session
        close_singleton_session()


if __name__ == "__main__":
    sys.exit(main())
