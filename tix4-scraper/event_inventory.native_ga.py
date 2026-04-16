"""
Scraper module: Native GA (33 shows, provider empty).

Ticket categories are <input name="ticket_id"> radios.
total_price = data-fullprice (= data-price + fee). NOT data-price alone.
Category label from nearest .ticket-type-title.
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
        "ticket_url": buy_url, "scraper_type": "native_ga",
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
    for radio in soup.select('input[name="ticket_id"]'):
        ticket_id = radio.get("value", "")
        fullprice = radio.get("data-fullprice")
        if not fullprice:
            continue

        # Find category label from enclosing holder
        holder = radio.find_parent(class_="ticket-type-holder")
        title_el = holder.select_one(".ticket-type-title") if holder else None
        if title_el is None:
            # Fallback: search upward for any preceding label
            title_el = radio.find_previous(class_="ticket-type-title")
        category = title_el.get_text(strip=True) if title_el else f"ticket_{ticket_id}"

        rows.append({
            "objectLabelOrUuid": f"GA-{category}-{ticket_id}",
            "section_name": category,
            "row": "GA",
            "seat_number": 1,
            "total_price": float(fullprice),
            "category": category,
            "ticket_type": None,
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

    print(f"[native_ga] {event_name} | {len(df)} categories | "
          f"sections: {logs['stock_by_section']}")

    return df, logs
