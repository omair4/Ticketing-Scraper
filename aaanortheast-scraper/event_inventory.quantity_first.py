"""Seat-level inventory parser for the `ebg_v2/quantity_first` template
(Broadway-style shows like The Lion King, MJ, Hamilton, etc.).

Module contract (called by event_inventory_main.py):
  parse(html: str, event: dict) -> (pd.DataFrame, dict)

Note: the fetch is owned by event_inventory_main so the template can be
auto-detected from the response before dispatching to the right parser.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from shared import (
    expand_seats_field,
    extract_event_metadata,
    parse_quantity_first_tickets,
)


def _build_rows(event: dict, panels: list[dict], venue_name: str) -> list[dict]:
    qty = int(event.get("qty", 6))
    rows: list[dict] = []
    for panel in panels:
        seats = expand_seats_field(panel.get("seats", ""), qty)
        if not seats:
            seats = [0]
        section = panel.get("section") or ""
        row = panel.get("row") or ""
        price = panel.get("price")
        price_f = float(price) if price is not None else None
        for seat in seats:
            rows.append({
                "eventId":           event["eventId"],
                "event_name":        event.get("event_name", ""),
                "venue_name":        venue_name or event.get("venue_name", ""),
                "event_datetime":    event.get("event_datetime", ""),
                "objectLabelOrUuid": f"{section}-{row}-{seat}".strip("-"),
                "section_name":      section,
                "row":                row,
                "seat_number":        float(seat),
                "total_price":        price_f,
                "status":             "available",
                "seat_pattern":       "Consecutive",
                "isRestrictedView":   0,
                "category":           section,
                "ticket_type":        "seat",
                "time_frame":         int(event.get("time_frame", 0)),
            })
    return rows


def parse(html: str, event: dict) -> tuple[pd.DataFrame, dict]:
    """Returns (df, partial_logs). Caller fills statuscode + error_message."""
    metadata = extract_event_metadata(html)
    panels = parse_quantity_first_tickets(html)
    rows = _build_rows(event, panels, venue_name=metadata.get("venue_name", ""))
    df = pd.DataFrame(rows)

    logs_partial = {
        "scraper_type": "quantity_first",
        "venue_name":   metadata.get("venue_name", ""),
        "total_stock":  int(len(df)),
        "stock_by_section": dict(Counter(df["section_name"].tolist())) if not df.empty else {},
        "panels_found": len(panels),
    }
    return df, logs_partial
