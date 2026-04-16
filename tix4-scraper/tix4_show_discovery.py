"""
Lambda 1: Discover all shows from https://tix4.com/shows.

Input:  {} (empty)
Output: {"num_shows": N}
"""
import sys
sys.path.append('/mnt/access')

import re
import json
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


def _scrape_shows(html: str) -> list[dict]:
    """Parse /shows HTML and return list of show dicts."""
    soup = BeautifulSoup(html, "lxml")
    shows = []
    seen_slugs: set[str] = set()

    current_category = ""
    for el in soup.select(".row.w-100.c-container *"):
        # Track category headers
        if "event-magazine-title" in (el.get("class") or []):
            current_category = el.get_text(strip=True)
            continue

        # Show cards are <a href="/event/<slug>">
        if el.name == "a" and el.get("href", "").startswith("/event/"):
            slug = el["href"].replace("/event/", "").strip("/")
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            title_el = el.select_one(".event-title")
            title = title_el.get_text(strip=True) if title_el else slug

            price_el = el.select_one(".magazine-discounted-price")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price_match = re.search(r"[\d,.]+", price_text.replace(",", ""))
            starting_price = float(price_match.group()) if price_match else None

            shows.append({
                "show_slug": slug,
                "title": title,
                "category": current_category,
                "starting_price_usd": starting_price,
                "event_url": f"{BASE_URL}/event/{slug}",
            })

    return shows


def lambda_handler(event, context):
    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = None
    if proxy_host:
        proxy = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"

    with httpx.Client(
        timeout=30, follow_redirects=True,
        headers={"User-Agent": UA},
        proxy=proxy,
    ) as client:
        resp = client.get(f"{BASE_URL}/shows")
        resp.raise_for_status()

    shows = _scrape_shows(resp.text)
    print(f"[discovery] Found {len(shows)} shows")

    # DB upsert
    host, user, password = get_db_info()
    if host:
        conn = pymysql.connect(host=host, user=user, password=password,
                               database=DATABASE, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                for s in shows:
                    cur.execute("""
                        INSERT INTO event_discovery
                            (show_slug, title, category, starting_price_usd, event_url)
                        VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            title = VALUES(title),
                            category = VALUES(category),
                            starting_price_usd = VALUES(starting_price_usd),
                            event_url = VALUES(event_url),
                            is_active = 1
                    """, (s["show_slug"], s["title"], s["category"],
                          s["starting_price_usd"], s["event_url"]))
            conn.commit()
            print(f"[discovery] Upserted {len(shows)} rows into event_discovery")
        finally:
            conn.close()

    return {"num_shows": len(shows)}
