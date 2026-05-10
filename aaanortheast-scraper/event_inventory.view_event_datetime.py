"""Tier-level inventory parser for the `ebg_v2/view_event_datetime` template
(e.g. Harry Potter and the Cursed Child).

Module contract (called by event_inventory_main.py):
  parse(html: str, event: dict) -> (pd.DataFrame, dict)

Emits ONE DataFrame row per available tier, with `total_stock` carrying the
maximum buyable quantity (the highest <option value=N> in the qty <select>).
"""
from __future__ import annotations

import pandas as pd

from shared import (
    extract_event_metadata,
    parse_view_event_datetime_tickets,
)


def _build_rows(event: dict, tiers: list[dict], venue_name: str) -> list[dict]:
    eid = event["eventId"]
    dt = event.get("event_datetime", "")
    rows: list[dict] = []
    for tier in tiers:
        qty_options = tier.get("qty_options") or []
        if not qty_options:
            continue
        max_qty = max(qty_options)
        price = tier.get("price")
        price_f = float(price) if price is not None else None
        tier_name = tier.get("tier_name") or ""
        rows.append({
            "eventId":           eid,
            "event_name":        event.get("event_name", ""),
            "venue_name":        venue_name or event.get("venue_name", ""),
            "event_datetime":    dt,
            "objectLabelOrUuid": f"GA-{tier_name}-{eid}-{dt}".strip("-"),
            "section_name":      tier_name,
            "row":               "GA",
            "seat_number":       1.0,
            "total_price":       price_f,
            "status":            "available",
            "seat_pattern":      "Consecutive",
            "isRestrictedView":  0,
            "category":          tier_name,
            "ticket_type":       "GA",
            "time_frame":        int(event.get("time_frame", 0)),
            "total_stock":       int(max_qty),
        })
    return rows


def parse(html: str, event: dict) -> tuple[pd.DataFrame, dict]:
    """Returns (df, partial_logs). Caller fills statuscode + error_message."""
    metadata = extract_event_metadata(html)
    tiers = parse_view_event_datetime_tickets(html)
    rows = _build_rows(event, tiers, venue_name=metadata.get("venue_name", ""))
    df = pd.DataFrame(rows)

    total_stock = int(df["total_stock"].sum()) if not df.empty else 0
    logs_partial = {
        "scraper_type": "view_event_datetime",
        "venue_name":   metadata.get("venue_name", ""),
        "total_stock":  total_stock,
        "stock_by_section": (
            {r["section_name"]: r["total_stock"] for r in rows}
            if rows else {}
        ),
        "tiers_found": len(tiers),
    }
    return df, logs_partial
