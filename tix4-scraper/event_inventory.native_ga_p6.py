"""
Scraper module: Native GA Provider-6 (2 shows: Brad Garrett, Tape Face).

Page states "All-In Pricing". Categories are .tickets-container elements
with .seat-section (label) and .platinum-price (total).
Adjacent form.form-horizontal has price_level_id hidden input.
Containers and forms alternate in document order.
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
        "ticket_url": buy_url, "scraper_type": "native_ga_p6",
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

    # Collect price_level_ids from forms in document order
    seating_area = (
        soup.select_one(".svg-seating-chart")
        or soup.select_one(".seating-chart-sect")
        or soup.select_one(".event-seating")
        or soup
    )
    forms_and_containers = seating_area.select(
        ".tickets-container, form.form-horizontal"
    )

    # Build paired list: (price_level_id, container)
    price_level_ids = []
    containers = []
    for el in forms_and_containers:
        if el.name == "form":
            plid_input = el.select_one('input[name="price_level_id"]')
            if plid_input:
                price_level_ids.append(plid_input.get("value", ""))
        elif "tickets-container" in (el.get("class") or []):
            containers.append(el)

    rows = []
    for i, tc in enumerate(containers):
        plid = price_level_ids[i] if i < len(price_level_ids) else str(i)

        # Category label — use the <h3> inside .seat-section; ignore trailing
        # "Only N tickets available!" / qty button text that also lives in the div.
        sect_el = tc.select_one(".seat-section h3") or tc.select_one(".seat-section")
        raw_cat = re.sub(r"\s+", " ", sect_el.get_text(" ", strip=True)) if sect_el else ""
        category = re.sub(r"[\s\-\+]+$", "", raw_cat).strip() or f"category_{i}"

        # Extract remaining count from "Only N tickets available!" if present
        remaining_m = re.search(r"Only\s+(\d+)\s+tickets?\s+available", tc.get_text(" ", strip=True), re.I)
        qty_remaining = int(remaining_m.group(1)) if remaining_m else None

        # All-in price from .platinum-price
        plat = tc.select_one(".platinum-price")
        if plat:
            price_text = plat.get_text(strip=True)
            price_m = re.search(r"[\d,.]+", price_text.replace(",", ""))
            total_price = float(price_m.group()) if price_m else None
        else:
            total_price = None

        if total_price is None or total_price <= 0:
            continue

        # Description text for ticket_type (e.g. "General Admission - special rate")
        full_text = re.sub(r"\s+", " ", tc.get_text(" ", strip=True)).strip()
        # Remove price, button text, and leading category
        desc = re.sub(r"\$[\d,.]+.*", "", full_text).strip().rstrip("-").rstrip("+").strip()
        if desc.startswith(category):
            desc = desc[len(category):].strip().lstrip("-").strip()

        rows.append({
            "objectLabelOrUuid": f"GA-P6-{plid}",
            "section_name": category,
            "row": "GA",
            "seat_number": 1,
            "total_price": total_price,
            "category": category,
            "ticket_type": desc if desc != category else None,
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

    print(f"[native_ga_p6] {event_name} | {len(df)} categories | "
          f"sections: {logs['stock_by_section']}")

    return df, logs
