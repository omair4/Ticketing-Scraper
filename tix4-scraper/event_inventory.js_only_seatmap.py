"""
Scraper module: JS-Only Seatmap (1 show: Atomic Saloon, provider 17).

SVG circles exist but have ZERO pricing in raw HTML.
Only section-level "Starting at $X" text in .tickets-container.
Emits section-level rows (no per-seat data).
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
        "ticket_url": buy_url, "scraper_type": "js_only_seatmap",
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
    for tc in soup.select(".tickets-container"):
        # Section name from .seat-section
        sect_el = tc.select_one(".seat-section")
        section = sect_el.get_text(strip=True) if sect_el else ""
        if not section:
            continue

        # Price from .platinum-price or "Starting at $X" text
        plat = tc.select_one(".platinum-price")
        if plat:
            price_text = plat.get_text(strip=True)
        else:
            price_text = tc.get_text(" ", strip=True)

        price_m = re.search(r"\$\s*([\d,.]+)", price_text.replace(",", ""))
        total_price = float(price_m.group(1)) if price_m else None

        if total_price is None or total_price <= 0:
            continue

        rows.append({
            "objectLabelOrUuid": f"SECTION-{section}",
            "section_name": section,
            "row": None,
            "seat_number": None,
            "total_price": total_price,
            "category": section,
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

    print(f"[js_only_seatmap] {event_name} | {len(df)} sections | "
          f"sections: {logs['stock_by_section']}")

    return df, logs
