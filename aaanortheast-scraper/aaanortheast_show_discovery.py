"""Stage 1 -- Show discovery.

Fetches the AAA Northeast all-shows-events listing through Camoufox + Bright
Data sticky-session proxy, parses the embedded analytics JSON for the full
event list, and filters down to events whose card has a non-empty `.grid-card-h2`
or `.promo-color` child (the user-visible "red text" badges like
"Save on tickets today" or "Celebrating 15 years on Broadway").

Lambda input  : {} (or {"save_html": false} to skip writing the listing HTML)
Lambda output : {"num_shows": int, "shows": [{event_id, title, event_url, red_text}, ...]}

Outputs (local, when enabled):
  output/listing_<run_id>.html
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import shared
from shared import (
    LISTING_URL,
    EVENT_URL_FMT,
    filter_events_by_red_text,
    get_or_create_session,
    parse_events_json,
)

OUTPUT_DIR = Path(os.environ.get("AAANE_OUTPUT_DIR", "output"))


async def _discover(save_html: bool, run_id: str) -> dict:
    session = await get_or_create_session()
    result = await session.fetch(LISTING_URL)

    if result.blocked:
        return {
            "num_shows": 0,
            "shows": [],
            "error": f"blocked: {result.block_reason}",
            "status": result.status,
        }

    all_items = parse_events_json(result.html)
    filtered = filter_events_by_red_text(result.html, all_items)
    shows = [
        {
            "event_id": str(ev["id"]).lstrip("E"),
            "title":    ev.get("name", ""),
            "event_url": EVENT_URL_FMT.format(id=str(ev["id"]).lstrip("E")),
            "red_text": ev.get("red_text", ""),
        }
        for ev in filtered
        if str(ev.get("id", "")).startswith("E")  # Skip URL/category entries
    ]

    if save_html:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / f"listing_{run_id}.html"
        out.write_text(result.html, encoding="utf-8", errors="replace")

    return {
        "num_shows":     len(shows),
        "shows":         shows,
        "num_total":     len(all_items),
        "num_filtered":  len(shows),
        "status":        result.status,
        "elapsed_s":     result.elapsed_s,
    }


def lambda_handler(event, context):
    save_html = event.get("save_html", True) if isinstance(event, dict) else True
    run_id = event.get("run_id") if isinstance(event, dict) else None
    if not run_id:
        run_id = time.strftime("%Y%m%d_%H%M%S")
    return shared.run_async(_discover(save_html=save_html, run_id=run_id))


if __name__ == "__main__":
    import json
    print(json.dumps(lambda_handler({}, None), indent=2)[:2000])
