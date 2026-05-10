"""Stage 3 -- Schedule params fan-out.

Mirrors `tix4_provide_schedule_params_list.py`: takes the show-discovery
output (or runs discovery if not supplied) plus per-show scraper-type info
(or runs determine_scraper for each show), and emits the list of jobs to be
processed in parallel by Stage 4 (`aaanortheast_event_schedule`).

Lambda input:
  {} -- runs Stage 1 + Stage 2 internally
  {"shows": [...]} -- skip Stage 1; for each show, run Stage 2
  {"events": [...]} -- skip Stages 1 + 2 entirely; pass-through

  Optional: "max_shows": int, "save_html": bool

Lambda output:
  list of {"event_id": str, "scraper_type": str, "event_name": str,
           "venue_name": str, "showtimes": list[{date,time}]}
"""
from __future__ import annotations

from aaanortheast_show_discovery import lambda_handler as discover_handler
from aaanortheast_determine_scraper import lambda_handler as determine_handler


def lambda_handler(event, context):
    event = event or {}

    # Either accept pre-built event dicts (from Step Function passthrough) or
    # build them by chaining Stage 1 -> Stage 2.
    events_in = event.get("events")
    if events_in:
        return list(events_in)

    shows = event.get("shows")
    if shows is None:
        disc = discover_handler({"save_html": event.get("save_html", True)}, context)
        shows = disc.get("shows", [])

    max_shows = event.get("max_shows")
    if max_shows is not None:
        shows = shows[:int(max_shows)]

    out: list[dict] = []
    save_html = event.get("save_html", True)
    for show in shows:
        eid = show["event_id"]
        try:
            det = determine_handler(
                {"event_id": eid, "save_html": save_html}, context,
            )
        except Exception as e:
            out.append({
                "event_id": eid,
                "scraper_type": "",
                "event_name": show.get("title", ""),
                "venue_name": "",
                "showtimes": [],
                "error": f"{type(e).__name__}: {e}",
            })
            continue

        if det.get("blocked"):
            out.append({**det, "error": f"blocked: {det.get('block_reason', '')}"})
            continue

        out.append({
            "event_id":     det["event_id"],
            "scraper_type": det["scraper_type"],
            "event_name":   det.get("event_name") or show.get("title", ""),
            "venue_name":   det.get("venue_name") or "",
            "showtimes":    det.get("showtimes", []),
            "red_text":     show.get("red_text", ""),
        })
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(lambda_handler({"max_shows": 2}, None), indent=2)[:4000])
