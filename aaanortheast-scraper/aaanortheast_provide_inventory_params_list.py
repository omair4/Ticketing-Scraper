"""Stage 5 -- Inventory params fan-out.

Walks the determine_scraper output (or runs Stages 1-2 internally) and
expands each (event, showtime) pair into one inventory job. Each job is the
unit of work for Stage 6 (`event_inventory_main`) and is shaped exactly the
way that stage's `lambda_handler` expects.

Lambda input:
  {} -- runs the upstream stages internally
  {"events": [<determine_scraper output>, ...]} -- skip upstream
  Optional knobs:
    "qty":         int   (default DEFAULT_QTY = 6)
    "time_frame":  int   (default 0; carries through to the DataFrame)
    "days_ahead":  int   (default 90; filters showtimes within N days of today)
    "max_shows":   int   (cap on number of events when running upstream)

Lambda output: list of inventory jobs:
  {"eventId":        str,
   "event_name":     str,
   "venue_name":     str,
   "event_datetime": "YYYY-MM-DD HH:MM:SS",
   "ticket_url":     str (with qty appended),
   "scraper_type":   "quantity_first" | "view_event_datetime",
   "qty":            int,
   "time_frame":     int}
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from shared import DATETIME_URL_FMT, DEFAULT_QTY
from aaanortheast_provide_schedule_params_list import lambda_handler as schedule_handler


def _within_window(showtime_date: str, days_ahead: int) -> bool:
    if days_ahead <= 0:
        return True
    try:
        d = datetime.strptime(showtime_date, "%Y-%m-%d").date()
    except ValueError:
        return True  # If we can't parse, don't drop it.
    today = date.today()
    return today <= d <= today + timedelta(days=days_ahead)


def lambda_handler(event, context):
    event = event or {}
    qty = int(event.get("qty", DEFAULT_QTY))
    time_frame = int(event.get("time_frame", 0))
    days_ahead = int(event.get("days_ahead", 90))

    events = event.get("events")
    if events is None:
        events = schedule_handler(
            {
                "max_shows": event.get("max_shows"),
                "save_html": event.get("save_html", True),
                "shows":     event.get("shows"),
            },
            context,
        )

    jobs: list[dict] = []
    for ev in events:
        eid = str(ev.get("event_id", "")).lstrip("E")
        if not eid:
            continue
        scraper_type = ev.get("scraper_type", "")
        if not scraper_type:
            continue
        for showtime in ev.get("showtimes", []):
            d = showtime.get("date", "")
            t = showtime.get("time", "")
            if not d or not t:
                continue
            if not _within_window(d, days_ahead):
                continue
            jobs.append({
                "eventId":        eid,
                "event_name":     ev.get("event_name", ""),
                "venue_name":     ev.get("venue_name", ""),
                "event_datetime": f"{d} {t}",
                "ticket_url":     DATETIME_URL_FMT.format(
                    id=eid, date=d, time=t, qty=qty,
                ),
                "scraper_type":   scraper_type,
                "qty":            qty,
                "time_frame":     time_frame,
            })
    return jobs


if __name__ == "__main__":
    import json
    print(json.dumps(lambda_handler({"max_shows": 2, "qty": 6}, None), indent=2)[:4000])
