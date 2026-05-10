"""Stage 4 -- Event schedule (per-show passthrough).

For AAA Northeast, the schedule is on the same SSR HTML that Stage 2
(`aaanortheast_determine_scraper`) already fetched. So this Lambda is a
cache reader: re-parses showtimes from `output/events/E<id>.html` if it
exists, otherwise re-fetches the event page.

Kept as a separate stage for symmetry with tix4 (which separates schedule
from determine because tix4's schedule comes from a different endpoint).

Lambda input  : {"event_id": "24885"} -- optional "scraper_type", "use_cache": bool
Lambda output : {"event_id": str, "scraper_type": str, "event_name": str,
                 "venue_name": str, "showtimes": [{date,time}, ...]}
"""
from __future__ import annotations

import os
from pathlib import Path

from aaanortheast_determine_scraper import lambda_handler as determine_handler
from shared import detect_template, extract_event_metadata, parse_showtimes

OUTPUT_DIR = Path(os.environ.get("AAANE_OUTPUT_DIR", "output"))
EVENTS_DIR = OUTPUT_DIR / "events"


def _scraper_type_from_template(template: str) -> str:
    if "quantity_first" in template:
        return "quantity_first"
    if "view_event_datetime" in template:
        return "view_event_datetime"
    return ""


def _read_cache(event_id: str) -> dict | None:
    p = EVENTS_DIR / f"E{event_id}.html"
    if not p.exists():
        return None
    html = p.read_text(encoding="utf-8", errors="replace")
    metadata = extract_event_metadata(html)
    return {
        "event_id":     event_id,
        "scraper_type": _scraper_type_from_template(detect_template(html)),
        "event_name":   metadata["event_name"],
        "venue_name":   metadata["venue_name"],
        "showtimes":    [
            {"date": d, "time": t}
            for (_id, d, t) in parse_showtimes(html)
        ],
    }


def lambda_handler(event, context):
    event = event or {}
    event_id = str(event["event_id"]).lstrip("E")
    use_cache = event.get("use_cache", True)

    if use_cache:
        cached = _read_cache(event_id)
        if cached:
            return cached

    # Fall back to a fresh determine_scraper fetch.
    return determine_handler({"event_id": event_id}, context)


if __name__ == "__main__":
    import json, sys
    eid = sys.argv[1] if len(sys.argv) > 1 else "24885"
    print(json.dumps(lambda_handler({"event_id": eid}, None), indent=2))
