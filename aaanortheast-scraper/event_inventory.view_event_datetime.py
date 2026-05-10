"""Tier-level inventory scraper for the `ebg_v2/view_event_datetime` template
(e.g. Harry Potter and the Cursed Child, where price tiers cover groups of
seats rather than individual seats).

Emits ONE DataFrame row per available tier, with `total_stock` carrying the
maximum buyable quantity exposed by the page (the highest <option value=N>
in the qty <select>).

Module contract:
  scrape(event: dict, proxy: str | None = None) -> (pd.DataFrame, dict)
"""
from __future__ import annotations

import pandas as pd

import shared
from shared import (
    get_or_create_session,
    parse_view_event_datetime_tickets,
)


def _build_rows(event: dict, tiers: list[dict]) -> list[dict]:
    eid = event["eventId"]
    dt = event.get("event_datetime", "")
    rows: list[dict] = []
    for tier in tiers:
        qty_options = tier.get("qty_options") or []
        if not qty_options:
            # Sold out / no available qty -> skip.
            continue
        max_qty = max(qty_options)
        price = tier.get("price")
        price_f = float(price) if price is not None else None
        tier_name = tier.get("tier_name") or ""
        rows.append({
            "eventId":           eid,
            "event_name":        event.get("event_name", ""),
            "venue_name":        event.get("venue_name", ""),
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


async def _scrape_async(event: dict) -> tuple[pd.DataFrame, dict]:
    logs = {
        "eventId":         event["eventId"],
        "event_name":      event.get("event_name", ""),
        "venue_name":      event.get("venue_name", ""),
        "event_datetime":  event.get("event_datetime", ""),
        "ticket_url":      event.get("ticket_url", ""),
        "scraper_type":    event.get("scraper_type", "view_event_datetime"),
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

    tiers = parse_view_event_datetime_tickets(result.html)
    rows = _build_rows(event, tiers)
    df = pd.DataFrame(rows)

    if not df.empty:
        logs["total_stock"] = int(df["total_stock"].sum())
        logs["stock_by_section"] = {
            r["section_name"]: r["total_stock"] for r in rows
        }
    return df, logs


def scrape(event: dict, proxy: str | None = None) -> tuple[pd.DataFrame, dict]:
    _ = proxy  # Honored implicitly via shared.playwright_proxy().
    return shared.run_async(_scrape_async(event))
