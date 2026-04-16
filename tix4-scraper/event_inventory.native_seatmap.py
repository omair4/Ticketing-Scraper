"""
Scraper module: Native Seatmap (16 shows, providers 6/11).

Seats are inline SVG <circle> elements with data-totalprice attribute.
Available seats: data-disabled="false" + data-totalprice present.
Sold seats: data-disabled="true", no pricing attrs.

Price legend at #price-map validates totals.
"""
import re
from decimal import Decimal
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
    """Classify Consecutive/Even/Odd per (section_name, row) group."""
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
    """Scrape native seatmap buy page. Returns (DataFrame, logs)."""
    event_id = event["eventId"]
    buy_url = event["buy_url"]
    event_name = event.get("event_name", "")
    venue_name = event.get("venue_name", "")
    event_datetime = event.get("event_datetime", "")
    time_frame = event.get("time_frame", 0)

    logs = {
        "eventId": event_id, "event_name": event_name,
        "venue_name": venue_name, "event_datetime": event_datetime,
        "ticket_url": buy_url, "scraper_type": "native_seatmap",
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

    # Build price legend: {fill_color: price}
    legend = {}
    for item in soup.select(".price-legend-item[data-price]"):
        color = (item.get("data-color") or "").lower()
        price = item.get("data-price")
        if color and price:
            legend[color] = float(price)

    # Parse available circles
    rows = []
    for circle in soup.select("svg circle"):
        if circle.get("data-disabled") == "true":
            continue
        totalprice = circle.get("data-totalprice")
        if not totalprice:
            # Try legend fallback via fill color
            style = circle.get("style", "")
            color_m = re.search(r"fill:\s*(#[0-9a-fA-F]+)", style)
            if color_m and color_m.group(1).lower() in legend:
                totalprice = str(legend[color_m.group(1).lower()])
            else:
                continue  # No price available — skip

        seat_id = circle.get("id", "")
        parts = seat_id.split("-")
        section = parts[0] if len(parts) >= 1 else ""
        row_val = parts[1] if len(parts) >= 2 else ""
        seat_num = parts[2] if len(parts) >= 3 else ""

        # Parse seat_number as numeric
        seat_number = None
        num_m = re.search(r"(\d+)", seat_num)
        if num_m:
            seat_number = float(num_m.group(1))

        rows.append({
            "objectLabelOrUuid": seat_id,
            "section_name": section,
            "row": row_val,
            "seat_number": seat_number,
            "total_price": float(totalprice),
            "category": circle.get("data-description", ""),
            "ticket_type": None,
            "status": "available",
            "isRestrictedView": 0,
        })

    df = pd.DataFrame(rows)

    if df.empty:
        logs["total_stock"] = 0
        return df, logs

    # Seat pattern
    df = _determine_seat_pattern(df)

    # Attach event metadata
    df["eventId"] = event_id
    df["event_name"] = event_name
    df["venue_name"] = venue_name
    df["event_datetime"] = event_datetime
    df["time_frame"] = time_frame

    # Sort
    df = df.sort_values(["section_name", "row", "seat_number"]).reset_index(drop=True)

    # Logs
    logs["total_stock"] = len(df)
    logs["stock_by_section"] = (
        df.groupby("section_name").size().sort_values(ascending=False).to_dict()
    )

    print(f"[native_seatmap] {event_name} | {len(df)} seats | "
          f"sections: {logs['stock_by_section']}")

    return df, logs
