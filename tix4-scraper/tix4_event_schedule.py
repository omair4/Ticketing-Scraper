"""
Lambda 4: Fetch all showtimes for a single show from its event page.

Input:  {"show_slug": str}
Output: {"show_slug": str, "num_showtimes": N}
"""
import sys
sys.path.append('/mnt/access')

import json
import re
from datetime import datetime
from urllib.parse import unquote

import pymysql
import httpx
from bs4 import BeautifulSoup

from creds import get_db_info
from creds import get_proxy_info

DATABASE = "tix4"
BASE_URL = "https://tix4.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def _parse_schedule(event_page_html: str) -> list[dict]:
    """Parse event-calendar-model JSON into schedule rows."""
    soup = BeautifulSoup(event_page_html, "lxml")
    cal = soup.find("script", {"id": "event-calendar-model", "type": "application/json"})
    if cal is None:
        return []
    data = json.loads(cal.string or "[]")

    rows = []
    for day in data:
        for t in day.get("times", []):
            buy_url = t.get("buy_url")
            if not buy_url:
                continue

            # Extract event_id_token from path: /buy/<slug>/<token>?...
            m = re.search(r"/buy/[^/]+/([^/?]+)", buy_url)
            event_id_token = m.group(1) if m else ""

            # Extract showtime_id from query param (absent on Native GA)
            sid_m = re.search(r"showtime_id=([^&]+)", buy_url)
            showtime_id = sid_m.group(1) if sid_m else None

            # Extract datetime from show_time param
            st_m = re.search(r"show_time=([^&]+)", buy_url)
            event_dt = None
            if st_m:
                raw = unquote(st_m.group(1))  # "2026/04/16 19:00"
                try:
                    event_dt = datetime.strptime(raw, "%Y/%m/%d %H:%M")
                except ValueError:
                    pass

            rows.append({
                "event_id_token": event_id_token,
                "showtime_id": showtime_id,
                "event_datetime": event_dt,
                "buy_url_path": buy_url,
            })

    return rows


def lambda_handler(event, context):
    show_slug = event["show_slug"]
    scraper_type = event.get("scraper_type")

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = None
    if proxy_host:
        proxy = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"

    with httpx.Client(
        timeout=30, follow_redirects=True,
        headers={"User-Agent": UA},
        proxy=proxy,
    ) as client:
        resp = client.get(f"{BASE_URL}/event/{show_slug}")
        resp.raise_for_status()

    rows = _parse_schedule(resp.text)
    print(f"[schedule] {show_slug}: {len(rows)} showtimes")

    # DB upsert
    host, user, password = get_db_info()
    if host:
        conn = pymysql.connect(host=host, user=user, password=password,
                               database=DATABASE, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute("""
                        INSERT INTO event_schedule
                            (show_slug, event_id_token, showtime_id,
                             event_datetime, buy_url_path, scraper_type)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            showtime_id = VALUES(showtime_id),
                            event_datetime = VALUES(event_datetime),
                            buy_url_path = VALUES(buy_url_path),
                            scraper_type = VALUES(scraper_type)
                    """, (show_slug, r["event_id_token"], r["showtime_id"],
                          r["event_datetime"], r["buy_url_path"], scraper_type))
            conn.commit()
        finally:
            conn.close()

    return {"show_slug": show_slug, "num_showtimes": len(rows)}
