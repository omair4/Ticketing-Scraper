"""
Lambda 1: Scrape concerts.yapsody.com → populate event_discovery table.

Input:  {} (no params)
Output: { num_events: N }
"""
import sys
sys.path.append('/mnt/access')

from creds import get_db_info
from creds import get_proxy_info
import json

import httpx
import pymysql
import re
from bs4 import BeautifulSoup
from datetime import datetime
import pandas as pd

DATABASE = "yapsody"
DISCOVERY_URL = "https://concerts.yapsody.com/"

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}


def _classify_url(href: str) -> str:
    """Return scraper_type based on event URL."""
    if not href:
        return "unknown"
    if "yapsody.com/event/index" in href or "yapsody.com/event/book" in href:
        return "yapsody_seatsio"
    if "holdmyticket.com" in href or "ticketmaster.com" in href:
        return "external_redirect"
    # Any non-yapsody ticket link is treated as external
    if "yapsody.com" not in href:
        return "external_redirect"
    return "yapsody_seatsio"


def _parse_venue_code(url: str) -> str | None:
    """Extract subdomain venue code from yapsody URL.

    https://sycuancasino.yapsody.com/event/... → sycuancasino
    """
    match = re.match(r"https?://([^.]+)\.yapsody\.com", url)
    if match:
        code = match.group(1)
        # Skip the top-level domain itself
        if code in ("www", "concerts", "api"):
            return None
        return code
    return None


def _parse_event_id(url: str) -> str | None:
    """Extract event_id from Yapsody event/index URL."""
    match = re.search(r"/event/(?:index|book)/(\d+)", url)
    return match.group(1) if match else None


def _parse_event_datetime(date_str: str) -> datetime | None:
    """Best-effort parse of a human-readable date string from the page."""
    if not date_str:
        return None
    try:
        return pd.to_datetime(date_str, errors="coerce").to_pydatetime()
    except Exception:
        return None


def _scrape_events(html: str) -> list[dict]:
    """Parse concerts.yapsody.com HTML and return list of event dicts."""
    soup = BeautifulSoup(html, "html.parser")
    events = []

    # Yapsody event cards are typically <a> tags with class containing "event"
    # or inside a container like .event-list, .concerts-list, etc.
    # We search broadly for <a> tags whose href matches the yapsody event pattern
    # or links to external vendors.

    seen_ids = set()

    # Strategy 1: find all <a> tags that point to event URLs
    all_links = soup.find_all("a", href=True)

    for link in all_links:
        href = link.get("href", "").strip()

        # Normalise relative URLs
        if href.startswith("/"):
            continue  # relative links on concerts.yapsody.com aren't event pages

        # Must be an absolute URL pointing to a yapsody event or known vendor
        if not href.startswith("http"):
            continue

        scraper_type = _classify_url(href)
        if scraper_type == "unknown":
            continue

        # For yapsody events, extract event_id and venue_code
        event_id = _parse_event_id(href) if scraper_type == "yapsody_seatsio" else None
        venue_code = _parse_venue_code(href) if scraper_type == "yapsody_seatsio" else None

        if scraper_type == "yapsody_seatsio" and not event_id:
            continue

        # Deduplicate by event_id (or href for external)
        dedup_key = event_id if event_id else href
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        # Try to extract text info from the card surrounding this link
        card = link.find_parent(["article", "div", "li", "section"]) or link
        card_text = card.get_text(" ", strip=True)

        # Extract event name: try <h2>, <h3>, <h4>, <strong> inside the card
        name_el = card.find(["h2", "h3", "h4", "strong"])
        event_name = name_el.get_text(strip=True) if name_el else link.get_text(strip=True)
        event_name = event_name[:255] if event_name else ""

        # Venue name: look for patterns like "@ Venue Name" or secondary heading
        venue_name = ""
        venue_match = re.search(r"@\s*(.+?)(?:\s*[-|]|\s*$)", card_text)
        if venue_match:
            venue_name = venue_match.group(1).strip()[:255]

        # Date: look for date-like strings
        date_match = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}[,\s]+\d{4}",
            card_text,
            re.IGNORECASE,
        )
        event_datetime = _parse_event_datetime(date_match.group(0) if date_match else None)

        events.append({
            "event_id": event_id,
            "venue_code": venue_code,
            "event_name": event_name.lower(),
            "venue_name": venue_name,
            "event_datetime": event_datetime,
            "booking_url": href if scraper_type == "yapsody_seatsio" else None,
            "ticket_url": href if scraper_type == "external_redirect" else None,
            "scraper_type": scraper_type,
        })

    return events


def lambda_handler(event, context):
    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxies = None
    if proxy_host:
        proxy_url = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
        proxies = proxy_url  # httpx accepts a single URL string

    print(f"Fetching {DISCOVERY_URL}")

    with httpx.Client(headers=HEADERS, timeout=30, proxy=proxies) as client:
        response = client.get(DISCOVERY_URL)
        response.raise_for_status()
        html = response.text

    print(f"Fetched {len(html)} bytes")

    all_events = _scrape_events(html)
    print(f"Parsed {len(all_events)} events")

    if not all_events:
        print("No events found — check HTML parsing")
        return {"num_events": 0}

    # Filter to only yapsody_seatsio for DB insert (skip external_redirect stubs)
    yapsody_events = [e for e in all_events if e["scraper_type"] == "yapsody_seatsio"]
    print(f"Yapsody seatsio events: {len(yapsody_events)}")

    if not yapsody_events:
        return {"num_events": 0}

    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=DATABASE,
        port=3306,
        charset="utf8mb4",
        autocommit=False,
    )

    insert_sql = """
        INSERT INTO event_discovery (
            event_id,
            venue_code,
            event_name,
            venue_name,
            event_datetime,
            booking_url,
            scraper_type,
            event_status
        ) VALUES (
            %(event_id)s,
            %(venue_code)s,
            %(event_name)s,
            %(venue_name)s,
            %(event_datetime)s,
            %(booking_url)s,
            %(scraper_type)s,
            'active'
        )
        ON DUPLICATE KEY UPDATE
            venue_code      = VALUES(venue_code),
            event_name      = VALUES(event_name),
            venue_name      = VALUES(venue_name),
            event_datetime  = VALUES(event_datetime),
            booking_url     = VALUES(booking_url),
            scraper_type    = VALUES(scraper_type),
            modified_at     = CURRENT_TIMESTAMP
    """

    try:
        with conn.cursor() as cursor:
            for ev in yapsody_events:
                cursor.execute(insert_sql, {
                    "event_id":      ev["event_id"],
                    "venue_code":    ev["venue_code"],
                    "event_name":    ev["event_name"],
                    "venue_name":    ev["venue_name"],
                    "event_datetime": ev["event_datetime"],
                    "booking_url":   ev["booking_url"],
                    "scraper_type":  ev["scraper_type"],
                })
        conn.commit()
        print(f"Upserted {len(yapsody_events)} rows into event_discovery")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"num_events": len(yapsody_events)}
