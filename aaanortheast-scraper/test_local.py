#!/usr/bin/env python3
"""Local CLI for AAA Northeast scraper -- per-stage testing without the full
pipeline. Mirrors tix4-scraper/test_local.py.

Usage:
  python test_local.py discover
  python test_local.py event <event_id>
  python test_local.py schedule <event_id>
  python test_local.py inventory <event_id> <date> <time> [--qty 6]
  python test_local.py full <event_id> [--qty 6]
  python test_local.py run-all [--max-shows N] [--qty 6]

Examples:
  python test_local.py discover
  python test_local.py event 24885
  python test_local.py inventory 24885 2026-05-09 19:00:00 --qty 6
  python test_local.py full 24885 --qty 6
  python test_local.py run-all --max-shows 3 --qty 6
"""
from __future__ import annotations

import json
import os
import sys
import time
from pprint import pprint

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Inject local_creds as `creds`.
try:
    import local_creds as _local_creds
except ImportError:
    class _local_creds:  # type: ignore[no-redef]
        @staticmethod
        def get_db_info(): return ("", "", "")
        @staticmethod
        def get_proxy_info(): return (None, None, None, None)
sys.modules["creds"] = _local_creds


from aaanortheast_show_discovery import lambda_handler as discover_handler
from aaanortheast_determine_scraper import lambda_handler as determine_handler
from aaanortheast_event_schedule import lambda_handler as event_schedule_handler
from aaanortheast_provide_inventory_params_list import lambda_handler as inventory_params_handler
from event_inventory_main import lambda_handler as event_inventory_handler
from shared import DATETIME_URL_FMT, DEFAULT_QTY


def _arg(args: list[str], flag: str, default: str | None = None) -> str | None:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def _arg_int(args: list[str], flag: str, default: int) -> int:
    v = _arg(args, flag, None)
    return int(v) if v is not None else default


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_discover() -> dict:
    """Stage 1 only."""
    print("=== Stage 1: Show discovery ===")
    out = discover_handler({"run_id": time.strftime("%Y%m%d_%H%M%S")}, None)
    n_total = out.get("num_total", 0)
    n_filt = out.get("num_filtered", 0)
    print(f"  Total listings: {n_total}    With red text: {n_filt}")
    print(f"  Showing first 20 with red text:")
    for s in out.get("shows", [])[:20]:
        print(f"    {s['event_id']:<8} {s['title'][:50]:<52} | {s['red_text'][:60]}")
    return out


def cmd_event(event_id: str) -> dict:
    """Stage 2 only."""
    print(f"=== Stage 2: determine_scraper for E{event_id} ===")
    out = determine_handler({"event_id": event_id}, None)
    print(f"  scraper_type:  {out.get('scraper_type')}")
    print(f"  event_name:    {out.get('event_name')}")
    print(f"  venue_name:    {out.get('venue_name')}")
    showtimes = out.get("showtimes", [])
    print(f"  showtimes ({len(showtimes)}):")
    for st in showtimes[:15]:
        print(f"    {st['date']} {st['time']}")
    if len(showtimes) > 15:
        print(f"    ... and {len(showtimes) - 15} more")
    return out


def cmd_schedule(event_id: str) -> dict:
    """Stage 4 (cached schedule reader)."""
    print(f"=== Stage 4: event_schedule for E{event_id} (cached) ===")
    out = event_schedule_handler({"event_id": event_id, "use_cache": True}, None)
    showtimes = out.get("showtimes", [])
    print(f"  scraper_type:  {out.get('scraper_type')}")
    print(f"  event_name:    {out.get('event_name')}")
    print(f"  showtimes:     {len(showtimes)}")
    for st in showtimes[:10]:
        print(f"    {st['date']} {st['time']}")
    return out


def cmd_inventory(event_id: str, date: str, time_: str, qty: int = DEFAULT_QTY) -> dict:
    """Stage 6 only -- one showtime. Auto-detects scraper_type from response."""
    # Try to get event_name / venue_name from the cached event HTML (cheap).
    sched = event_schedule_handler({"event_id": event_id, "use_cache": True}, None)
    job = {
        "eventId":        event_id,
        "event_name":     sched.get("event_name", "") if sched else "",
        "venue_name":     sched.get("venue_name", "") if sched else "",
        "event_datetime": f"{date} {time_}",
        "ticket_url":     DATETIME_URL_FMT.format(id=event_id, date=date, time=time_, qty=qty),
        "scraper_type":   "",  # auto-detected by event_inventory_main from response
        "qty":            qty,
        "time_frame":     0,
        "run_id":         time.strftime("%Y%m%d_%H%M%S"),
    }
    print(f"=== Stage 6: inventory for E{event_id} {date} {time_} qty={qty} ===")
    print(f"  ticket_url: {job['ticket_url']}")
    out = event_inventory_handler(job, None)
    print(f"  scraper_type: {out.get('scraper_type')}")
    print(f"  rows={out.get('rows')}    total_stock={out.get('total_stock')}")
    if out.get("error"):
        print(f"  error: {out['error']}")
    print(f"  csv:  {out.get('csv_path')}")
    print(f"  log:  {out.get('log_path')}")
    return out


def cmd_full(event_id: str, qty: int = DEFAULT_QTY) -> dict:
    """Stages 2 -> 5 -> 6 for one event (all its showtimes)."""
    print(f"=== Full pipeline for E{event_id} qty={qty} ===\n")
    det = determine_handler({"event_id": event_id}, None)
    if det.get("blocked"):
        print(f"  determine_scraper blocked: {det.get('block_reason')}")
        return {}
    if not det.get("showtimes"):
        print(f"  determine_scraper found no showtimes: {det}")
        return {}
    print(f"  scraper_type:  {det.get('scraper_type') or '(auto-detect at fetch)'}")
    print(f"  event_name:    {det['event_name']}")
    print(f"  venue_name:    {det['venue_name']}")
    print(f"  showtimes:     {len(det['showtimes'])}")
    print()

    jobs = inventory_params_handler(
        {"events": [det], "qty": qty, "days_ahead": 365},
        None,
    )
    print(f"=== Inventory jobs: {len(jobs)} ===\n")
    run_id = time.strftime("%Y%m%d_%H%M%S")
    n_ok = 0
    n_rows_total = 0
    for i, job in enumerate(jobs, start=1):
        label = f"{job['eventId']} {job['event_datetime']}"
        print(f"  [{i}/{len(jobs)}] {label}")
        try:
            res = event_inventory_handler({**job, "run_id": run_id}, None)
            n_ok += 1
            n_rows_total += int(res.get("rows", 0))
            print(f"    rows={res.get('rows')} stock={res.get('total_stock')}")
        except Exception as e:
            print(f"    ERROR: {type(e).__name__}: {e}")
        time.sleep(0.5)

    print()
    print(f"  jobs OK: {n_ok}/{len(jobs)}    total rows: {n_rows_total}")
    print(f"  CSV:  output/inventory_{run_id}.csv")
    print(f"  Logs: output/logs_{run_id}.json")
    return {"jobs_ok": n_ok, "jobs_total": len(jobs), "rows": n_rows_total, "run_id": run_id}


def cmd_run_all(max_shows: int | None = None, qty: int = DEFAULT_QTY) -> None:
    """Discover -> determine -> inventory across N events."""
    import run_pipeline
    class A:  # tiny argparse-shaped stand-in
        max_shows = max_shows
        max_jobs = None
        qty = qty
        time_frame = 0
        days_ahead = 90
        delay_between_jobs = 0.5
    rc = run_pipeline.run(A())
    print(f"\nrun_pipeline rc={rc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0

    cmd = args[0]
    qty = _arg_int(args, "--qty", DEFAULT_QTY)

    try:
        if cmd == "discover":
            cmd_discover()
        elif cmd == "event":
            if len(args) < 2:
                print("Usage: python test_local.py event <event_id>")
                return 2
            cmd_event(args[1])
        elif cmd == "schedule":
            if len(args) < 2:
                print("Usage: python test_local.py schedule <event_id>")
                return 2
            cmd_schedule(args[1])
        elif cmd == "inventory":
            if len(args) < 4:
                print("Usage: python test_local.py inventory <event_id> <date> <time> [--qty 6]")
                return 2
            cmd_inventory(args[1], args[2], args[3], qty=qty)
        elif cmd == "full":
            if len(args) < 2:
                print("Usage: python test_local.py full <event_id> [--qty 6]")
                return 2
            cmd_full(args[1], qty=qty)
        elif cmd == "run-all":
            max_shows = None
            if "--max-shows" in args:
                max_shows = _arg_int(args, "--max-shows", 0) or None
            cmd_run_all(max_shows=max_shows, qty=qty)
        else:
            print(f"Unknown command: {cmd}\n")
            print(__doc__)
            return 2
        return 0
    finally:
        from shared import close_singleton_session
        close_singleton_session()


if __name__ == "__main__":
    sys.exit(main())
