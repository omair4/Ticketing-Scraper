"""
Scraper module: External Seatmap (59 shows, provider 12).

Seats are in <div class="seat-item"> with data-total-price, data-section, etc.
SVG circles carry geometry only; seat-item divs are the source of truth.
Price legend at #price-map validates totals.
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


def _determine_seat_pattern(df: pd.DataFrame) -> pd.DataFrame:
    def classify_group(g: pd.DataFrame) -> str:
        if len(g) <= 1:
            return "Consecutive"
        seat_nums = sorted(g["seat_number"].dropna().astype(int))
        if len(seat_nums) <= 1:
            return "Consecutive"
        diffs = {b - a for a, b in zip(seat_nums, seat_nums[1:])}
        if diffs == {1}:
            return "Consecutive"
        if diffs == {2}:
            return "Even" if seat_nums[0] % 2 == 0 else "Odd"
        return "Consecutive"

    if df.empty:
        return df
    return (
        df.groupby(["section_name", "row"], group_keys=False)
          .apply(lambda g: g.assign(seat_pattern=classify_group(g)))
          .reset_index(drop=True)
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
        "ticket_url": buy_url, "scraper_type": "external_seatmap",
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
    for si in soup.select("div.seat-item[data-total-price]"):
        seat_key = si.get("data-seat-key", "")
        section = si.get("data-section", "")
        row_val = si.get("data-row", "")
        number = si.get("data-number", "")
        total_price = si.get("data-total-price", "0")
        ticket_type = si.get("data-ticket-type", "")

        seat_number = None
        num_m = re.search(r"(\d+)", number)
        if num_m:
            seat_number = float(num_m.group(1))

        rows.append({
            "objectLabelOrUuid": seat_key,
            "section_name": section,
            "row": row_val,
            "seat_number": seat_number,
            "total_price": float(total_price),
            "category": None,
            "ticket_type": ticket_type,
            "status": "available",
            "isRestrictedView": 0,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        logs["total_stock"] = 0
        return df, logs

    df = _determine_seat_pattern(df)

    df["eventId"] = event_id
    df["event_name"] = event_name
    df["venue_name"] = venue_name
    df["event_datetime"] = event_datetime
    df["time_frame"] = time_frame

    df = df.sort_values(["section_name", "row", "seat_number"]).reset_index(drop=True)

    logs["total_stock"] = len(df)
    logs["stock_by_section"] = (
        df.groupby("section_name").size().sort_values(ascending=False).to_dict()
    )

    print(f"[external_seatmap] {event_name} | {len(df)} seats | "
          f"sections: {logs['stock_by_section']}")

    return df, logs
