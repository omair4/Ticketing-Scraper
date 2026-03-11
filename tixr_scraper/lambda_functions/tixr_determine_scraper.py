"""
Lambda 3: Determine scraper type + extract config for a single Tixr event.

Analogue of `prekindle_determine_scraper.py`:
- Takes `event_id` (and optionally `ticket_url`) from Step Functions.
- Uses Playwright (via shared helpers) to call `/api/events/{eventId}` inside
  the browser context (to satisfy DataDome).
- Classifies the event as `open_seating`, `seatsio`, or `sold_out`.
- Returns a config payload + raw event JSON for downstream inventory.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from .shared.browser_setup import with_tixr_session
from .shared.models import ScraperConfig
from .shared.tixr_api import fetch_event_detail


def _classify_scraper(event_json: Dict[str, Any]) -> Dict[str, Any]:
    """Classify scraper_type and extract seats.io config + pricing list."""

    venue = event_json.get("venue") or {}
    seating_type = (venue.get("seatingType") or "").upper()
    seatsio_cfg = event_json.get("seatsIoConfiguration") or {}

    sales = event_json.get("sales") or []
    has_open_sale = any(s.get("state") == "OPEN" for s in sales if isinstance(s, dict))
    all_closed = sales and all(s.get("state") == "CLOSED" for s in sales if isinstance(s, dict))

    scraper_type: str
    if seatsio_cfg and seating_type == "RESERVED":
        scraper_type = "seatsio"
    elif all_closed and not has_open_sale:
        scraper_type = "sold_out"
    else:
        scraper_type = "open_seating"

    workspace_key = seatsio_cfg.get("workspace", {}).get("key") if isinstance(seatsio_cfg, dict) else None
    event_key = seatsio_cfg.get("event", {}).get("key") if isinstance(seatsio_cfg, dict) else None
    chart_key = seatsio_cfg.get("chart", {}).get("key") if isinstance(seatsio_cfg, dict) else None

    # pricing_list: keep raw sales[] for seatsio/open_seating scrapers
    pricing_list = sales if isinstance(sales, list) else None

    return {
        "scraper_type": scraper_type,
        "workspace_key": workspace_key,
        "event_key": event_key,
        "chart_key": chart_key,
        "pricing_list": pricing_list,
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Expected input:
    {
      "event_id": 178222,
      "ticket_url": "https://www.tixr.com/groups/.../events/...-178222"
    }
    """

    raw_event_id = (event or {}).get("event_id")
    if raw_event_id is None:
        raise ValueError("event_id is required")

    try:
        event_id = int(raw_event_id)
    except (TypeError, ValueError) as exc:  # noqa: BLE001
        raise ValueError(f"invalid event_id: {raw_event_id}") from exc

    async def _run() -> Dict[str, Any]:
        async def _inner(browser, page) -> Dict[str, Any]:
            event_json = await fetch_event_detail(page, event_id)
            if not event_json:
                raise RuntimeError(f"No data returned for event_id={event_id}")

            meta = {
                "name": event_json.get("name"),
                "venue_name": (event_json.get("venue") or {}).get("name"),
                "start_date": event_json.get("startDate"),
                "end_date": event_json.get("endDate"),
                "eventCategoryName": event_json.get("eventCategoryName"),
            }

            classification = _classify_scraper(event_json)

            cfg = ScraperConfig(
                event_id=event_id,
                scraper_type=classification["scraper_type"],  # type: ignore[arg-type]
                workspace_key=classification["workspace_key"],
                event_key=classification["event_key"],
                chart_key=classification["chart_key"],
                pricing_list=classification["pricing_list"],
                raw_event_data=event_json,
                event_meta=meta,
            )
            return cfg.model_dump()

        return await with_tixr_session(_inner)

    result: Dict[str, Any] = asyncio.run(_run())
    return result

