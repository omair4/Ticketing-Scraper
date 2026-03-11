"""
Lambda 4: Event inventory router for Tixr.

Takes the output of `tixr_determine_scraper.lambda_handler` and routes to the
appropriate scraper implementation:
- open_seating -> scrapers.open_seating.scrape_open_seating(raw_event_data)
- seatsio      -> scrapers.seatsio.scrape_seatsio(workspace_key, event_key, chart_key, pricing_list)
- sold_out     -> empty inventory with a flag.
"""

from __future__ import annotations

from typing import Any, Dict

from .shared.models import InventoryResult, ScraperConfig
from .scrapers import open_seating, seatsio


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Expected input: full dict returned by tixr_determine_scraper.
    """

    cfg = ScraperConfig.model_validate(event)

    if cfg.scraper_type == "open_seating":
        ticket_categories = open_seating.scrape_open_seating(cfg.raw_event_data)
        result = InventoryResult(
            event_id=cfg.event_id,
            scraper_type=cfg.scraper_type,
            ticket_categories=ticket_categories,
        )
    elif cfg.scraper_type == "seatsio":
        seats = seatsio.scrape_seatsio(
            workspace_key=cfg.workspace_key,
            event_key=cfg.event_key,
            chart_key=cfg.chart_key,
            pricing_list=cfg.pricing_list or [],
        )
        result = InventoryResult(
            event_id=cfg.event_id,
            scraper_type=cfg.scraper_type,
            seats=seats,
        )
    else:  # sold_out or unknown
        result = InventoryResult(
            event_id=cfg.event_id,
            scraper_type=cfg.scraper_type,
            ticket_categories=[],
            errors=["sold_out"] if cfg.scraper_type == "sold_out" else [],
        )

    return result.model_dump()

