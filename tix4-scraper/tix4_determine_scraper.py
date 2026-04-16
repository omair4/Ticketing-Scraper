"""
Lambda 2: Classify scraper_type for a single show.

Input:  {"show_slug": str}
Output: {"show_slug": str, "scraper_type": str, "external_api_provider_id": str}
"""
import sys
sys.path.append('/mnt/access')

import json
import re
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


class UnknownScenarioError(Exception):
    """Raised when no classifier rule matches the buy page DOM."""


def _first_buy_url(event_page_html: str) -> str | None:
    """Extract the first buy_url from the event-calendar-model JSON."""
    soup = BeautifulSoup(event_page_html, "lxml")
    cal = soup.find("script", {"id": "event-calendar-model", "type": "application/json"})
    if cal is None:
        return None
    data = json.loads(cal.string or "[]")
    for day in data:
        for t in day.get("times", []):
            if t.get("buy_url"):
                return t["buy_url"]
    return None


def classify(soup: BeautifulSoup) -> str:
    """DOM-shape classifier — returns scraper_type string."""
    has_svg = soup.select_one('input[name="svg_seats_exists"][value="true"]') is not None

    if has_svg and soup.select("svg circle[data-totalprice]"):
        return "native_seatmap"

    if has_svg and soup.select("div.seat-item[data-total-price]"):
        return "external_seatmap"

    if soup.select_one('input[name="ticket_id"]'):
        return "native_ga"

    if soup.select("[data-ticket-type][data-name][data-price]"):
        return "external_ga"

    if soup.select('input[name="is_ga"][value="1"]'):
        return "native_ga_p6"

    if has_svg:
        return "js_only_seatmap"

    # Buy page rendered (has external_event_id hidden input) but carries no
    # inventory elements. Sold-out / add-on only / temporarily unavailable.
    # Return a sentinel instead of hard-failing.
    if soup.select_one('input[name="external_event_id"]') is not None:
        return "no_inventory"

    raise UnknownScenarioError(
        "No classifier rule matched. "
        f"has_svg={has_svg}, "
        f"circles={len(soup.select('svg circle'))}, "
        f"seat_items={len(soup.select('div.seat-item'))}, "
        f"ticket_radios={len(soup.select('input[name=ticket_id]'))}, "
        f"ticket_type_divs={len(soup.select('[data-ticket-type]'))}, "
        f"is_ga={len(soup.select('input[name=is_ga]'))}"
    )


def lambda_handler(event, context):
    show_slug = event["show_slug"]

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxy = None
    if proxy_host:
        proxy = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"

    with httpx.Client(
        timeout=30, follow_redirects=True,
        headers={"User-Agent": UA},
        proxy=proxy,
    ) as client:
        # Fetch event page to get first buy_url
        resp = client.get(f"{BASE_URL}/event/{show_slug}")
        resp.raise_for_status()
        buy_url = _first_buy_url(resp.text)

        if not buy_url:
            print(f"[determine] {show_slug}: no buy_url (no upcoming showtimes)")
            return {"show_slug": show_slug, "scraper_type": None, "reason": "no_showtimes"}

        # Fetch buy page and classify
        full_url = f"{BASE_URL}{buy_url}" if buy_url.startswith("/") else buy_url
        resp2 = client.get(full_url)
        resp2.raise_for_status()

    soup = BeautifulSoup(resp2.text, "lxml")
    scraper_type = classify(soup)

    provider_el = soup.select_one('input[name="external_api_provider_id"]')
    provider_id = provider_el.get("value", "") if provider_el else ""

    print(f"[determine] {show_slug}: scraper_type={scraper_type}, provider_id={provider_id!r}")

    # DB update
    host, user, password = get_db_info()
    if host:
        conn = pymysql.connect(host=host, user=user, password=password,
                               database=DATABASE, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE event_discovery
                    SET scraper_type = %s,
                        external_api_provider_id = %s
                    WHERE show_slug = %s
                """, (scraper_type, provider_id, show_slug))
            conn.commit()
        finally:
            conn.close()

    return {
        "show_slug": show_slug,
        "scraper_type": scraper_type,
        "external_api_provider_id": provider_id,
    }
