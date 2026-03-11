# scrapers/seatsio.py

import json
import gzip
import time
from decimal import Decimal
from typing import Dict, Optional, Tuple
import re

import pandas as pd
from bs4 import BeautifulSoup


import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from ._seatsio_browser import driversetup


# --------------------------------------------------
# Public API
# --------------------------------------------------

def scrape(event: dict, proxy: Optional[Dict] = None) -> pd.DataFrame:
    ticket_url = event["ticket_url"]

    eventId = event["eventId"]
    find_tickets_id = event["find_tickets_id"]
    workspace_key = event["workspace_key"]
    event_key = event["event_key"]
    pricing_list = event.get("pricing_list")

    if pricing_list:
        pricing_list = json.loads(pricing_list)
    else:
        pricing_list = None
    event_name = event["event_name"]
    venue_name = event["venue_name"]
    event_datetime = event["event_datetime"]
    time_frame = event["time_frame"]

    logs = {
        "eventId": eventId,
        "find_tickets_id":find_tickets_id,
        "event_name": event_name,
        "venue_name": venue_name,
        "event_datetime": event_datetime,
        "ticket_url":ticket_url,
        "time_frame": time_frame,
        "statuscode": None,          # seatsio is browser-based; keep for parity
        "stock_by_section": {},
        "total_stock": 0,
        "error_message": None,
    }

    find_tickets_url = f'https://www.prekindle.com/find-tickets/id/{find_tickets_id}'
    print(
        f"[ga via browser] START "
        f"event_name={event_name} "
        f"venue_name={venue_name} "
        f"event_datetime={event_datetime} "
        f"find_tickets_url={find_tickets_url}"
    )

    driver = driversetup()

    try:
        print("=== DRIVER SETUP ===")

        print(f"\nNavigating to event:\n{find_tickets_url}")
        driver.get(find_tickets_url)

        print("Sleeping 5s for initial JS load...")
        time.sleep(5)

        print("Current URL:", driver.current_url)

        ticket_page_html = driver.page_source
        print("Captured Promo HTML")
        soup = BeautifulSoup(ticket_page_html, "html.parser")

        # --------------------------------------------------
        # SOLD OUT / WAITLIST CHECK
        # --------------------------------------------------
        print("\n=== CHECKING SOLD OUT / WAITLIST STATE ===")

        sold_out = False

        elements = driver.find_elements(By.XPATH, "//*[contains(text(),'Sold Out') or contains(text(),'Waitlist')]")

        for el in elements:
            txt = el.text.lower().strip()
            if "sold out" in txt or "waitlist" in txt:
                print("🚨 SOLD OUT / WAITLIST DETECTED")
                print("Element:", el.tag_name)
                print("Text:", txt)
                sold_out = True
                break

        if sold_out:
            scenario = "sold_out"
            print("\n=== FINAL RESULT ===")
            print("Scenario:", scenario)
            all_seats = pd.DataFrame()

        else:

            rows = []

            for row in soup.select("tr.ticketoption"):

                # Section + ticket name
                section = row.select_one(".sectionname").get_text(strip=True)
                ticket_type = row.select_one(".pricename").get_text(strip=True)

                # Price (total price span)
                price_text = row.select_one("span[wicketpath*='totalAmount']").get_text(strip=True)
                price = float(re.search(r"\$([\d.]+)", price_text).group(1))

                # Quantity (max dropdown value)
                select = row.select_one("select")
                max_qty = max(
                    int(o["value"])
                    for o in select.select("option")
                    if o["value"].isdigit()
                )

                # ADA detection
                ada = "ada" in ticket_type.lower() or "accessible" in ticket_type.lower()

                # Restricted view detection
                is_restricted = any(
                    k in ticket_type.lower()
                    for k in ["restricted", "limited", "obstructed"]
                )

                # Expand to seat rows
                for seat in range(1, max_qty + 1):
                    rows.append({
                        "eventId":eventId,
                        "event_name":event_name,
                        "venue_name":venue_name,
                        "event_datetime":event_datetime,
                        "objectLabelOrUuid": f"{section}-GA-{seat}",
                        "section_name": section,
                        "row": "GA",
                        "seat_number": int(seat),
                        "category_key": None,
                        "category": ticket_type,
                        "total_price": price,
                        "isRestrictedView": is_restricted,
                        "ada": ada,
                        "ticket_type": ticket_type,
                        "seat_pattern": "Consecutive",
                        "time_frame":time_frame
                    })


            all_seats = pd.DataFrame(rows)

            if not all_seats.empty:

                all_seats = all_seats[
                    (all_seats["ada"] == False)
                ]

                all_seats = all_seats[all_seats["total_price"] > 0]

                all_seats.drop(columns=["ada"], inplace=True)

                all_seats.reset_index(drop=True, inplace=True)

                all_seats = all_seats.sort_values(
                    by=["section_name", "row", "seat_number"]
                )


            print(
                f"[ga browser] post-filter seats = {len(all_seats)} "
                f"(removed ADA/unavailable)"
            )

            print(
                f"[seatsio] DONE | "
                f"rows={len(all_seats)} "
                f"cols={list(all_seats.columns)}"
            )
        return all_seats, logs

    finally:
        print("\n=== DRIVER CLEANUP ===")
        driver.quit()

        try:
            logs['statuscode'] = 200
            logs["total_stock"] = int(len(all_seats))

            logs["stock_by_section"] = (
                all_seats
                .groupby("section_name")
                .size()
                .sort_values(ascending=False)
                .to_dict()
            )
        except Exception:
            logs['statuscode'] = 500
            logs["total_stock"] = 0
            logs["stock_by_section"] = {}


