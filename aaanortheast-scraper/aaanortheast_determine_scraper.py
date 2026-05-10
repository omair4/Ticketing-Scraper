"""Stage 2 -- Determine scraper type for one event.

Fetches the event-detail page (`packages.php?...&action=view_event&id=<id>`),
detects which SSR template the site is using (`ebg_v2/quantity_first` vs
`ebg_v2/view_event_datetime`), extracts the event/venue metadata, and lists
all (date, time) showtimes the page surfaces.

Both templates ship inventory in static HTML, so the same template name is
used downstream to pick the right scraper module
(`event_inventory.<scraper_type>.py`).

Lambda input  : {"event_id": "24885"} or {"event_id": "24885", "save_html": False}
Lambda output : {
    "event_id": str,
    "scraper_type": "quantity_first" | "view_event_datetime" | "",
    "event_name": str,
    "venue_name": str,
    "showtimes": [{"date": "YYYY-MM-DD", "time": "HH:MM:SS"}, ...],
    "status": int | None,
    "blocked": bool,
}
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import shared
from shared import (
    EVENT_URL_FMT,
    detect_template,
    extract_event_metadata,
    get_or_create_session,
    parse_showtimes,
)

OUTPUT_DIR = Path(os.environ.get("AAANE_OUTPUT_DIR", "output"))
EVENTS_DIR = OUTPUT_DIR / "events"


def _scraper_type_from_template(template: str) -> str:
    if "quantity_first" in template:
        return "quantity_first"
    if "view_event_datetime" in template:
        return "view_event_datetime"
    return ""


async def _determine(event_id: str, save_html: bool) -> dict:
    url = EVENT_URL_FMT.format(id=event_id)
    session = await get_or_create_session()
    result = await session.fetch(url)

    if result.blocked:
        return {
            "event_id": event_id,
            "scraper_type": "",
            "event_name": "",
            "venue_name": "",
            "showtimes": [],
            "status": result.status,
            "blocked": True,
            "block_reason": result.block_reason,
        }

    if save_html:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        (EVENTS_DIR / f"E{event_id}.html").write_text(
            result.html, encoding="utf-8", errors="replace",
        )

    metadata = extract_event_metadata(result.html)
    showtimes = [
        {"date": d, "time": t}
        for (_id, d, t) in parse_showtimes(result.html)
    ]

    return {
        "event_id":     event_id,
        "scraper_type": _scraper_type_from_template(detect_template(result.html)),
        "event_name":   metadata["event_name"],
        "venue_name":   metadata["venue_name"],
        "showtimes":    showtimes,
        "status":       result.status,
        "blocked":      False,
    }


def lambda_handler(event, context):
    if not isinstance(event, dict):
        raise ValueError("event must be a dict with at least event_id")
    event_id = str(event["event_id"]).lstrip("E")
    save_html = event.get("save_html", True)
    return shared.run_async(_determine(event_id=event_id, save_html=save_html))


if __name__ == "__main__":
    import json, sys
    eid = sys.argv[1] if len(sys.argv) > 1 else "24885"
    print(json.dumps(lambda_handler({"event_id": eid}, None), indent=2))
