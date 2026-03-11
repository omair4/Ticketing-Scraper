import sys
sys.path.append('/mnt/access')

from creds import get_db_info
from creds import get_proxy_info
from creds import json

import pymysql
import requests
import pandas as pd
from datetime import datetime


database = "mgmresorts"

GRAPHQL_URL = "https://api.mgmresorts.com/graphql-next"
AUTH_URL = "https://identityapi.mgmresorts.com/identity/authorization/v2/anon/user/token"


def get_anon_token(proxy=None):
    resp = requests.get(AUTH_URL, proxies=proxy, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or data.get("access_token") or data.get("id_token")
    if not token:
        raise ValueError(f"No token in auth response: {list(data.keys())}")
    return token


def graphql(query, variables, operation_name, token, proxy=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.mgmresorts.com",
        "Referer": "https://www.mgmresorts.com/",
    }
    payload = {"query": query, "variables": variables, "operationName": operation_name}
    resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, proxies=proxy, timeout=30)
    resp.raise_for_status()
    return resp.json()


GET_SEATS = """
query GetSeatsAvailability(
  $showEventId: String!, $programId: String, $numTicket: Int,
  $sections: String, $priceCodes: String, $redemptionCode: String,
  $numAdaTickets: Int, $numCompanionTickets: Int,
  $offerSeatsOnly: Boolean, $propertyId: String
) {
  showBooking {
    seatsAvailability(queryParams: {
      showEventId: $showEventId, programId: $programId,
      numTickets: $numTicket, sections: $sections,
      priceCodes: $priceCodes, redemptionCode: $redemptionCode,
      numAdaTickets: $numAdaTickets, numCompanionTickets: $numCompanionTickets,
      offerSeatsOnly: $offerSeatsOnly, propertyId: $propertyId
    }) {
      limitedAvailability
      comp
      hdePackage
      seats {
        count
        startingPrice
        name
        seatRows {
          name
          seats {
            firstSeat
            lastSeat
            numSeats
            seatIncrement
            priceCode
            basePrice
            discountedPrice
            priceLevel
            holdClass
            ada
            adaCompanion
            vip
          }
        }
      }
      prices {
        code
        description
        price {
          basePrice
          discountedPrice
          basePriceWithServiceFee
          discountedPriceWithServiceFee
          serviceFee
        }
        totalAvailableSeats
        ticketTypeCode
        ticketTypeDescription
        offerApplicable
      }
    }
  }
}
"""


def detect_pattern(first_seat, last_seat, increment):
    if increment == 1:
        return "Consecutive"
    elif increment == 2:
        if first_seat % 2 == 0:
            return "Even"
        else:
            return "Odd"
    return "Consecutive"


def parse_seats(event_info, seats_data):
    """
    Expand GetSeatsAvailability response into flat seat-level rows.

    Returns (df, logs) matching the prekindle pattern.
    """
    event_id = event_info["eventId"]
    event_name = event_info["event_name"]
    venue_name = event_info["venue_name"]
    event_datetime = event_info["event_datetime"]
    property_id = event_info["property_id"]

    availability = seats_data.get("data", {}).get("showBooking", {}).get("seatsAvailability", {})

    if not availability:
        return pd.DataFrame(), {
            "eventId": event_id,
            "event_name": event_name,
            "venue_name": venue_name,
            "event_datetime": event_datetime,
            "ticket_url": event_info.get("ticket_url"),
            "scraper_type": "mgm_internal",
            "time_frame": event_info.get("time_frame", 0),
            "statuscode": 200,
            "total_stock": 0,
            "stock_by_section": {},
            "error_message": "No seatsAvailability in response",
        }

    # Build price code lookup: priceCode → ticketTypeDescription
    price_lookup = {}
    for p in availability.get("prices", []):
        price_lookup[p["code"]] = p.get("ticketTypeDescription", "")

    rows = []
    stock_by_section = {}

    for section in availability.get("seats", []):
        section_name = section["name"]
        section_count = section.get("count", 0)
        stock_by_section[section_name] = section_count

        for seat_row in section.get("seatRows", []):
            row_name = seat_row["name"]

            for seat_range in seat_row.get("seats", []):
                first = seat_range["firstSeat"]
                last = seat_range["lastSeat"]
                increment = seat_range.get("seatIncrement", 1)
                price_code = seat_range.get("priceCode", "")
                discounted_price = seat_range.get("discountedPrice", 0.0)
                ada = seat_range.get("ada", False)
                ada_companion = seat_range.get("adaCompanion", False)
                vip = seat_range.get("vip", False)
                ticket_type = price_lookup.get(price_code, "")
                pattern = detect_pattern(first, last, increment)

                # Expand seat range into individual seat rows
                seat_num = first
                while seat_num <= last:
                    label = f"{section_name}-{row_name}-{seat_num}"
                    rows.append({
                        "eventId": event_id,
                        "event_name": event_name,
                        "venue_name": venue_name,
                        "event_datetime": event_datetime,
                        "objectLabelOrUuid": label,
                        "section_name": section_name,
                        "row": row_name,
                        "seat_number": seat_num,
                        "total_price": float(discounted_price),
                        "seat_pattern": pattern,
                        "isRestrictedView": False,
                        "category": price_code,
                        "ticket_type": ticket_type,
                        "ada": ada or ada_companion,
                        "vip": vip,
                        "time_frame": event_info.get("time_frame", 0),
                    })
                    seat_num += increment

    df = pd.DataFrame(rows)

    logs = {
        "eventId": event_id,
        "event_name": event_name,
        "venue_name": venue_name,
        "event_datetime": event_datetime,
        "ticket_url": event_info.get("ticket_url"),
        "scraper_type": "mgm_internal",
        "time_frame": event_info.get("time_frame", 0),
        "statuscode": 200,
        "total_stock": len(df),
        "stock_by_section": stock_by_section,
        "error_message": None,
    }

    return df, logs


def lambda_handler(event, context):
    ###################### PROXY
    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = {
        'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
        'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
    }

    print(event)

    existence_check = event.get("mode") == "existence_check"

    event_info = {
        "eventId":       event["eventId"],          # "Event_4801603"
        "property_id":   event["property_id"],       # UUID
        "event_name":    event["event_name"],
        "venue_name":    event["venue_name"],
        "event_datetime": event["event_datetime"],
        "ticket_url":    event.get("ticket_url"),
        "time_frame":    event.get("time_frame", 0),
    }
    listing = event.get("listing_info") if existence_check else None

    # Auth
    token = get_anon_token(proxy=proxy)

    # Fetch inventory
    variables = {
        "showEventId": event_info["eventId"],
        "propertyId": event_info["property_id"],
        "offerSeatsOnly": False,
        "programId": None,
        "numTicket": None,
        "sections": None,
        "priceCodes": None,
        "redemptionCode": None,
        "numAdaTickets": None,
        "numCompanionTickets": None,
    }

    seats_data = graphql(GET_SEATS, variables, "GetSeatsAvailability", token, proxy=proxy)

    df, logs = parse_seats(event_info, seats_data)
    num_seats = len(df)
    print(f"{num_seats} seats found")

    # ===========================================================
    # MODE: EXISTENCE CHECK
    # ===========================================================
    if existence_check:
        print("existence check mode")
        orig_section = str(listing["section"]).lower()
        orig_row = listing["row"].upper()
        quantity = int(listing["quantity"])
        orig_cost = float(listing["cost"])

        existence_df = df[df["section_name"].str.lower() == orig_section]
        existence_df = existence_df[existence_df["total_price"].astype(float) <= (orig_cost + 15)]

        def row_to_index(row_str):
            row_str = str(row_str).strip().upper()
            if row_str == "AAA": return 1
            if row_str == "BBB": return 2
            if row_str == "AA":  return 3
            if row_str == "BB":  return 4
            if len(row_str) == 1 and "A" <= row_str <= "Z":
                return 4 + (ord(row_str) - ord("A") + 1)
            return 999

        orig_row_index = row_to_index(orig_row)
        existence_df = existence_df.copy()
        existence_df["row_index"] = existence_df["row"].apply(row_to_index)
        existence_df = existence_df[
            (existence_df["row_index"] <= orig_row_index) &
            ((orig_row_index - existence_df["row_index"]) <= 5)
        ]

        from collections import defaultdict
        runs = defaultdict(list)
        for (row_name, pattern), grp in existence_df.groupby(["row", "seat_pattern"]):
            nums = sorted(grp["seat_number"].astype(int).tolist())
            if not nums:
                continue
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
                    runs[f"{orig_section}_{row_name}_{pattern}"].append(count)
                    count = 1
            runs[f"{orig_section}_{row_name}_{pattern}"].append(count)

        all_runs = [x for sub in runs.values() for x in sub] or [0]
        longest = max(all_runs)

        exists = False
        if quantity == 1:   exists = len(existence_df) >= 1
        elif quantity == 2: exists = (longest >= 3) or (sum(1 for r in all_runs if r >= 2) >= 2)
        elif quantity == 3: exists = longest >= 4 or (sum(1 for r in all_runs if r >= 3) >= 2)
        elif quantity == 4: exists = (longest >= 5) or (sum(1 for r in all_runs if r >= 4) >= 2)
        elif quantity == 5: exists = (longest >= 6) or (sum(1 for r in all_runs if r >= 5) >= 2)
        elif quantity == 6: exists = (longest >= 7) or (sum(1 for r in all_runs if r >= 6) >= 2)

        qualifying_runs_by_row = {
            k: [r for r in v if r >= quantity]
            for k, v in runs.items()
            if any(r >= quantity for r in v)
        }

        return {
            "mode": "existence_check",
            "exists": bool(exists),
            "event_url": event_info.get("ticket_url"),
            "listing": listing,
            "num_runs_ge_quantity": sum(1 for r in all_runs if r >= quantity),
            "runs": qualifying_runs_by_row if exists else dict(runs),
            "all_matching_seats": existence_df[
                ['section_name', 'row', 'seat_number', 'seat_pattern', 'total_price', 'row_index']
            ].to_dict(orient="records"),
            "original_seats": df[
                ['section_name', 'row', 'seat_number', 'seat_pattern', 'total_price']
            ].to_dict(orient="records") if not exists else None,
        }

    # ===========================================================
    # MODE: MYSQL UPLOAD
    # ===========================================================
    print("uploading to MySQL")

    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host, user=user, password=password,
        database=database, autocommit=True
    )

    insert_logs_sql = """
        INSERT INTO event_inventory_logs (
            eventId, event_name, venue_name, event_datetime,
            ticket_url, scraper_type, time_frame,
            statuscode, total_stock, stock_by_section, error_message
        ) VALUES (
            %(eventId)s, %(event_name)s, %(venue_name)s, %(event_datetime)s,
            %(ticket_url)s, %(scraper_type)s, %(time_frame)s,
            %(statuscode)s, %(total_stock)s, %(stock_by_section)s, %(error_message)s
        )
    """

    log_record = {
        "eventId":        logs["eventId"],
        "event_name":     logs["event_name"],
        "venue_name":     logs["venue_name"],
        "event_datetime": logs["event_datetime"],
        "ticket_url":     logs["ticket_url"],
        "scraper_type":   logs["scraper_type"],
        "time_frame":     logs["time_frame"],
        "statuscode":     logs.get("statuscode"),
        "total_stock":    logs.get("total_stock", 0),
        "stock_by_section": json.dumps(logs.get("stock_by_section", {})),
        "error_message":  logs.get("error_message"),
    }

    insert_sql = """
        INSERT INTO event_inventory (
            eventId, event_name, venue_name, event_datetime,
            objectLabelOrUuid, section_name, `row`, seat_number,
            total_price, seat_pattern, isRestrictedView,
            category, ticket_type, time_frame
        ) VALUES (
            %(eventId)s, %(event_name)s, %(venue_name)s, %(event_datetime)s,
            %(objectLabelOrUuid)s, %(section_name)s, %(row)s, %(seat_number)s,
            %(total_price)s, %(seat_pattern)s, %(isRestrictedView)s,
            %(category)s, %(ticket_type)s, %(time_frame)s
        )
    """

    df_insert = df[[
        "eventId", "event_name", "venue_name", "event_datetime",
        "objectLabelOrUuid", "section_name", "row", "seat_number",
        "total_price", "seat_pattern", "isRestrictedView",
        "category", "ticket_type", "time_frame"
    ]].copy() if num_seats > 0 else None

    with conn.cursor() as cur:
        if num_seats > 0:
            records = df_insert.to_dict(orient="records")
            cur.executemany(insert_sql, records)
            print(f"Inserted {len(records)} inventory rows")
        cur.execute(insert_logs_sql, log_record)
        print("Inserted log record")

    return {
        "rows": num_seats,
        "scraper_type": "mgm_internal",
        "eventId": event_info["eventId"],
    }
