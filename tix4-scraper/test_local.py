#!/usr/bin/env python3
"""
Local test runner for tix4 scraper — no DB required.
Runs the same scraper logic as Lambda; skips all DB reads/writes.

Usage:
    python test_local.py discover
    python test_local.py determine <slug>
    python test_local.py schedule <slug>
    python test_local.py inventory <buy_url_path>
    python test_local.py full <slug> [--save OUTPUT_DIR]
    python test_local.py run-all [--max-shows N]

Examples:
    python test_local.py discover
    python test_local.py determine blue-man-group
    python test_local.py schedule penn-and-teller
    python test_local.py inventory "/buy/blue-man-group/635?show_time=2026%2F04%2F17+19%3A00&showtime_id=1799334&v=1"
    python test_local.py full la-comedy-club
    python test_local.py run-all --max-shows 6
"""

import sys
import os
import json
import importlib.util
import time as _time
from pprint import pprint

# ---------------------------------------------------------------------------
# Inject local_creds as 'creds' so Lambda imports work without /mnt/access.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))
try:
    import local_creds as _local_creds
except ImportError:
    class _local_creds:
        @staticmethod
        def get_db_info():
            return None, None, None
        @staticmethod
        def get_proxy_info():
            return None, None, None, None
sys.modules["creds"] = _local_creds

import httpx
import pandas as pd
from bs4 import BeautifulSoup


BASE_URL = "https://tix4.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def _load_dotted(name: str, filename: str):
    """Load a module from a file that has dots in its name."""
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_discover():
    """Discover all shows from /shows."""
    from tix4_show_discovery import _scrape_shows

    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        resp = client.get(f"{BASE_URL}/shows")
        resp.raise_for_status()

    shows = _scrape_shows(resp.text)

    # Group by category
    by_cat: dict[str, list] = {}
    for s in shows:
        by_cat.setdefault(s["category"], []).append(s)

    print(f"\n{'='*60}")
    print(f"DISCOVERY: {len(shows)} shows across {len(by_cat)} categories")
    print(f"{'='*60}")
    for cat, items in sorted(by_cat.items()):
        print(f"\n  {cat} ({len(items)} shows):")
        for s in items:
            price = f"${s['starting_price_usd']:.2f}" if s["starting_price_usd"] else "N/A"
            print(f"    {s['show_slug']:<45} {price}")

    return shows


def cmd_determine(slug: str):
    """Classify one show's scraper_type."""
    from tix4_determine_scraper import _first_buy_url, classify

    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        resp = client.get(f"{BASE_URL}/event/{slug}")
        resp.raise_for_status()
        buy_url = _first_buy_url(resp.text)

        if not buy_url:
            print(f"\n  {slug}: NO buy_url (no upcoming showtimes)")
            return {"scraper_type": None, "buy_url": None}

        full_url = f"{BASE_URL}{buy_url}" if buy_url.startswith("/") else buy_url
        resp2 = client.get(full_url)
        resp2.raise_for_status()

    soup = BeautifulSoup(resp2.text, "lxml")
    scraper_type = classify(soup)

    provider_el = soup.select_one('input[name="external_api_provider_id"]')
    provider_id = provider_el.get("value", "") if provider_el else ""

    print(f"\n  {slug}")
    print(f"    scraper_type:  {scraper_type}")
    print(f"    provider_id:   {provider_id!r}")
    print(f"    buy_url:       {buy_url}")

    return {"scraper_type": scraper_type, "provider_id": provider_id, "buy_url": buy_url}


def cmd_schedule(slug: str):
    """Fetch showtimes for one show."""
    from tix4_event_schedule import _parse_schedule

    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        resp = client.get(f"{BASE_URL}/event/{slug}")
        resp.raise_for_status()

    rows = _parse_schedule(resp.text)

    print(f"\n{'='*60}")
    print(f"SCHEDULE: {slug} — {len(rows)} showtimes")
    print(f"{'='*60}")
    for r in rows:
        dt = r["event_datetime"].strftime("%Y-%m-%d %H:%M") if r["event_datetime"] else "?"
        print(f"  {dt}  token={r['event_id_token']:<20}  showtime_id={r['showtime_id'] or 'N/A'}")

    return rows


def cmd_inventory(buy_url_path: str, scraper_type: str = None):
    """Scrape inventory for one showtime by buy_url_path."""
    if scraper_type is None:
        # Auto-detect scraper_type from the buy page
        full_url = f"{BASE_URL}{buy_url_path}" if buy_url_path.startswith("/") else buy_url_path
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": UA}) as client:
            resp = client.get(full_url)
            resp.raise_for_status()
        from tix4_determine_scraper import classify
        soup = BeautifulSoup(resp.text, "lxml")
        scraper_type = classify(soup)
        print(f"  Auto-detected scraper_type: {scraper_type}")

    mod = _load_dotted(scraper_type, f"event_inventory.{scraper_type}.py")
    scraper_event = {
        "eventId": "LOCAL_TEST",
        "buy_url": buy_url_path,
        "event_name": "Local Test",
        "venue_name": "tix4.com",
        "event_datetime": "",
        "time_frame": 0,
        "scraper_type": scraper_type,
    }

    df, logs = mod.scrape(scraper_event, proxy=None)
    _print_inventory_results(df, logs)
    return df, logs


def cmd_full(slug: str, save_dir: str = None):
    """Full single-show pipeline: determine -> schedule -> first showtime inventory."""
    print(f"\n{'='*60}")
    print(f"FULL PIPELINE: {slug}")
    print(f"{'='*60}")

    # 1. Determine
    print("\n--- Step 1: Determine scraper_type ---")
    det = cmd_determine(slug)
    if not det.get("scraper_type"):
        print("  Cannot proceed — no scraper_type.")
        return pd.DataFrame(), {}

    # 2. Schedule
    print("\n--- Step 2: Fetch schedule ---")
    schedule = cmd_schedule(slug)
    if not schedule:
        print("  No showtimes found.")
        return pd.DataFrame(), {}

    # 3. Inventory (first showtime)
    first = schedule[0]
    print(f"\n--- Step 3: Inventory (first showtime: {first['event_id_token']}) ---")
    df, logs = cmd_inventory(first["buy_url_path"], scraper_type=det["scraper_type"])

    # 4. Save if requested
    if save_dir and not df.empty:
        os.makedirs(save_dir, exist_ok=True)
        json_path = os.path.join(save_dir, f"{slug}_inventory.json")
        csv_path = os.path.join(save_dir, f"{slug}_inventory.csv")
        df.to_json(json_path, orient="records", indent=2)
        df.to_csv(csv_path, index=False)
        print(f"\n  Saved: {json_path}")
        print(f"  Saved: {csv_path}")

    return df, logs


def cmd_run_all(max_shows: int = None):
    """Full pipeline: discover all shows, determine, schedule, inventory."""
    shows = cmd_discover()

    results = []
    total = len(shows) if max_shows is None else min(max_shows, len(shows))
    shows_to_process = shows[:total]

    for i, show in enumerate(shows_to_process):
        slug = show["show_slug"]
        print(f"\n{'='*60}")
        print(f"[{i+1}/{total}] {slug}")
        print(f"{'='*60}")

        try:
            # Determine
            det = cmd_determine(slug)
            if not det.get("scraper_type"):
                results.append({"slug": slug, "status": "no_scraper_type"})
                _time.sleep(0.3)
                continue

            # Schedule
            schedule = cmd_schedule(slug)
            if not schedule:
                results.append({"slug": slug, "status": "no_showtimes"})
                _time.sleep(0.3)
                continue

            # Inventory (first showtime only)
            first = schedule[0]
            df, logs = cmd_inventory(first["buy_url_path"], scraper_type=det["scraper_type"])
            results.append({
                "slug": slug,
                "scraper_type": det["scraper_type"],
                "status": "ok",
                "seats": len(df),
            })

        except Exception as e:
            print(f"  ERROR: {e!r}")
            results.append({"slug": slug, "status": "error", "error": repr(e)})

        _time.sleep(0.3)

    # Summary
    print(f"\n{'='*60}")
    print(f"RUN-ALL SUMMARY: {len(results)} shows processed")
    print(f"{'='*60}")
    ok = [r for r in results if r["status"] == "ok"]
    by_type: dict[str, int] = {}
    for r in ok:
        t = r.get("scraper_type", "?")
        by_type[t] = by_type.get(t, 0) + 1
    print(f"  OK: {len(ok)}, No scraper: {sum(1 for r in results if r['status']=='no_scraper_type')}, "
          f"No showtimes: {sum(1 for r in results if r['status']=='no_showtimes')}, "
          f"Errors: {sum(1 for r in results if r['status']=='error')}")
    print(f"  By type: {by_type}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_inventory_results(df: pd.DataFrame, logs: dict):
    """Print inventory results in yapsody/mgm style."""
    print(f"\n{'='*60}")
    print(f"INVENTORY RESULTS")
    print(f"{'='*60}")

    if df.empty:
        print("  (no seats)")
        print(f"\n  Logs: {logs}")
        return

    # Section summary
    section_summary = (
        df.groupby("section_name")
          .agg(count=("section_name", "size"),
               min_price=("total_price", "min"),
               max_price=("total_price", "max"))
          .sort_values("count", ascending=False)
    )
    print(f"\n  Sections ({len(section_summary)}):")
    for sec, row in section_summary.iterrows():
        print(f"    {sec:<20} {int(row['count']):>5} seats   ${row['min_price']:.2f} - ${row['max_price']:.2f}")

    # Price range
    print(f"\n  Total: {len(df)} seats")
    print(f"  Price range: ${df['total_price'].min():.2f} - ${df['total_price'].max():.2f}")

    # Sample rows
    display_cols = [
        "objectLabelOrUuid", "section_name", "row", "seat_number",
        "total_price", "status", "seat_pattern", "category",
    ]
    available_cols = [c for c in display_cols if c in df.columns]
    print(f"\n  Sample rows (first 10):")
    print(df[available_cols].head(10).to_string(index=False))

    # Logs
    print(f"\n  Logs:")
    print(f"    statuscode:    {logs.get('statuscode')}")
    print(f"    total_stock:   {logs.get('total_stock')}")
    print(f"    scraper_type:  {logs.get('scraper_type')}")
    if logs.get("error_message"):
        print(f"    error:         {logs['error_message']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "discover":
        cmd_discover()

    elif cmd == "determine":
        if len(args) < 2:
            print("Usage: python test_local.py determine <slug>")
            return
        cmd_determine(args[1])

    elif cmd == "schedule":
        if len(args) < 2:
            print("Usage: python test_local.py schedule <slug>")
            return
        cmd_schedule(args[1])

    elif cmd == "inventory":
        if len(args) < 2:
            print("Usage: python test_local.py inventory <buy_url_path>")
            return
        cmd_inventory(args[1])

    elif cmd == "full":
        if len(args) < 2:
            print("Usage: python test_local.py full <slug> [--save OUTPUT_DIR]")
            return
        save_dir = None
        if "--save" in args:
            idx = args.index("--save")
            if idx + 1 < len(args):
                save_dir = args[idx + 1]
        cmd_full(args[1], save_dir=save_dir)

    elif cmd == "run-all":
        max_shows = None
        if "--max-shows" in args:
            idx = args.index("--max-shows")
            if idx + 1 < len(args):
                max_shows = int(args[idx + 1])
        cmd_run_all(max_shows=max_shows)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
