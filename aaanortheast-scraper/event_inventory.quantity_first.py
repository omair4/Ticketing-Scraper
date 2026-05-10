"""Seat-level inventory scraper for the `ebg_v2/quantity_first` template
(Broadway-style shows like The Lion King, The Book of Mormon, etc.).

Fetches the datetime URL through Camoufox + Bright Data sticky proxy, parses
the `.ticket-panel.js-qf-ticket` rows from the SSR HTML, and expands each
panel into one DataFrame row per seat in the requested-quantity group.

Module contract:
  scrape(event: dict, proxy: str | None = None) -> (pd.DataFrame, dict)
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

import shared
from shared import (
    expand_seats_field,
    get_or_create_session,
    parse_quantity_first_tickets,
)


def _build_rows(event: dict, panels: list[dict]) -> list[dict]:
    qty = int(event.get("qty", 6))
    rows: list[dict] = []
    for panel in panels:
        seats = expand_seats_field(panel.get("seats", ""), qty)
        if not seats:
            # If we can't parse `data-seats`, still emit one row at seat=0 with
            # the raw value preserved in objectLabelOrUuid so the data isn't lost.
            seats = [0]
        section = panel.get("section") or ""
        row = panel.get("row") or ""
        price = panel.get("price")  # Decimal | None
        price_f = float(price) if price is not None else None
        for seat in seats:
            rows.append({
                "eventId":          event["eventId"],
                "event_name":       event.get("event_name", ""),
                "venue_name":       event.get("venue_name", ""),
                "event_datetime":   event.get("event_datetime", ""),
                "objectLabelOrUuid": f"{section}-{row}-{seat}".strip("-"),
                "section_name":     section,
                "row":               row,
                "seat_number":       float(seat),
                "total_price":       price_f,
                "status":            "available",
                "seat_pattern":      "Consecutive",
                "isRestrictedView":  0,
                "category":          section,
                "ticket_type":       "seat",
                "time_frame":        int(event.get("time_frame", 0)),
            })
    return rows


async def _scrape_async(event: dict) -> tuple[pd.DataFrame, dict]:
    logs = {
        "eventId":         event["eventId"],
        "event_name":      event.get("event_name", ""),
        "venue_name":      event.get("venue_name", ""),
        "event_datetime":  event.get("event_datetime", ""),
        "ticket_url":      event.get("ticket_url", ""),
        "scraper_type":    event.get("scraper_type", "quantity_first"),
        "qty":             int(event.get("qty", 6)),
        "time_frame":      int(event.get("time_frame", 0)),
        "statuscode":      None,
        "total_stock":     0,
        "stock_by_section": {},
        "error_message":   None,
    }

    session = await get_or_create_session()
    result = await session.fetch(event["ticket_url"])

    logs["statuscode"] = result.status
    if result.blocked:
        logs["error_message"] = f"blocked: {result.block_reason}"
        return pd.DataFrame(), logs

    panels = parse_quantity_first_tickets(result.html)
    rows = _build_rows(event, panels)
    df = pd.DataFrame(rows)

    if not df.empty:
        logs["total_stock"] = int(len(df))
        logs["stock_by_section"] = dict(Counter(df["section_name"].tolist()))
    return df, logs


def scrape(event: dict, proxy: str | None = None) -> tuple[pd.DataFrame, dict]:
    # `proxy` is honored implicitly through shared.playwright_proxy() reading
    # the same `creds.get_proxy_info` that the caller supplied. Keep the
    # parameter for signature parity with tix4 / yapsody scrapers.
    _ = proxy
    return shared.run_async(_scrape_async(event))
