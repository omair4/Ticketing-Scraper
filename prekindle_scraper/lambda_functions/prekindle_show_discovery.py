import sys
sys.path.append('/mnt/access')
# ^^^^^^^^ MUST IMPORT

from creds import get_db_info
from creds import json
from creds import get_proxy_info
from customrequests import make_request
from customrequests import sleep

import pymysql

import requests
import json
import pandas as pd
# from bs4 import BeautifulSoup
# import random
import time
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


database = "prekindle"

def lambda_handler(event, context):
    ###################### PROXY ###################### PROXY
    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = {
        'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
        'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
    }
    ###################### PROXY ###################### PROXY


    # get PARAMs from event
    print(event)

    page = event["page"]
    LIMIT = event['limit']
    TIMEZONE = event['timeZone']
    start_time = event["startTime"]
    
    SEARCH_API_URL = "https://web.prekindle.com/api/v1/event/search"

    REQUEST_TIMEOUT = 30

    all_events = []

    params = {
        "page": page,
        "limit": LIMIT,
        "timeZone": TIMEZONE,
        "startTime": start_time,
    }

    headers = {
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'priority': 'u=1, i',
    'referer': 'https://web.prekindle.com/',
    'sec-ch-ua': '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
    }

    response = make_request(
        SEARCH_API_URL,
        method="GET",
        headers=headers,
        params=params,
        proxies=proxy,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()
    data = response.json()
    print(data)

    total_pages = data.get("totalPages", 0)

    searched_events = data.get("searchedEvents", [])

    for event_data in searched_events:

        cta = event_data.get("ctaButtonProps", {})
        ticket_url = cta.get("href", "")
        if "null" in ticket_url.lower():
            continue
        if "prekindle" not in ticket_url.lower():
            ticket_url = "https://www.prekindle.com" + ticket_url

        # 🔥 REMOVE query string manually
        if "?" in ticket_url:
            ticket_url = ticket_url.split("?")[0]
        is_sold_out = cta.get("disabled", False)
        event_status = cta.get("label", "")
        free_event = event_status.strip() == "Free - No Purchase Required"
        if free_event:
            continue

        if "rsvp" in event_status.strip().lower():
            continue


        event_id = None

        if ticket_url:
            # 1️⃣ Try old pattern
            match = re.search(r'/(?:promo|checkout|tickets|find-tickets)/id/(-?\d+)', ticket_url)
            if match:
                event_id = match.group(1)

            # 2️⃣ Try PreKindle-style /event/51487-slug
            if not event_id:
                match = re.search(r'/event/(\d+)-', ticket_url)
                if match:
                    event_id = match.group(1)

        # 3️⃣ Fallback to raw id field
        if not event_id:
            event_id = str(event_data.get("id", ""))

        if not event_id:
            raise ValueError(f"No event ID found for: {event_data}")

        find_tickets_id = None
        if '-' in event_id:
            find_tickets_id = event_id


        # get datetimes
        event_start_time = event_data.get("startTime")
        datetimeraw = pd.to_datetime(event_start_time)
        event_datetime = datetimeraw.tz_localize(None)
        event_datetime_utc = datetimeraw.tz_convert("UTC").tz_localize(None)

        # get venue_name
        detailsSecondary = event_data.get("detailsSecondary")
        # normalize apostrophes
        detailsSecondary = detailsSecondary.replace("'", "")
        normalized = (
            detailsSecondary
                .lower()
                .replace("'", "")
                .replace("&", "and")
                .strip()
        )

        if "dave and busters" in normalized:
            continue

        event_name = (event_data.get("title") or "").lower()

        if any(phrase in event_name for phrase in ["meet & greet", "meet and greet"]):
            continue

        venue_name, venue_city_state = detailsSecondary.rsplit("-", 1)
        venue_name = venue_name.strip()
        venue_city_state = venue_city_state.strip()
        venue_city = venue_city_state.split(",")[0].strip()
        venue_state = venue_city_state.split(",")[1].strip()



        all_events.append({
            "eventId": event_id,
            "find_tickets_id": find_tickets_id,            
            "event_name": event_name,
            "venue_name": venue_name,
            "venue_city": venue_city,
            "venue_state": venue_state,
            "organizer_name": event_data.get("organizerName"),
            "event_datetime": event_datetime,
            "event_datetime_utc": event_datetime_utc,

            "ticket_url": ticket_url,
            "event_status": event_status,
            "is_sold_out": bool(is_sold_out),

        })

        print(f"Processed page {page} | total events: {len(all_events)}")


    print("Total events captured:", len(all_events))



    ####################################################
    # upload to mysql
    ####################################################
    if len(all_events)> 0:
        
        host, user, password = get_db_info()
    
        # Connect to the database and fetch data into a DataFrame
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
        INSERT INTO event_discovery (
            eventId,
            find_tickets_id,
            event_name,
            venue_name,
            venue_city,
            venue_state,
            organizer_name,
            event_datetime,
            event_datetime_utc,
            ticket_url,
            event_status,
            is_sold_out

        ) VALUES (
            %(eventId)s,
            %(find_tickets_id)s,
            %(event_name)s,
            %(venue_name)s,
            %(venue_city)s,
            %(venue_state)s,
            %(organizer_name)s,
            %(event_datetime)s,
            %(event_datetime_utc)s,
            %(ticket_url)s,
            %(event_status)s,

            %(is_sold_out)s

        )
        ON DUPLICATE KEY UPDATE
            event_name          = VALUES(event_name),
            venue_name          = VALUES(venue_name),
            venue_city          = VALUES(venue_city),
            venue_state         = VALUES(venue_state),
            organizer_name      = VALUES(organizer_name),
            event_datetime      = VALUES(event_datetime),
            event_datetime_utc  = VALUES(event_datetime_utc),
            ticket_url          = VALUES(ticket_url),
            event_status        = VALUES(event_status),
            is_sold_out            = VALUES(is_sold_out),
            modified_at         = CURRENT_TIMESTAMP;
        """

        try:
            with conn.cursor() as cursor:
                for ev in all_events:

                    payload = {
                        "eventId": ev.get("eventId"),
                        "find_tickets_id": ev.get("find_tickets_id"),
                        "event_name": ev.get("event_name"),
                        "venue_name": ev.get("venue_name"),
                        "venue_city": ev.get("venue_city"),
                        "venue_state": ev.get("venue_state"),
                        "organizer_name": ev.get("organizer_name"),
                        "event_datetime": ev.get("event_datetime"),
                        "event_datetime_utc": ev.get("event_datetime_utc"),
                        "ticket_url": ev.get("ticket_url"),
                        "event_status": ev.get("event_status"),
                        "is_sold_out": ev.get("is_sold_out")

                    }

                    cursor.execute(insert_sql, payload)

            conn.commit()

        except Exception as e:
            conn.rollback()
            raise

        finally:
            conn.close()
    
    else:
        print(f"Page {page} had no qualifying events.")


    # event_list['event_datetime'] = event_list['event_datetime'].dt.strftime('%m %d %y %I %M %p')

    return {'num_shows': len(all_events)}