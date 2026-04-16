"""
Scraper module: External GA (7 shows, provider 12).

Ticket categories are <div class="tickets-container" data-ticket-type data-name data-price>.
total_price = span.platinum-price text (all-in). NOT data-price (which is net).
"""
import re
from typing import Optional

import httpx
import pandas as pd
from bs4 import BeautifulSoup

BASE_URL = "https://tix4.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def scrape(event: dict, proxy: Optional[str] = None) -> tuple[pd.DataFrame, dict]:
    event_id = event["eventId"]
    buy_url = event["buy_url"]
    event_name = event.get("event_name", "")
    venue_name = event.get("venue_name", "")
    event_datetime = event.get("event_datetime", "")
    time_frame = event.get("time_frame", 0)

    logs = {
        "eventId": event_id, "event_name": event_name,
        "venue_name": venue_name, "event_datetime": event_datetime,
        "ticket_url": buy_url, "scraper_type": "external_ga",
        "time_frame": time_frame, "statuscode": None,
        "total_stock": 0, "stock_by_section": {}, "error_message": None,
    }

    full_url = f"{BASE_URL}{buy_url}" if buy_url.startswith("/") else buy_url

    try:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": UA}, proxy=proxy) as client:
            resp = client.get(full_url)
            logs["statuscode"] = resp.status_code
            resp.raise_for_status()
    except Exception as e:
        logs["error_message"] = repr(e)
        return pd.DataFrame(), logs

    soup = BeautifulSoup(resp.text, "lxml")

    rows = []
    for tc in soup.select("[data-ticket-type][data-name][data-price]"):
        data_id = tc.get("data-id", "")
        name = tc.get("data-name", "")
        section = tc.get("data-section", name)
        ticket_type = tc.get("data-ticket-type", "")

        # All-in total from span.platinum-price inside this container
        plat = tc.select_one(".platinum-price")
        if plat:
            price_text = plat.get_text(strip=True)
            price_m = re.search(r"[\d,.]+", price_text.replace(",", ""))
            total_price = float(price_m.group()) if price_m else None
        else:
            # Fallback to data-price (net) — not ideal but better than nothing
            total_price = float(tc.get("data-price", 0))

        if total_price is None or total_price <= 0:
            continue

        rows.append({
            "objectLabelOrUuid": f"GA-{name}-{data_id}",
            "section_name": section,
            "row": "GA",
            "seat_number": 1,
            "total_price": total_price,
            "category": name,
            "ticket_type": ticket_type,
            "status": "available",
            "seat_pattern": "Consecutive",
            "isRestrictedView": 0,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        logs["total_stock"] = 0
        return df, logs

    df["eventId"] = event_id
    df["event_name"] = event_name
    df["venue_name"] = venue_name
    df["event_datetime"] = event_datetime
    df["time_frame"] = time_frame

    logs["total_stock"] = len(df)
    logs["stock_by_section"] = (
        df.groupby("section_name").size().sort_values(ascending=False).to_dict()
    )

    print(f"[external_ga] {event_name} | {len(df)} categories | "
          f"sections: {logs['stock_by_section']}")

    return df, logs
