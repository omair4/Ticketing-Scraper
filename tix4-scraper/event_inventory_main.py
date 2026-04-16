"""
Lambda 6: Orchestrate seat inventory fetch + MySQL upload.

Input (inventory mode):
  { "eventId": str, "show_slug": str, "scraper_type": str,
    "buy_url_path": str, "event_name": str, "venue_name": str,
    "event_datetime": str, "time_frame": int }

Input (existence_check mode):
  { "mode": "existence_check", ... plus listing_info }

Output (inventory):       {"rows": N, "scraper_type": str, "eventId": str}
Output (existence_check): {"mode": "existence_check", "exists": bool, ...}
"""
import sys
sys.path.append('/mnt/access')

import importlib.util
import json
import os

import pymysql
import pandas as pd

from creds import get_db_info
from creds import get_proxy_info

DATABASE = "tix4"


# ---------------------------------------------------------------------------
# Load dot-named scraper modules
# ---------------------------------------------------------------------------

def _load_dotted_module(name: str, filename: str):
    """Load a file whose name contains dots (e.g. event_inventory.native_seatmap.py)."""
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SCRAPERS = {
    "native_seatmap":   _load_dotted_module("native_seatmap",   "event_inventory.native_seatmap.py"),
    "external_seatmap": _load_dotted_module("external_seatmap", "event_inventory.external_seatmap.py"),
    "native_ga":        _load_dotted_module("native_ga",        "event_inventory.native_ga.py"),
    "external_ga":      _load_dotted_module("external_ga",      "event_inventory.external_ga.py"),
    "native_ga_p6":     _load_dotted_module("native_ga_p6",     "event_inventory.native_ga_p6.py"),
    "js_only_seatmap":  _load_dotted_module("js_only_seatmap",  "event_inventory.js_only_seatmap.py"),
}


def lambda_handler(event, context):
    existence_check = (event.get("mode") == "existence_check")

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = None
    if proxy_host:
        proxy = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"

    # -------------------------------------------------------------------
    # Build scraper_event dict
    # -------------------------------------------------------------------
    if existence_check:
        listing = event.get("listing_info")
        scraper_event = {
            "eventId":        event["eventId"],
            "buy_url":        event.get("buy_url_path", ""),
            "event_name":     event.get("event_name", ""),
            "venue_name":     event.get("venue_name", ""),
            "event_datetime": event.get("event_datetime", ""),
            "time_frame":     0,
            "scraper_type":   event["scraper_type"],
        }
        scraper_type = event["scraper_type"]
    else:
        scraper_event = {
            "eventId":        event["eventId"],
            "buy_url":        event.get("buy_url_path", ""),
            "event_name":     event.get("event_name", ""),
            "venue_name":     event.get("venue_name", "tix4.com"),
            "event_datetime": event.get("event_datetime", ""),
            "time_frame":     event.get("time_frame", 0),
            "scraper_type":   event["scraper_type"],
        }
        scraper_type = event["scraper_type"]

    # -------------------------------------------------------------------
    # Dispatch to scraper module
    # -------------------------------------------------------------------
    scraper_mod = _SCRAPERS.get(scraper_type)
    if scraper_mod is None:
        raise ValueError(f"Unknown scraper_type: {scraper_type!r}")

    print(f"[inventory] {scraper_type} | eventId={scraper_event['eventId']} | {scraper_event['event_name']}")

    df, logs = scraper_mod.scrape(scraper_event, proxy=proxy)

    # -------------------------------------------------------------------
    # Existence check mode
    # -------------------------------------------------------------------
    if existence_check:
        if df.empty:
            return {"mode": "existence_check", "exists": False, "reason": "no_seats"}
        listing = event.get("listing_info", {})
        section = (listing.get("section") or "").lower()
        row_val = (listing.get("row") or "").lower()
        qty = int(listing.get("quantity", 1))
        max_price = float(listing.get("price", 99999))

        matched = df.copy()
        if section:
            matched = matched[matched["section_name"].str.lower() == section]
        if row_val:
            matched = matched[matched["row"].str.lower() == row_val]
        matched = matched[matched["total_price"] <= max_price + 15]

        exists = len(matched) >= qty
        return {
            "mode": "existence_check",
            "exists": exists,
            "matching_seats": len(matched),
            "requested_qty": qty,
        }

    # -------------------------------------------------------------------
    # Inventory mode — upload to DB
    # -------------------------------------------------------------------
    host, user, password = get_db_info()
    if host and not df.empty:
        conn = pymysql.connect(host=host, user=user, password=password,
                               database=DATABASE, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                # Insert inventory rows
                records = df.to_dict(orient="records")
                for rec in records:
                    cur.execute("""
                        INSERT INTO event_inventory
                            (eventId, event_name, venue_name, event_datetime,
                             objectLabelOrUuid, section_name, `row`, seat_number,
                             total_price, status, seat_pattern, isRestrictedView,
                             category, ticket_type, time_frame)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        rec.get("eventId"), rec.get("event_name"), rec.get("venue_name"),
                        rec.get("event_datetime"), rec.get("objectLabelOrUuid"),
                        rec.get("section_name"), rec.get("row"), rec.get("seat_number"),
                        rec.get("total_price"), rec.get("status"), rec.get("seat_pattern"),
                        rec.get("isRestrictedView", 0), rec.get("category"),
                        rec.get("ticket_type"), rec.get("time_frame", 0),
                    ))

                # Insert logs row
                cur.execute("""
                    INSERT INTO event_inventory_logs
                        (eventId, event_name, venue_name, event_datetime,
                         ticket_url, scraper_type, time_frame,
                         statuscode, total_stock, stock_by_section, error_message)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    logs.get("eventId"), logs.get("event_name"), logs.get("venue_name"),
                    logs.get("event_datetime"), logs.get("ticket_url"),
                    logs.get("scraper_type"), logs.get("time_frame"),
                    logs.get("statuscode"), logs.get("total_stock"),
                    json.dumps(logs.get("stock_by_section", {})),
                    logs.get("error_message"),
                ))
            conn.commit()
        finally:
            conn.close()

    num_rows = len(df)
    print(f"[inventory] Done | {num_rows} rows | scraper_type={scraper_type}")
    return {"rows": num_rows, "scraper_type": scraper_type, "eventId": scraper_event["eventId"]}
