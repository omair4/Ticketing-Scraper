"""
Lambda 3: Orchestrate seat inventory fetch + MySQL upload.

Input:
  inventory mode:       { event_id, mode: "inventory", time_frame: N }
  existence_check mode: { mode: "existence_check", event_id, url, eId, wkspc_key,
                          evt_key, chart_key, pricelist, e_name, v_name, date,
                          scraper_type, listing_info }
Output:
  inventory mode:       { rows: N, scraper_type, event_id }
  existence_check mode: { mode, exists, ... }
"""
import sys
sys.path.append('/mnt/access')

from creds import get_db_info
from creds import get_proxy_info
import json

import importlib.util
import os
import pymysql
import pandas as pd


def _load_dotted_module(name: str, filename: str):
    """Load a file whose name contains dots (e.g. event_inventory.seatsio.py)."""
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seatsio_scraper = _load_dotted_module("seatsio", "event_inventory.seatsio.py")

DATABASE = "yapsody"


def lambda_handler(event, context):
    existence_check = (event.get("mode") == "existence_check")

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = None
    if proxy_host:
        proxy = {
            "http":  f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}",
            "https": f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}",
        }

    # -------------------------------------------------------------------
    # Build scraper_event dict
    # -------------------------------------------------------------------
    if existence_check:
        print(f"existence_check mode | listing={event.get('listing_info')}")
        listing = event.get("listing_info")

        scraper_event = {
            "event_id":      event["eId"],
            "ticket_url":    event.get("url", ""),
            "workspace_key": event["wkspc_key"],
            "event_key":     event["evt_key"],
            "chart_key":     event["chart_key"],
            "pricing_list":  event["pricelist"],
            "event_name":    event["e_name"],
            "venue_name":    event["v_name"],
            "event_datetime": event["date"],
            "time_frame":    0,
        }
        scraper_type = event["scraper_type"]

    else:
        print("inventory mode")
        event_id = event["event_id"]
        time_frame = event.get("time_frame", 0)

        # Read full event record from DB
        host, user, password = get_db_info()
        conn = pymysql.connect(
            host=host, user=user, password=password,
            database=DATABASE, autocommit=True,
        )
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT event_id, event_name, venue_name, event_datetime,
                           ticket_url, workspace_key, event_key, chart_key,
                           pricing_list, scraper_type
                    FROM event_discovery
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            raise ValueError(f"event_id={event_id} not found in event_discovery")

        scraper_event = {
            "event_id":       row["event_id"],
            "ticket_url":     row.get("ticket_url", ""),
            "workspace_key":  row["workspace_key"],
            "event_key":      row["event_key"],
            "chart_key":      row["chart_key"],
            "pricing_list":   row["pricing_list"],
            "event_name":     row["event_name"],
            "venue_name":     row["venue_name"],
            "event_datetime": row["event_datetime"],
            "time_frame":     time_frame,
        }
        scraper_type = row["scraper_type"]

    # -------------------------------------------------------------------
    # Run scraper
    # -------------------------------------------------------------------
    print(f"Running scraper | type={scraper_type} | event={scraper_event['event_id']}")

    if scraper_type != "yapsody_seatsio":
        raise ValueError(f"Unsupported scraper_type: {scraper_type}")

    df, logs = seatsio_scraper.scrape(scraper_event, proxy=proxy)
    num_seats = len(df)
    print(f"{num_seats} seats found")

    # -------------------------------------------------------------------
    # EXISTENCE CHECK
    # -------------------------------------------------------------------
    if existence_check:
        result = _existence_check(df, event, listing)
        _upload_to_mysql(df, logs, scraper_type, num_seats, existence_check=True)
        return result

    # -------------------------------------------------------------------
    # INVENTORY MODE — delete old rows + insert new
    # -------------------------------------------------------------------
    _upload_to_mysql(df, logs, scraper_type, num_seats, existence_check=False)

    return {
        "rows":         num_seats,
        "scraper_type": scraper_type,
        "event_id":     scraper_event["event_id"],
    }


# ---------------------------------------------------------------------------
# Existence check
# ---------------------------------------------------------------------------

def _existence_check(df: pd.DataFrame, event: dict, listing: dict) -> dict:
    orig_section = str(listing["section"]).lower()
    orig_row     = listing["row"].upper()
    quantity     = int(listing["quantity"])
    orig_cost    = float(listing["cost"])

    # Step 1: filter by section
    existence_df = df[df["section_name"].str.lower() == orig_section]
    print(f"seats_in_same_section: {len(existence_df)}")

    # Step 2: filter by price (within +15)
    existence_df = existence_df[
        existence_df["total_price"].astype(float) <= (orig_cost + 15)
    ]
    print(f"seats_in_same_section_and_price: {len(existence_df)}")

    # Step 3: filter by row (within 5 rows toward stage)
    existence_df = existence_df.copy()
    existence_df["row_index"] = existence_df["row"].apply(_row_to_index)
    orig_row_index = _row_to_index(orig_row)
    existence_df = existence_df[
        (existence_df["row_index"] <= orig_row_index) &
        ((orig_row_index - existence_df["row_index"]) <= 5)
    ]
    print(f"seats_in_same_section_and_price_and_row: {len(existence_df)}")

    if existence_df.empty:
        return {
            "mode":    "existence_check",
            "exists":  False,
            "reason":  "No seats match section/price/row constraints",
            "event_url": event.get("url"),
            "listing": listing,
            "runs":    [],
            "original_seats": df[
                ["section_name", "row", "seat_number", "seat_pattern", "total_price"]
            ].to_dict(orient="records"),
        }

    # Step 4: find consecutive runs per row
    from collections import defaultdict
    runs = defaultdict(list)

    for (row_name, pattern), grp in existence_df.groupby(["row", "seat_pattern"]):
        nums = sorted(grp["seat_number"].astype(int).tolist())
        if not nums:
            continue
        row_runs = []
        count = 1
        for i in range(1, len(nums)):
            if pattern == "Consecutive":
                adjacent = (nums[i] == nums[i - 1] + 1)
            elif pattern in ("Odd", "Even"):
                adjacent = (nums[i] - nums[i - 1] == 2)
            else:
                adjacent = False
            if adjacent:
                count += 1
            else:
                row_runs.append(count)
                count = 1
        row_runs.append(count)
        runs[f"{orig_section}_{row_name}_{pattern}"] = row_runs

    all_runs = [x for sub in runs.values() for x in sub] or [0]
    longest  = max(all_runs)

    qualifying_runs_by_row = {
        k: [r for r in v if r >= quantity]
        for k, v in runs.items()
        if any(r >= quantity for r in v)
    }

    # Step 5: apply quantity rules
    if quantity == 1:
        exists = len(existence_df) >= 1
    elif quantity == 2:
        exists = (longest >= 3) or (sum(1 for r in all_runs if r >= 2) >= 2)
    elif quantity == 3:
        exists = longest >= 4 or (sum(1 for r in all_runs if r >= 3) >= 2)
    elif quantity == 4:
        exists = (longest >= 5) or (sum(1 for r in all_runs if r >= 4) >= 2)
    elif quantity == 5:
        exists = (longest >= 6) or (sum(1 for r in all_runs if r >= 5) >= 2)
    elif quantity == 6:
        exists = (longest >= 7) or (sum(1 for r in all_runs if r >= 6) >= 2)
    else:
        raise ValueError(f"Invalid quantity: {quantity}")

    return {
        "mode":    "existence_check",
        "exists":  bool(exists),
        "event_url": event.get("url"),
        "listing": listing,
        "num_runs_ge_quantity": sum(1 for r in all_runs if r >= quantity),
        "runs":    qualifying_runs_by_row if exists else dict(runs),
        "all_matching_seats": existence_df[
            ["section_name", "row", "seat_number", "seat_pattern", "total_price", "row_index"]
        ].to_dict(orient="records"),
        "original_seats": df[
            ["section_name", "row", "seat_number", "seat_pattern", "total_price"]
        ].to_dict(orient="records") if not exists else None,
    }


def _row_to_index(row_str: str) -> int:
    row_str = str(row_str).strip().upper()
    if row_str == "AAA": return 1
    if row_str == "BBB": return 2
    if row_str == "AA":  return 3
    if row_str == "BB":  return 4
    if len(row_str) == 1 and "A" <= row_str <= "Z":
        return 4 + (ord(row_str) - ord("A") + 1)
    return 999


# ---------------------------------------------------------------------------
# MySQL upload
# ---------------------------------------------------------------------------

def _upload_to_mysql(df: pd.DataFrame, logs: dict, scraper_type: str,
                     num_seats: int, existence_check: bool):
    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host, user=user, password=password,
        database=DATABASE, autocommit=True,
    )

    insert_logs_sql = """
        INSERT INTO event_inventory_logs (
            event_id, event_name, venue_name, event_datetime,
            ticket_url, scraper_type, time_frame,
            statuscode, total_stock, stock_by_section, error_message
        ) VALUES (
            %(event_id)s, %(event_name)s, %(venue_name)s, %(event_datetime)s,
            %(ticket_url)s, %(scraper_type)s, %(time_frame)s,
            %(statuscode)s, %(total_stock)s, %(stock_by_section)s, %(error_message)s
        )
    """

    log_record = {
        "event_id":       logs["event_id"],
        "event_name":     logs["event_name"],
        "venue_name":     logs["venue_name"],
        "event_datetime": logs["event_datetime"],
        "ticket_url":     logs.get("ticket_url"),
        "scraper_type":   scraper_type,
        "time_frame":     logs.get("time_frame", 0),
        "statuscode":     logs.get("statuscode"),
        "total_stock":    logs.get("total_stock", 0),
        "stock_by_section": json.dumps(logs.get("stock_by_section", {})),
        "error_message":  logs.get("error_message"),
    }

    insert_sql = """
        INSERT INTO event_inventory (
            event_id, event_name, venue_name, event_datetime,
            objectLabelOrUuid, section_name, `row`, seat_number,
            total_price, seat_pattern, isRestrictedView,
            category, ticket_type, time_frame
        ) VALUES (
            %(event_id)s, %(event_name)s, %(venue_name)s, %(event_datetime)s,
            %(objectLabelOrUuid)s, %(section_name)s, %(row)s, %(seat_number)s,
            %(total_price)s, %(seat_pattern)s, %(isRestrictedView)s,
            %(category)s, %(ticket_type)s, %(time_frame)s
        )
    """

    required_cols = [
        "event_id", "event_name", "venue_name", "event_datetime",
        "objectLabelOrUuid", "section_name", "row", "seat_number",
        "total_price", "seat_pattern", "isRestrictedView",
        "category", "ticket_type", "time_frame",
    ]

    try:
        with conn.cursor() as cur:
            if num_seats > 0 and not existence_check:
                # Delete stale rows for this event
                cur.execute(
                    "DELETE FROM event_inventory WHERE event_id = %s",
                    (logs["event_id"],),
                )

                df_insert = df[[c for c in required_cols if c in df.columns]].copy()
                records = df_insert.to_dict(orient="records")
                cur.executemany(insert_sql, records)
                print(f"Inserted {len(records)} inventory rows")

            cur.execute(insert_logs_sql, log_record)
            print("Inserted log row")
    finally:
        conn.close()
