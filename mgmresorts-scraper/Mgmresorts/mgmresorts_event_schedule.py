import sys
sys.path.append('/mnt/access')

from creds import get_db_info
from creds import get_proxy_info
from customrequests import sleep

import pymysql
import json
try:
    from curl_cffi import requests as requests
    _HTTP_EXTRA = {"impersonate": "chrome120"}
except ImportError:
    import requests  # type: ignore[no-redef]
    _HTTP_EXTRA = {}
from datetime import datetime, date


database = "mgmresorts"

from mgm_auth import get_anon_token

GRAPHQL_URL = "https://api.mgmresorts.com/graphql-next"


def graphql(query, variables, operation_name, token, proxy=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.mgmresorts.com",
        "Referer": "https://www.mgmresorts.com/",
        "x-mgm-source": "mgmri",
        "x-mgm-channel": "web",
    }
    payload = {"query": query, "variables": variables, "operationName": operation_name}
    resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, proxies=proxy, timeout=30, **_HTTP_EXTRA)
    resp.raise_for_status()
    return resp.json()


GET_EVENTS_VIEW = """
query GetShowEventsView(
  $endDate: String, $limit: Int!, $offset: Int!,
  $propertyId: String!, $showId: String!, $startDate: String!
) {
  showBooking {
    eventsViewForShow(queryParams: {
      endDate: $endDate, limit: $limit, offset: $offset,
      propertyId: $propertyId, showId: $showId, startDate: $startDate
    }) {
      events {
        eventDate
        eventTime
        eventId
        eventCode
        seasonId
        offerAvailable
        lowestPrice
      }
      lastUpdated
    }
  }
}
"""

PAGE_SIZE = 50


def fetch_all_events(show_id, property_id, start_date, token, proxy=None):
    """Paginate GetShowEventsView until all upcoming events are fetched."""
    all_events = []
    offset = 0

    while True:
        variables = {
            "showId": show_id,
            "propertyId": property_id,
            "startDate": start_date,
            "endDate": None,
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        result = graphql(GET_EVENTS_VIEW, variables, "GetShowEventsView", token, proxy=proxy)
        data = result.get("data", {}).get("showBooking", {}).get("eventsViewForShow", {})
        events = data.get("events", [])

        if not events:
            break

        all_events.extend(events)
        print(f"  Fetched {len(events)} events at offset={offset}, total so far: {len(all_events)}")

        if len(events) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        sleep(0.3)

    return all_events


def lambda_handler(event, context):
    ###################### PROXY
    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    if proxy_host is None:
        proxy = None
    else:
        proxy = {
            'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
            'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
        }

    print(event)

    # Params from event (Step Function input)
    show_id = event["show_id"]          # e.g. "ka-by-cirque-du-soleil"
    property_id = event["property_id"]  # UUID

    start_date = event.get("start_date", date.today().strftime("%Y-%m-%d"))

    token = get_anon_token(proxy=proxy)

    all_events = fetch_all_events(show_id, property_id, start_date, token, proxy=proxy)
    print(f"Total events for {show_id}: {len(all_events)}")

    if not all_events:
        return {"show_id": show_id, "num_events": 0}

    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=3306,
        charset='utf8mb4',
        autocommit=False
    )

    insert_sql = """
    INSERT INTO event_schedule (
        show_id,
        property_id,
        event_id,
        event_code,
        season_id,
        event_date,
        event_time,
        lowest_price,
        offer_available
    ) VALUES (
        %(show_id)s,
        %(property_id)s,
        %(event_id)s,
        %(event_code)s,
        %(season_id)s,
        %(event_date)s,
        %(event_time)s,
        %(lowest_price)s,
        %(offer_available)s
    )
    ON DUPLICATE KEY UPDATE
        lowest_price    = VALUES(lowest_price),
        offer_available = VALUES(offer_available),
        modified_at     = CURRENT_TIMESTAMP;
    """

    records = [
        {
            "show_id":        show_id,
            "property_id":    property_id,
            "event_id":       ev["eventId"],
            "event_code":     ev.get("eventCode"),
            "season_id":      ev.get("seasonId"),
            "event_date":     ev["eventDate"],
            "event_time":     ev["eventTime"],
            "lowest_price":   ev.get("lowestPrice"),
            "offer_available": bool(ev.get("offerAvailable")),
        }
        for ev in all_events
    ]

    try:
        with conn.cursor() as cursor:
            cursor.executemany(insert_sql, records)
        conn.commit()
        print(f"Upserted {len(records)} event schedule rows")
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"show_id": show_id, "num_events": len(records)}
