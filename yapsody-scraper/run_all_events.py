#!/usr/bin/env python3
"""Discover events, scrape each yapsody_seatsio (full), list external_redirect. To-the-point results."""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))
try:
    import local_creds as _c
except ImportError:
    class _c:
        @staticmethod
        def get_db_info(): return None, None, None
        @staticmethod
        def get_proxy_info(): return None, None, None, None
sys.modules["creds"] = _c

import httpx
from datetime import datetime, timezone
from yapsody_show_discovery import _scrape_events
from test_local import cmd_full

HEADERS = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36"}


def main():
    print("DISCOVER")
    resp = httpx.get("https://concerts.yapsody.com/", headers=HEADERS, timeout=45)
    resp.raise_for_status()
    events = _scrape_events(resp.text)
    yapsody = [e for e in events if e["scraper_type"] == "yapsody_seatsio"]
    # Only scrape events that are in the future (live-system behavior); skip expired
    now = datetime.now(timezone.utc)
    def _is_future(dt):
        if dt is None:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= now
    yapsody = [e for e in yapsody if _is_future(e.get("event_datetime"))]
    external = [e for e in events if e["scraper_type"] == "external_redirect"]
    print(f"yapsody_seatsio: {len(yapsody)}  external_redirect: {len(external)}\n")

    print("SEATS.IO EVENTS (full scrape)")
    results = []
    for i, ev in enumerate(yapsody, 1):
        venue, eid = ev["venue_code"], ev["event_id"]
        t0 = time.perf_counter()
        try:
            df, logs = cmd_full(venue, eid)
            elapsed = time.perf_counter() - t0
            if df is None and logs is None:
                print(f"{i}. {venue} {eid}  SKIP (event expired/redirected)  {elapsed:.0f}s")
                results.append((venue, eid, "SKIP", 0, elapsed, None))
            else:
                n = len(df) if df is not None else 0
                print(f"{i}. {venue} {eid}  seats={n}  OK  {elapsed:.0f}s")
                results.append((venue, eid, "OK", n, elapsed, None))
        except Exception as e:
            elapsed = time.perf_counter() - t0
            msg = str(e).replace("\n", " ")[:80]
            print(f"{i}. {venue} {eid}  FAIL  {type(e).__name__}: {msg}  {elapsed:.0f}s")
            results.append((venue, eid, "FAIL", 0, elapsed, str(e)))

    print("\n--- SUMMARY ---")
    ok = [r for r in results if r[2] == "OK"]
    skip = [r for r in results if r[2] == "SKIP"]
    fail = [r for r in results if r[2] == "FAIL"]
    total_seats = sum(r[3] for r in results)
    total_time = sum(r[4] for r in results)
    print(f"  OK: {len(ok)}  SKIP: {len(skip)}  FAIL: {len(fail)}  Total seats: {total_seats}  Time: {total_time:.0f}s")
    if fail:
        print("  Failed:")
        for r in fail:
            print(f"    {r[0]} {r[1]}: {r[5][:60] if r[5] else 'unknown'}")

    print("\nEXTERNAL_REDIRECT (validate reachability)")
    for i, ev in enumerate(external, 1):
        url = ev.get("ticket_url") or ""
        try:
            r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15)
            final = str(r.url)
            status = "OK" if r.status_code == 200 else f"HTTP {r.status_code}"
            if final != url:
                print(f"{i}. {status}  -> {final[:70]}")
            else:
                print(f"{i}. {status}  {url[:70]}")
        except Exception as e:
            print(f"{i}. FAIL  {url[:60]}  ({type(e).__name__})")

    print("\nDONE")


if __name__ == "__main__":
    main()
