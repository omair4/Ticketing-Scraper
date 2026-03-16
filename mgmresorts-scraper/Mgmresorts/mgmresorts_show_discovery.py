import sys
sys.path.append('/mnt/access')
# ^^^^^^^^ MUST IMPORT

from creds import get_db_info
from creds import json
from creds import get_proxy_info
from customrequests import make_request
from customrequests import sleep

import pymysql
import json
try:
    from curl_cffi import requests as requests
    _HTTP_EXTRA = {"impersonate": "chrome120"}
except ImportError:
    import requests  # type: ignore[no-redef]
    _HTTP_EXTRA = {}
import time
from datetime import datetime, timezone


database = "mgmresorts"

from mgm_auth import get_anon_token

GRAPHQL_URL = "https://api.mgmresorts.com/graphql-next"

PROPERTY_SUBDOMAIN_MAP = {
    "mgmgrand":        "mgmgrand.mgmresorts.com",
    "bellagio":        "bellagio.mgmresorts.com",
    "parkmgm":         "parkmgm.mgmresorts.com",
    "mandalaybay":     "mandalaybay.mgmresorts.com",
    "luxor":           "luxor.mgmresorts.com",
    "newyorknewyork":  "newyorknewyork.mgmresorts.com",
    "excalibur":       "excalibur.mgmresorts.com",
    "aria":            "aria.mgmresorts.com",
    "cosmopolitan":    "cosmopolitanlasvegas.mgmresorts.com",
    "borgata":         "borgata.mgmresorts.com",
    "mgmnationalharbor": "mgmnationalharbor.mgmresorts.com",
    "mgmgranddetroit": "mgmgranddetroit.mgmresorts.com",
    "mgmspringfield":  "mgmspringfield.mgmresorts.com",
    "mgmnorthfieldpark": "mgmnorthfieldpark.mgmresorts.com",
    "beaurivage":      "beaurivage.mgmresorts.com",
}


def graphql(query, variables, token, proxy=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.mgmresorts.com",
        "Referer": "https://www.mgmresorts.com/",
    }
    payload = {"query": query, "variables": variables}
    resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, proxies=proxy, timeout=30, **_HTTP_EXTRA)
    resp.raise_for_status()
    return resp.json()


GET_SHOW_PROPERTIES = "query GetShowProperties { getShows { id propertyId } }"

GET_SHOW = """
query GetShow($showId: String) {
  show(id: $showId) {
    id
    name
    productCategory
    darkDates
    descriptions { short overview appCard }
    images {
      tile { url metadata { description } }
      overview { url }
    }
    property {
      id
      name
      location { address latitude longitude }
    }
    arenaTheater { name }
    allDayEvent
    seasons { id displayName }
  }
}
"""


def get_booking_url(show_id, property_id, token, proxy=None):
    """
    Determine booking URL and scraper_type for a show.
    Internal MGM shows: {property}.mgmresorts.com/book-show/{show-slug}
    We detect by attempting GetShowEventsView — if it returns events, it's internal.
    """
    # Derive subdomain from property name (fetched in GetShow)
    # We'll try a direct URL pattern — if show has seasons[], it's bookable internally
    return None  # caller handles this based on property domain lookup


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
    ###################### PROXY

    print(event)

    token = get_anon_token(proxy=proxy)
    print("Got anonymous token")

    # Step 1: Get all shows
    result = graphql(GET_SHOW_PROPERTIES, {}, token, proxy=proxy)
    all_show_stubs = result.get("data", {}).get("getShows", [])
    print(f"Found {len(all_show_stubs)} shows from GetShowProperties")

    all_shows = []

    for stub in all_show_stubs:
        show_id = stub.get("id")
        property_id = stub.get("propertyId")

        if not show_id or not property_id:
            continue

        try:
            detail = graphql(GET_SHOW, {"showId": show_id}, token, proxy=proxy)
            show = detail.get("data", {}).get("show", {})
        except requests.exceptions.HTTPError as e:
            if e.response is not None and getattr(e.response, "status_code", None) == 401:
                token = get_anon_token(proxy=proxy)
                try:
                    detail = graphql(GET_SHOW, {"showId": show_id}, token, proxy=proxy)
                    show = detail.get("data", {}).get("show", {})
                except Exception as retry_e:
                    print(f"Error fetching show {show_id}: {retry_e}")
                    continue
            else:
                print(f"Error fetching show {show_id}: {e}")
                continue
        except Exception as e:
            if "401" in str(e):
                token = get_anon_token(proxy=proxy)
                try:
                    detail = graphql(GET_SHOW, {"showId": show_id}, token, proxy=proxy)
                    show = detail.get("data", {}).get("show", {})
                except Exception as retry_e:
                    print(f"Error fetching show {show_id}: {retry_e}")
                    continue
            else:
                print(f"Error fetching show {show_id}: {e}")
                continue

        if not show:
            print(f"No show data for: {show_id}")
            continue

        name = show.get("name", "")
        product_category = show.get("productCategory", "")
        prop = show.get("property", {}) or {}
        prop_name = prop.get("name", "")
        location = prop.get("location", {}) or {}
        venue_address = location.get("address", "")

        # Determine booking URL
        # Look up the subdomain from property name (lowercase, no spaces)
        prop_key = prop_name.lower().replace(" ", "").replace("lasvegas", "").replace("hotelcasino", "").replace("hotel", "").replace("casino", "").replace("resort", "").replace("&", "")
        subdomain = None
        for key, domain in PROPERTY_SUBDOMAIN_MAP.items():
            if key in prop_key or prop_key in key:
                subdomain = domain
                break

        if subdomain:
            booking_url = f"https://{subdomain}/book-show/{show_id}"
            scraper_type = "mgm_internal"
        else:
            booking_url = None
            scraper_type = "unknown"

        print(f"  {show_id} | {prop_name} | type={scraper_type}")

        all_shows.append({
            "show_id": show_id,
            "property_id": property_id,
            "show_name": name,
            "venue_name": prop_name,
            "venue_address": venue_address,
            "booking_url": booking_url,
            "scraper_type": scraper_type,
            "product_category": product_category,
        })

        sleep(0.3)  # gentle rate limiting

    print(f"Total shows processed: {len(all_shows)}")

    # Step 2: Upload to MySQL
    if len(all_shows) == 0:
        return {"num_shows": 0}

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
    INSERT INTO event_discovery (
        show_id,
        property_id,
        show_name,
        venue_name,
        venue_address,
        booking_url,
        scraper_type,
        product_category,
        is_active
    ) VALUES (
        %(show_id)s,
        %(property_id)s,
        %(show_name)s,
        %(venue_name)s,
        %(venue_address)s,
        %(booking_url)s,
        %(scraper_type)s,
        %(product_category)s,
        1
    )
    ON DUPLICATE KEY UPDATE
        property_id      = VALUES(property_id),
        show_name        = VALUES(show_name),
        venue_name       = VALUES(venue_name),
        venue_address    = VALUES(venue_address),
        booking_url      = VALUES(booking_url),
        scraper_type     = VALUES(scraper_type),
        product_category = VALUES(product_category),
        is_active        = 1,
        modified_at      = CURRENT_TIMESTAMP;
    """

    try:
        with conn.cursor() as cursor:
            for show in all_shows:
                cursor.execute(insert_sql, show)
        conn.commit()
        print(f"Upserted {len(all_shows)} shows into event_discovery")
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"num_shows": len(all_shows)}
