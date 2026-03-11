#!/usr/bin/env python3
"""
Local test runner for MGM Resorts scraper.
Bypasses Lambda infrastructure (creds, customrequests, pymysql uploads).
Prints results to stdout.

Usage:
    python test_local.py shows                     # list all shows
    python test_local.py schedule <show_id>        # event schedule for a show
    python test_local.py inventory <event_id>      # seat inventory for one event
    python test_local.py full <show_id>            # show → first event → inventory

Examples:
    python test_local.py shows
    python test_local.py schedule ka-by-cirque-du-soleil
    python test_local.py inventory Event_4801603
    python test_local.py full ka-by-cirque-du-soleil
"""

import sys
import json
import requests
import pandas as pd
from datetime import date
from pprint import pprint

# ─── Endpoints ────────────────────────────────────────────────────────────────
GRAPHQL_URL = "https://api.mgmresorts.com/graphql-next"
AUTH_URL    = "https://identityapi.mgmresorts.com/identity/authorization/v2/anon/user/token"

# ─── Proxy (optional) ─────────────────────────────────────────────────────────
# Set to None to disable, or fill in: "http://user:pass@host:port"
PROXY_URL = None
PROXIES   = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# ─── Auth ─────────────────────────────────────────────────────────────────────

def get_token():
    resp = requests.get(AUTH_URL, proxies=PROXIES, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or data.get("access_token") or data.get("id_token")
    if not token:
        # Try nested structures
        for v in data.values():
            if isinstance(v, str) and len(v) > 50:
                token = v
                break
    if not token:
        print("Auth response keys:", list(data.keys()))
        print("Full response:", json.dumps(data, indent=2)[:500])
        raise ValueError("Could not find token in auth response")
    print(f"[auth] Got token: {token[:40]}...")
    return token


def gql(query, variables, operation_name, token):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.mgmresorts.com",
        "Referer": "https://www.mgmresorts.com/",
    }
    payload = {"query": query, "variables": variables, "operationName": operation_name}
    resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, proxies=PROXIES, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        print("GraphQL errors:", data["errors"])
    return data


# ─── Queries ──────────────────────────────────────────────────────────────────

Q_SHOW_PROPERTIES = "query GetShowProperties { getShows { id propertyId } }"

Q_SHOW = """
query GetShow($showId: String) {
  show(id: $showId) {
    id name productCategory darkDates
    descriptions { short }
    property { id name location { address } }
    arenaTheater { name }
    seasons { id displayName }
  }
}
"""

Q_EVENTS_VIEW = """
query GetShowEventsView(
  $endDate: String, $limit: Int!, $offset: Int!,
  $propertyId: String!, $showId: String!, $startDate: String!
) {
  showBooking {
    eventsViewForShow(queryParams: {
      endDate: $endDate, limit: $limit, offset: $offset,
      propertyId: $propertyId, showId: $showId, startDate: $startDate
    }) {
      events { eventDate eventTime eventId eventCode seasonId offerAvailable lowestPrice }
      lastUpdated
    }
  }
}
"""

Q_SEATS = """
query GetSeatsAvailability(
  $showEventId: String!, $offerSeatsOnly: Boolean, $propertyId: String
) {
  showBooking {
    seatsAvailability(queryParams: {
      showEventId: $showEventId, offerSeatsOnly: $offerSeatsOnly, propertyId: $propertyId
    }) {
      limitedAvailability
      seats {
        count startingPrice name
        seatRows {
          name
          seats {
            firstSeat lastSeat numSeats seatIncrement
            priceCode basePrice discountedPrice
            priceLevel holdClass ada adaCompanion vip
          }
        }
      }
      prices {
        code description
        price { basePriceWithServiceFee discountedPriceWithServiceFee serviceFee }
        totalAvailableSeats ticketTypeDescription
      }
    }
  }
}
"""


# ─── Helpers ─────────────────────────────────────────────────────────────────

def detect_pattern(first, last, increment):
    if increment == 1:
        return "Consecutive"
    elif increment == 2:
        return "Even" if first % 2 == 0 else "Odd"
    return "Consecutive"


def expand_seats(availability, event_id="TEST"):
    rows = []
    price_lookup = {p["code"]: p.get("ticketTypeDescription", "") for p in availability.get("prices", [])}
    for section in availability.get("seats", []):
        for seat_row in section.get("seatRows", []):
            for sr in seat_row.get("seats", []):
                pattern = detect_pattern(sr["firstSeat"], sr["lastSeat"], sr.get("seatIncrement", 1))
                seat_num = sr["firstSeat"]
                while seat_num <= sr["lastSeat"]:
                    rows.append({
                        "eventId":       event_id,
                        "section_name":  section["name"],
                        "row":           seat_row["name"],
                        "seat_number":   seat_num,
                        "total_price":   sr["discountedPrice"],
                        "seat_pattern":  pattern,
                        "category":      sr["priceCode"],
                        "ticket_type":   price_lookup.get(sr["priceCode"], ""),
                        "ada":           sr.get("ada") or sr.get("adaCompanion"),
                        "vip":           sr.get("vip"),
                        "label":         f"{section['name']}-{seat_row['name']}-{seat_num}",
                    })
                    seat_num += sr.get("seatIncrement", 1)
    return pd.DataFrame(rows)


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_shows(token):
    print("\n=== All MGM Shows (GetShowProperties) ===")
    data = gql(Q_SHOW_PROPERTIES, {}, "GetShowProperties", token)
    shows = data.get("data", {}).get("getShows", [])
    print(f"Total shows: {len(shows)}\n")
    for s in shows:
        print(f"  {s['id']:50s}  propertyId={s['propertyId']}")
    return shows


def cmd_schedule(show_id, token):
    print(f"\n=== Show Detail: {show_id} ===")
    show_data = gql(Q_SHOW, {"showId": show_id}, "GetShow", token)
    show = show_data.get("data", {}).get("show", {})
    if not show:
        print("Show not found")
        return []

    property_id = show.get("property", {}).get("id", "")
    print(f"  Name:       {show.get('name')}")
    print(f"  Venue:      {show.get('property', {}).get('name')}")
    print(f"  Address:    {show.get('property', {}).get('location', {}).get('address')}")
    print(f"  Category:   {show.get('productCategory')}")
    print(f"  PropertyID: {property_id}")
    print(f"  Dark dates: {show.get('darkDates')}")

    print(f"\n=== Event Schedule: {show_id} ===")
    today = date.today().strftime("%Y-%m-%d")
    all_events = []
    offset = 0

    while True:
        result = gql(Q_EVENTS_VIEW, {
            "showId": show_id, "propertyId": property_id,
            "startDate": today, "endDate": None, "limit": 20, "offset": offset
        }, "GetShowEventsView", token)
        events = result.get("data", {}).get("showBooking", {}).get("eventsViewForShow", {}).get("events", [])
        if not events:
            break
        all_events.extend(events)
        offset += 20
        if len(events) < 20:
            break

    print(f"  Total upcoming events: {len(all_events)}\n")
    for ev in all_events[:10]:
        print(f"  {ev['eventDate']} {ev['eventTime']:8s}  id={ev['eventId']:15s}  ${ev.get('lowestPrice', '?')}")
    if len(all_events) > 10:
        print(f"  ... and {len(all_events) - 10} more")

    return all_events, property_id


def cmd_inventory(event_id, property_id, token):
    print(f"\n=== Seat Inventory: {event_id} ===")
    result = gql(Q_SEATS, {
        "showEventId": event_id,
        "propertyId": property_id,
        "offerSeatsOnly": False,
    }, "GetSeatsAvailability", token)

    availability = result.get("data", {}).get("showBooking", {}).get("seatsAvailability", {})

    if not availability:
        print("No seatsAvailability in response")
        return

    # Summary
    sections = availability.get("seats", [])
    total = sum(s.get("count", 0) for s in sections)
    print(f"  limitedAvailability: {availability.get('limitedAvailability')}")
    print(f"  Total available seats: {total}")
    print(f"\n  Sections ({len(sections)}):")
    for s in sections:
        print(f"    {s['name']:10s}  {s['count']:4d} seats  starting ${s.get('startingPrice', '?')}")

    # Price codes
    print(f"\n  Price codes ({len(availability.get('prices', []))}):")
    for p in availability.get("prices", []):
        price_info = p.get("price", {})
        print(f"    {p['code']:8s}  {p.get('ticketTypeDescription', ''):25s}  "
              f"base=${price_info.get('basePriceWithServiceFee', '?')}  "
              f"total_avail={p.get('totalAvailableSeats', '?')}")

    # Expand to flat DataFrame
    df = expand_seats(availability, event_id)
    print(f"\n  Expanded to {len(df)} individual seat rows")
    if not df.empty:
        print(df.groupby(["section_name", "category"])[["seat_number"]].count().rename(columns={"seat_number": "count"}))
        print(f"\n  Price range: ${df['total_price'].min():.2f} - ${df['total_price'].max():.2f}")
        print(f"\n  Sample rows:")
        print(df.head(10).to_string(index=False))

    return df


def cmd_full(show_id, token):
    result = cmd_schedule(show_id, token)
    if not result:
        return
    events, property_id = result
    if not events:
        print("No events found")
        return
    first_event = events[0]
    print(f"\nRunning inventory for first event: {first_event['eventId']} on {first_event['eventDate']} {first_event['eventTime']}")
    cmd_inventory(first_event["eventId"], property_id, token)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    try:
        token = get_token()
    except Exception as e:
        print(f"Auth failed: {e}")
        sys.exit(1)

    if cmd == "shows":
        cmd_shows(token)

    elif cmd == "schedule":
        if len(args) < 2:
            print("Usage: python test_local.py schedule <show_id>")
            sys.exit(1)
        cmd_schedule(args[1], token)

    elif cmd == "inventory":
        if len(args) < 2:
            print("Usage: python test_local.py inventory <event_id> [property_id]")
            sys.exit(1)
        event_id = args[1]
        property_id = args[2] if len(args) > 2 else "66964e2b-2550-4476-84c3-1a4c0c5c067f"
        cmd_inventory(event_id, property_id, token)

    elif cmd == "full":
        if len(args) < 2:
            print("Usage: python test_local.py full <show_id>")
            sys.exit(1)
        cmd_full(args[1], token)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
