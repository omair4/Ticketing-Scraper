from creds import get_db_info
from creds import get_proxy_info
from creds import json
from bs4 import BeautifulSoup
import io

# import PyPDF2
import pandas as pd
# import img2pdf
import random
import os



import boto3
from botocore.exceptions import ClientError
import pymysql
import requests

import re
from datetime import datetime, timedelta
import base64
import sqlalchemy
from sqlalchemy import create_engine, text
import pymysql

from urllib.parse import urljoin
import time



def run_scraper(scenario: str, event: dict, proxy=None):
    if scenario == "xxxxx":
        from scrapers.direct_api import scrape as run
    elif scenario == "seatsio":
        from scrapers.seatsio import scrape as run
    elif scenario == "general_admission":
        from scrapers.direct_api import scrape as run
    else:
        raise ValueError(f"Unknown scenario: {scenario}")

    return run(event, proxy=proxy)




def lambda_handler(event,context):

    existence_check = (event.get("mode") == "existence_check")
    if existence_check:
        print(f'existence_check: {existence_check}')
        listing = event.get("listing_info")
        print(f'listing: {listing}')

        # ---- Example event payload (this should come from DB / Step Function)
        scraper_event = {
            "ticket_url": event["url"],
            "eventId": event["eId"], 
            "find_tickets_id":event["fId"],
            "workspace_key":event["wkspc_key"],
            "event_key":event["evt_key"],
            "chart_key":event["chart_key"],
            "published_key":event["published_key"],
            "pricing_list":event["pricelist"],
            "event_name": event["e_name"],
            "venue_name": event["v_name"],
            "event_datetime": event["date"],
            "time_frame": 0,
        }
        scraper_type = event["scraper_type"]
        

    else:
        print('regular inventory mode')

        # ---- Example event payload (this should come from DB / Step Function)
        scraper_event = {
            "ticket_url": event["ticket_url"],
            "eventId": event["eventId"],
            "find_tickets_id":event["find_tickets_id"],
            "workspace_key":event["workspace_key"],
            "event_key":event["event_key"],
            "chart_key":event["chart_key"],
            "published_key":event["published_key"],
            "pricing_list":event["pricing_list"],
            "event_name": event["event_name"],
            "venue_name": event["venue_name"],
            "event_datetime": event["event_datetime"],
            "time_frame": event["time_frame"],
        }
        scraper_type = event["scraper_type"]  # e.g. "direct_api"

    host, user, password = get_db_info()
    port = 3306

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = {
        'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
        'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
    }



    logs = None
    df = None
    print(
        f'Scraping EVENT: {scraper_event} ',
        f'scrape_type: {scraper_type}'
    )


    df, logs = run_scraper(
        scenario=scraper_type,
        event=scraper_event,
        proxy=proxy,
    )
    num_seats = len(df)
    print(f'{num_seats} seats found')   


    # ===========================================================
    # MODE: EXISTENCE CHECK
    # ===========================================================
    if existence_check:
        print('existence check upload')

        orig_section  = str(listing["section"]).lower()
        orig_row      = listing["row"].upper()
        quantity      = int(listing["quantity"])
        orig_cost     = float(listing["cost"])

        # STEP 1: Filter SECTION (straight match)
        existence_df = df[ df["section_name"].str.lower() == orig_section ]
        seats_in_same_section = len(existence_df)
        print(f"seats_in_same_section: {seats_in_same_section}")

        # print(existence_df.to_dict(orient="records"))

        # STEP 2: Filter PRICE within ±15
        existence_df = existence_df[ existence_df["total_price"].astype(float) <= (orig_cost + 15) ]
        seats_in_same_section_and_price = len(existence_df)
        print(f"seats_in_same_section_and_price: {seats_in_same_section_and_price}")

        def row_to_index(row_str):
            """
            Stage order:
            AAA = 1
            BBB = 2
            AA  = 3
            BB  = 4
            A   = 5
            B   = 6
            C   = 7
            ...
            """
            row_str = str(row_str).strip().upper()

            # Three-letter rows first
            if row_str == "AAA":
                return 1
            if row_str == "BBB":
                return 2

            # Two-letter rows
            if row_str == "AA":
                return 3
            if row_str == "BB":
                return 4

            # Single-letter rows follow
            if len(row_str) == 1 and "A" <= row_str <= "Z":
                return 4 + (ord(row_str) - ord("A") + 1)

            # Anything unexpected goes last
            return 999

        # STEP 3: Filter ROW within 5 rows forward
        orig_row_index = row_to_index(orig_row)
        existence_df = existence_df.copy()
        existence_df["row_index"] = existence_df["row"].apply(row_to_index)
        existence_df = existence_df[
            (existence_df["row_index"] <= orig_row_index) &        # cannot be behind original row
            ((orig_row_index - existence_df["row_index"]) <= 5)    # within 5 rows toward stage
        ]
        seats_in_same_section_and_price_and_row = len(existence_df)
        print(f"seats_in_same_section_and_price_and_row: {seats_in_same_section_and_price_and_row}")

        # If nothing left → no chance
        if existence_df.empty:
            return {
                "mode": "existence_check",
                "exists": False,
                "reason": "No seats match section/price/row constraints",
                "event_url": event["url"],
                "listing": listing,
                "runs":[],
                "original_seats": df[['section_name','row','seat_number','seat_pattern','total_price']].to_dict(orient="records")
            }


        # STEP 4: Find consecutive runs per row
        from collections import defaultdict

        runs = defaultdict(list)

        # already filtered for section / price

        for (row_name, pattern), grp in existence_df.groupby(["row", "seat_pattern"]):

            nums = sorted(grp["seat_number"].astype(int).tolist())

            if not nums:
                continue

            row_runs = []
            count = 1

            for i in range(1, len(nums)):
                if pattern == "Consecutive":
                    adjacent = (nums[i] == nums[i-1] + 1)
                elif pattern in ("Odd", "Even"):
                    adjacent = (nums[i] - nums[i-1] == 2)
                else:
                    adjacent = False

                if adjacent:
                    count += 1
                else:
                    row_runs.append(count)
                    count = 1

            row_runs.append(count)
            runs[f"{orig_section}_{row_name}_{pattern}"] = row_runs


        # Flatten all runs
        all_runs = [x for sub in runs.values() for x in sub] or [0]
        longest = max(all_runs)

        # filter only those runs that meet the needed threshold
        qualifying_runs_by_row = {
            key: [r for r in run_list if r >= quantity]
            for key, run_list in runs.items()
            if any(r >= quantity for r in run_list)
        }

        # STEP 5: Apply your rules
        exists = False

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
            raise ValueError("Invalid quantity")






# ===========================================================
# MODE: MYSQL UPLOAD
# ===========================================================
    print('uploading to MYSQL')


        
    # Connect to MySQL
    conn = pymysql.connect(
        host= host,
        user=user,
        password=password,
        database='prekindle',
        autocommit=True
    )

    insert_logs_sql = """
        INSERT INTO event_inventory_logs (
            eventId,
            find_tickets_id,
            event_name,
            venue_name,
            event_datetime,
            ticket_url,
            scraper_type,
            time_frame,
            statuscode,
            total_stock,
            stock_by_section,
            error_message
        )
        VALUES (
            %(eventId)s,
            %(find_tickets_id)s,
            %(event_name)s,
            %(venue_name)s,
            %(event_datetime)s,
            %(ticket_url)s,
            %(scraper_type)s,
            %(time_frame)s,
            %(statuscode)s,
            %(total_stock)s,
            %(stock_by_section)s,
            %(error_message)s
        )
    """

    log_record = {
        "eventId": logs["eventId"],
        "find_tickets_id":logs["find_tickets_id"],
        "event_name": logs["event_name"],
        "venue_name": logs["venue_name"],
        "event_datetime": logs["event_datetime"],
        "ticket_url": logs["ticket_url"],
        "scraper_type": scraper_type,
        "time_frame": logs["time_frame"],
        "statuscode": logs.get("statuscode"),
        "total_stock": logs.get("total_stock", 0),
        "stock_by_section": json.dumps(logs.get("stock_by_section", {})),
        "error_message": logs.get("error_message"),
    }

    if num_seats > 0:
        insert_sql = """
            INSERT INTO event_inventory (
                eventId,
                event_name,
                venue_name,
                event_datetime,
                objectLabelOrUuid,
                section_name,
                `row`,
                seat_number,
                total_price,
                seat_pattern,
                isRestrictedView,
                category,
                ticket_type,
                time_frame
            )
            VALUES (
                %(eventId)s,
                %(event_name)s,
                %(venue_name)s,
                %(event_datetime)s,
                %(objectLabelOrUuid)s,
                %(section_name)s,
                %(row)s,
                %(seat_number)s,
                %(total_price)s,
                %(seat_pattern)s,
                %(isRestrictedView)s,
                %(category)s,
                %(ticket_type)s,
                %(time_frame)s
            )
        """

        df_insert = df[
            [
                "eventId",
                "event_name",
                "venue_name",
                "event_datetime",
                "objectLabelOrUuid",
                "section_name",
                "row",
                "seat_number",
                "total_price",
                "seat_pattern",
                "isRestrictedView",
                "category",
                "ticket_type",
                "time_frame"
            ]
        ].copy()

        records = df_insert.to_dict(orient="records")

    with conn.cursor() as cur:
        if num_seats > 0:
            cur.executemany(insert_sql, records)
            print(f"Inserted {len(records)} inventory rows")
        cur.execute(insert_logs_sql, log_record)

    
    print(f"Inserted logs")




    if existence_check:
        return {
            "mode": "existence_check",
            "exists": bool(exists),
            "event_url": event["url"],
            "listing": listing,
            "num_runs_ge_quantity": sum(1 for r in all_runs if r >= quantity), # how many clusters are big enough to satisfy the entire quantity in one row/pattern.
            "runs": qualifying_runs_by_row if exists else runs, # shows qualifed runs if exists, otherwise all runs
            "all_matching_seats": existence_df[['section_name','row','seat_number','seat_pattern','total_price','row_index']].to_dict(orient="records"),
            "original_seats": df[['section_name','row','seat_number','seat_pattern','total_price']].to_dict(orient="records") if not exists else None
        }


    else:

        return {
            "rows": len(df),
            "scraper_type": scraper_type,
            "eventId": scraper_event["eventId"],
        }