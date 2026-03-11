from __future__ import annotations

"""
Local test runner to compare Tixr scraper output against real /api/events/{id} payloads.

This does NOT hit Tixr or seats.io – it works purely from the saved raw_api_*.json
files we captured via Playwright MCP and stored under tixr_scraper/output/.

For now this focuses on GA / open-seating and sold-out scenarios, where all the
information we need lives in /api/events/{id}. Seats.io tests will be wired in
once workspace_key extraction is fully implemented.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

from lambda_functions.scrapers import open_seating

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


@dataclass
class TestEvent:
    event_id: int
    name: str
    scenario_type: str  # "open_seating" | "sold_out" | "seatsio"
    raw_filename: str
    out_filename: str


# Hand-curated mapping between saved raw_api files and scenarios.
TEST_EVENTS: List[TestEvent] = [
    TestEvent(
        event_id=173397,
        name="Houston Dynamo 2 vs Timbers2",
        scenario_type="open_seating",
        raw_filename="raw_api_173397_dynamo2_simple_ga_free.json",
        out_filename="scraped_173397_dynamo2_simple_ga_free.json",
    ),
    TestEvent(
        event_id=168656,
        name="FISHER",
        scenario_type="sold_out",
        raw_filename="raw_api_168656_fisher_soldout.json",
        out_filename="scraped_168656_fisher_soldout.json",
    ),
    TestEvent(
        event_id=175000,
        name="Wisp",
        scenario_type="sold_out",
        raw_filename="raw_api_175000_wisp_soldout.json",
        out_filename="scraped_175000_wisp_soldout.json",
    ),
]


def _load_raw(path: str) -> Dict[str, Any]:
    """
    Load raw JSON from a file.

    Some of the captured raw_api_*.json files are full Playwright tool dumps that
    include a leading `### Result` header and other text before the actual JSON.
    We normalise those by stripping everything before the first '{' and after the
    last '}' so that json.loads() sees a clean payload.
    """

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    stripped = text.lstrip()
    if stripped.startswith("### Result"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"Could not locate JSON object in tool dump: {path}")
        payload = text[start : end + 1]
        return json.loads(payload)

    return json.loads(text)


def _format_event_block(raw: Dict[str, Any]) -> Dict[str, Any]:
    venue = raw.get("venue") or {}
    address = venue.get("address") or {}
    return {
        "event_id": str(raw.get("id")),
        "event_name": raw.get("name"),
        "venue_name": venue.get("name"),
        "venue_address": ", ".join(
            [
                addr
                for addr in [
                    address.get("streetAddress"),
                    address.get("city"),
                    address.get("state"),
                    address.get("postalCode"),
                ]
                if addr
            ]
        )
        or None,
        "event_date": raw.get("formattedStartDate"),
        "event_url": raw.get("url"),
    }


def _format_ga_as_seats(categories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert open_seating ticket_categories into a seat-like list so the
    output shape roughly matches the AdmitONE seats inventory JSON.
    """
    seats: List[Dict[str, Any]] = []
    for cat in categories:
        price = cat.get("price") or 0.0
        fee = cat.get("service_fee") or 0.0
        total = (price or 0.0) + (fee or 0.0)
        remaining = cat.get("current_stock")
        is_out = bool(cat.get("is_out_of_stock"))
        status = "sold_out" if is_out else "available"
        seats.append(
            {
                "section": None,
                "row": None,
                "seat": None,
                "price": f"{price:.2f}",
                "service_fee": f"{fee:.2f}",
                "status": status,
                "price_level": cat.get("category_name"),
                "price_level_id": str(cat.get("category_id")) if cat.get("category_id") else None,
                "restricted_view": False,
                "total_price": f"{total:.2f}",
                "remaining_quantity": remaining,
            }
        )
    return seats


def _build_envelope(raw: Dict[str, Any], scenario_type: str) -> Dict[str, Any]:
    """Build AdmitONE-style JSON envelope from raw Tixr /api/events payload."""

    scraped_at = datetime.now(timezone.utc).isoformat()
    event_block = _format_event_block(raw)

    if scenario_type == "open_seating":
        categories = open_seating.scrape_open_seating(raw)
        seats = _format_ga_as_seats(categories)
        total = float(len(seats))
        available = float(sum(1 for s in seats if s.get("status") == "available"))
    elif scenario_type == "sold_out":
        seats = []
        total = 0.0
        available = 0.0
    else:
        seats = []
        total = 0.0
        available = 0.0

    return {
        "event": event_block,
        "scraped_at": scraped_at,
        "scenario_type": scenario_type,
        "total_seats_found": total,
        "available_seats_count": available,
        "seats": seats,
    }


def run() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for ev in TEST_EVENTS:
        raw_path = os.path.join(OUTPUT_DIR, ev.raw_filename)
        if not os.path.exists(raw_path):
            print(f"[WARN] Missing raw file for {ev.event_id}: {raw_path}")
            continue
        raw = _load_raw(raw_path)
        envelope = _build_envelope(raw, ev.scenario_type)
        out_path = os.path.join(OUTPUT_DIR, ev.out_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, ensure_ascii=False)
        print(f"[OK] Wrote {out_path}")


if __name__ == "__main__":
    run()

