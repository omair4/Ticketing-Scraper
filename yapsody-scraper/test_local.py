#!/usr/bin/env python3
"""
Local test runner for Yapsody scraper.
Bypasses Lambda infrastructure (creds, pymysql uploads).
Prints results to stdout — no DB writes.

Usage:
    python test_local.py discover
    python test_local.py determine <venue_code> <event_id>
    python test_local.py inventory <workspace_key> <event_key> <chart_key> '<pricing_json>'
    python test_local.py full <venue_code> <event_id>

Examples:
    python test_local.py discover
    python test_local.py determine sycuancasino 852239
    python test_local.py determine dakotamagiccasino 863499
    python test_local.py full sycuancasino 852239
    python test_local.py full dakotamagiccasino 863499
"""

import sys
import os
import json
import asyncio
import importlib.util
import re
from pprint import pprint

# ---------------------------------------------------------------------------
# Inject local_creds as 'creds' so Lambda imports work without /mnt/access
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))
import local_creds as _local_creds
sys.modules["creds"] = _local_creds

import httpx
from bs4 import BeautifulSoup
import pandas as pd


def _load_dotted(name: str, filename: str):
    """Load a module from a file that has dots in its name."""
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ---------------------------------------------------------------------------
# Proxy (from local_creds)
# ---------------------------------------------------------------------------
_ph, _pp, _pu, _ppw = _local_creds.get_proxy_info()
PROXIES = None
if _ph:
    _proxy_url = f"http://{_pu}:{_ppw}@{_ph}:{_pp}"
    PROXIES = _proxy_url  # httpx proxy string

REQUESTS_PROXIES = None
if _ph:
    _proxy_url_rq = f"http://{_pu}:{_ppw}@{_ph}:{_pp}"
    REQUESTS_PROXIES = {"http": _proxy_url_rq, "https": _proxy_url_rq}

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

def cmd_discover():
    print("=== Discover: concerts.yapsody.com ===")
    from yapsody_show_discovery import _scrape_events

    with httpx.Client(headers=HEADERS, timeout=30, proxy=PROXIES) as client:
        resp = client.get("https://concerts.yapsody.com/")
        resp.raise_for_status()
        html = resp.text

    print(f"Fetched {len(html)} bytes")
    events = _scrape_events(html)
    print(f"\nTotal parsed events: {len(events)}")

    yapsody = [e for e in events if e["scraper_type"] == "yapsody_seatsio"]
    external = [e for e in events if e["scraper_type"] == "external_redirect"]

    print(f"  yapsody_seatsio:    {len(yapsody)}")
    print(f"  external_redirect:  {len(external)}")

    print("\n--- Yapsody events ---")
    for e in yapsody:
        print(
            f"  event_id={e['event_id']:10s}  venue={e['venue_code']:25s}"
            f"  name={e['event_name'][:50]}"
        )

    print("\n--- External ---")
    for e in external[:5]:
        print(f"  {e['ticket_url'][:80]}")


# ---------------------------------------------------------------------------
# determine
# ---------------------------------------------------------------------------

def cmd_determine(venue_code: str, event_id: str):
    print(f"=== Determine scraper: venue={venue_code} event={event_id} ===")

    from yapsody_determine_scraper import (
        _extract_performance_id,
        _run_browser,
    )

    booking_url = f"https://{venue_code}.yapsody.com/event/index/{event_id}/"
    print(f"Fetching event page: {booking_url}")

    with httpx.Client(headers=HEADERS, timeout=30, proxy=PROXIES, follow_redirects=True) as client:
        resp = client.get(booking_url)
        resp.raise_for_status()
        html = resp.text
        final_url = str(resp.url)

    print(f"Final URL: {final_url}")
    print(f"HTML size: {len(html)} bytes")

    performance_id = _extract_performance_id(html, event_id)
    print(f"performance_id: {performance_id}")

    if not performance_id:
        print("ERROR: Could not find performance_id in HTML")
        print("--- Snippet of HTML (first 2000 chars) ---")
        print(html[:2000])
        return

    event_key  = f"Performance-{performance_id}"
    ticket_url = f"https://{venue_code}.yapsody.com/event/book/{event_id}/{performance_id}"
    print(f"event_key:  {event_key}")
    print(f"ticket_url: {ticket_url}")

    print("\nLaunching Playwright browser...")
    workspace_key, chart_key, pricing_list = asyncio.run(
        _run_browser(venue_code, event_id, performance_id, _ph, _pp, _pu, _ppw)
    )

    print(f"\nworkspace_key: {workspace_key}")
    print(f"chart_key:     {chart_key}")
    print(f"pricing_list ({len(pricing_list)} categories):")
    pprint(pricing_list)

    return {
        "event_id":       event_id,
        "performance_id": performance_id,
        "event_key":      event_key,
        "workspace_key":  workspace_key,
        "chart_key":      chart_key,
        "pricing_list":   pricing_list,
        "ticket_url":     ticket_url,
    }


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def cmd_inventory(workspace_key: str, event_key: str, chart_key: str,
                  pricing_json: str, event_id: str = "TEST"):
    print(f"=== Inventory: workspace={workspace_key} event_key={event_key} ===")

    seatsio = _load_dotted("seatsio", "event_inventory.seatsio.py")

    pricing_list = json.loads(pricing_json)

    scraper_event = {
        "event_id":       event_id,
        "workspace_key":  workspace_key,
        "event_key":      event_key,
        "chart_key":      chart_key,
        "pricing_list":   pricing_list,
        "event_name":     "Test Event",
        "venue_name":     "Test Venue",
        "event_datetime": "2025-01-01 00:00:00",
        "time_frame":     0,
    }

    df, logs = seatsio.scrape(scraper_event, proxy=REQUESTS_PROXIES)

    print(f"\nTotal seats: {len(df)}")
    if not df.empty:
        print("\nSection counts:")
        print(
            df.groupby("section_name")
              .size()
              .sort_values(ascending=False)
              .to_string()
        )
        print(f"\nPrice range: ${df['total_price'].min():.2f} – ${df['total_price'].max():.2f}")
        print("\nSample rows (first 10):")
        print(df[["section_name", "row", "seat_number", "total_price",
                   "ticket_type", "seat_pattern"]].head(10).to_string(index=False))

    print("\nLogs:")
    pprint({k: v for k, v in logs.items() if k != "stock_by_section"})
    return df, logs


# ---------------------------------------------------------------------------
# full (determine + inventory)
# ---------------------------------------------------------------------------

def cmd_full(venue_code: str, event_id: str):
    print(f"=== Full: venue={venue_code} event={event_id} ===\n")

    result = cmd_determine(venue_code, event_id)
    if not result:
        print("determine step failed — aborting")
        return

    print("\n" + "=" * 60)
    print("Running inventory step...")
    print("=" * 60 + "\n")

    df, logs = cmd_inventory(
        workspace_key=result["workspace_key"],
        event_key=result["event_key"],
        chart_key=result["chart_key"],
        pricing_json=json.dumps(result["pricing_list"]),
        event_id=result["event_id"],
    )
    return df, logs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "discover":
        cmd_discover()

    elif cmd == "determine":
        if len(args) < 3:
            print("Usage: python test_local.py determine <venue_code> <event_id>")
            sys.exit(1)
        cmd_determine(args[1], args[2])

    elif cmd == "inventory":
        if len(args) < 5:
            print("Usage: python test_local.py inventory <workspace_key> <event_key>"
                  " <chart_key> '<pricing_json>'")
            sys.exit(1)
        event_id = args[5] if len(args) > 5 else "TEST"
        cmd_inventory(args[1], args[2], args[3], args[4], event_id)

    elif cmd == "full":
        if len(args) < 3:
            print("Usage: python test_local.py full <venue_code> <event_id>")
            sys.exit(1)
        cmd_full(args[1], args[2])

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
