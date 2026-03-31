"""
Yapsody seatsio seat extraction module.

Key difference vs PreKindle:
  - pricing comes from pricing_list (pricing_info API) NOT rendering-info categories
  - pricing_list format: [{category: KEY (int), ticketTypes: [{label, price, ...}]}]
  - category KEY in pricing_list == objectCategories category key in rendering-info
  - chart published version: /published/9
  - CDN: cdn-na.seatsio.net (same as PreKindle)
"""
import json
import gzip
import base64
import re
from decimal import Decimal
from typing import Optional, Any, Tuple

import pandas as pd
import requests

SEATSIO_CDN = "https://cdn-na.seatsio.net"
PUBLISHED_VERSION = "9"

SEATSIO_HEADERS = {
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "x-client-tool": "Renderer",
    "accept": "*/*",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "priority": "u=1, i",
}

EXCLUDE_TICKET_TYPES = ["vip", "greet", "package", "me +3", "4-pack"]
# "table" omitted: table-based charts use section label "Table" (e.g. Table-91-1)
EXCLUDE_LABELS = ["suite", "fast pass"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape(event: dict, proxy: Optional[dict] = None) -> Tuple[pd.DataFrame, dict]:
    """
    Main entry point. Fetches seatsio data and returns (DataFrame, logs_dict).

    event dict keys: event_id, workspace_key, event_key, chart_key,
                     pricing_list (JSON string or list),
                     event_name, venue_name, event_datetime, time_frame
    """
    event_id      = event["event_id"]
    workspace_key = event["workspace_key"]
    event_key     = event["event_key"]
    chart_key     = event["chart_key"]
    ticket_url    = event.get("ticket_url", "")
    event_name    = event["event_name"]
    venue_name    = event["venue_name"]
    event_datetime = event["event_datetime"]
    time_frame    = event.get("time_frame", 0)

    pricing_list = event.get("pricing_list")
    if isinstance(pricing_list, str):
        pricing_list = json.loads(pricing_list)

    logs = {
        "event_id":      event_id,
        "event_name":    event_name,
        "venue_name":    venue_name,
        "event_datetime": event_datetime,
        "ticket_url":    ticket_url,
        "time_frame":    time_frame,
        "statuscode":    None,
        "total_stock":   0,
        "stock_by_section": {},
        "error_message": None,
    }

    print(
        f"[seatsio] START event_name={event_name} venue_name={venue_name} "
        f"event_datetime={event_datetime} workspace_key={workspace_key} "
        f"event_key={event_key}"
    )

    all_seats = pd.DataFrame()
    response_status_code = None

    try:
        # -----------------------------------------------------------------------
        # 1. Build category_key_to_price from pricing_list
        #    pricing_list: [{category: KEY, ticketTypes: [{label, price, ...}]}]
        # -----------------------------------------------------------------------
        category_key_to_price, category_key_to_label = _build_pricing_dicts(pricing_list)
        print(f"Pricing map ({len(category_key_to_price)} categories): {category_key_to_price}")

        # -----------------------------------------------------------------------
        # 2. Fetch rendering-info (plain JSON, no encoding)
        # -----------------------------------------------------------------------
        rendering_url = (
            f"{SEATSIO_CDN}/system/public/{workspace_key}"
            f"/rendering-info?event_key={event_key}"
        )
        print(f"Fetching rendering-info: {rendering_url}")
        resp = requests.get(rendering_url, headers=SEATSIO_HEADERS, proxies=proxy, timeout=30)
        response_status_code = resp.status_code
        resp.raise_for_status()
        rendering_data = _decode_response(resp.content, plain_json=True)
        if not rendering_data:
            raise ValueError("Empty rendering-info response")
        rendering_data = json.loads(rendering_data)

        # -----------------------------------------------------------------------
        # 3. Fetch object-statuses (hex-encoded, sparse — missing = available)
        # -----------------------------------------------------------------------
        statuses_url = (
            f"{SEATSIO_CDN}/system/public/{workspace_key}"
            f"/events/object-statuses?event_key={event_key}"
        )
        print(f"Fetching object-statuses: {statuses_url}")
        resp = requests.get(statuses_url, headers=SEATSIO_HEADERS, proxies=proxy, timeout=30)
        response_status_code = resp.status_code
        resp.raise_for_status()
        statuses_raw = _decode_response(resp.content)
        statuses_data = json.loads(statuses_raw) if statuses_raw else []

        # Build availability lookup: label → status
        # Sparse: only seats with explicit status (free/booked/held) are present
        availability_lookup = {}
        for obj in statuses_data:
            key = obj.get("objectLabelOrUuid") or obj.get("uuid")
            if key:
                availability_lookup[key] = {
                    "status":   obj.get("status"),
                    "quantity": obj.get("quantity"),
                }

        print(f"object-statuses: {len(availability_lookup)} explicit entries")

        # -----------------------------------------------------------------------
        # 4. Build category lookup from rendering-info
        #    categories[]: [{key, label, color, accessible}, ...]
        #    objectCategories{}: {seat_label: category_key, ...}
        # -----------------------------------------------------------------------
        category_lookup = {}   # int(key) → label
        for cat in rendering_data.get("categories", []):
            k = _safe_int(cat.get("key"))
            if k is not None:
                category_lookup[k] = cat.get("label", "")

        object_categories = rendering_data.get("objectCategories") or {}
        # object_categories: {"Section A-1-2": category_key, ...}

        # -----------------------------------------------------------------------
        # 5. Build excluded_objects from forSaleConfigsPerEvent
        # -----------------------------------------------------------------------
        for_sale_configs = rendering_data.get("forSaleConfigsPerEvent", {})
        event_config = next(
            (v for v in for_sale_configs.values() if isinstance(v, dict) and "objects" in v),
            None,
        )

        excluded_objects: set = set()
        flag_value = True
        if event_config:
            objects_set = set(event_config.get("objects", []))
            flag_value = event_config.get("forSale", True)
            if not flag_value:
                # objects listed are NOT for sale
                excluded_objects = objects_set
            else:
                # objects listed are FOR sale; everything else excluded
                # (rare case — skip for simplicity, all non-listed = excluded)
                pass

        # -----------------------------------------------------------------------
        # 6. Fetch charts/published for seat layout + GA capacities
        # -----------------------------------------------------------------------
        chart_ver = event.get("chart_version") or PUBLISHED_VERSION
        charts_url = (
            f"{SEATSIO_CDN}/system/public/{workspace_key}"
            f"/charts/{chart_key}/published/{chart_ver}"
        )
        print(f"Fetching charts/published: {charts_url}")
        resp = requests.get(charts_url, headers=SEATSIO_HEADERS, proxies=proxy, timeout=30)
        resp.raise_for_status()
        chart_raw = _decode_response(resp.content)
        chart_data = json.loads(chart_raw) if chart_raw else {}

        # Yapsody: rendering-info often has no objectCategories; build from chart
        if not object_categories and chart_data:
            object_categories = _build_object_categories_from_chart(chart_data)
            if object_categories:
                print(f"Built objectCategories from chart: {len(object_categories)} objects")

        # -----------------------------------------------------------------------
        # 7. Build seat rows from objectCategories
        # -----------------------------------------------------------------------
        rows = []
        for obj_label, cat_key_raw in object_categories.items():
            cat_key = _safe_int(cat_key_raw)

            # Skip if not for sale
            if obj_label in excluded_objects:
                continue

            # Skip objects with excluded label substrings
            if any(ex in obj_label.lower() for ex in EXCLUDE_LABELS):
                continue

            category_label = category_lookup.get(cat_key, "")
            price = category_key_to_price.get(cat_key)
            ticket_type = category_key_to_label.get(cat_key, "")

            avail = availability_lookup.get(obj_label, {})
            status   = avail.get("status")    # None = not in statuses = available
            quantity = avail.get("quantity")   # >1 for GA areas

            # Determine forSale
            for_sale = obj_label not in excluded_objects

            rows.append({
                "objectLabelOrUuid": obj_label,
                "category_key":      cat_key,
                "category":          category_label,
                "price":             price,
                "ticket_type":       ticket_type,
                "availability_status": status,
                "quantity":          quantity,
                "forSale":           for_sale,
            })

        df = pd.DataFrame(rows)
        if df.empty:
            print("No seats found in objectCategories (rendering-info has no objects or all excluded)")
            return pd.DataFrame(), logs

        # -----------------------------------------------------------------------
        # 8. Parse section / row / seat_number from objectLabelOrUuid
        #    Format: "{Section}-{Row}-{Seat}" (hyphenated)
        # -----------------------------------------------------------------------
        # Remove stray "Reserved Seating" suffix tokens
        clean_label = (
            df["objectLabelOrUuid"]
                .str.replace(r"\s*-\s*Reserved Seating", "", regex=True, case=False)
                .str.replace(r"Reserved Seating-", "", regex=True, case=False)
        )

        parts = clean_label.str.split("-", expand=True)
        df["section_name"] = parts[0].str.strip()
        df["row"] = parts[1].str.strip() if parts.shape[1] > 1 else None
        df["seat_number"] = parts[2] if parts.shape[1] > 2 else None

        # GA areas: quantity > 1 or row matches "standing room"
        mask_standing = df["row"].str.contains("standing room", case=False, na=False)
        df.loc[mask_standing, "row"] = "GA"
        df.loc[mask_standing, "seat_number"] = 999

        mask_ga = df["quantity"].fillna(0).astype(float) > 1
        df.loc[mask_ga, "row"] = "GA"
        df.loc[mask_ga, "seat_number"] = 999

        # Extract numeric seat number
        df["seat_number"] = (
            df["seat_number"]
                .astype(str)
                .str.extract(r"(\d+)")[0]
                .astype(float)
        )

        # -----------------------------------------------------------------------
        # 9. isRestrictedView + ADA flags
        # -----------------------------------------------------------------------
        cat_lower    = df["category"].str.lower().fillna("")
        ticket_lower = df["ticket_type"].str.lower().fillna("")

        df["isRestrictedView"] = (
            cat_lower.str.contains("limited view") |
            ticket_lower.str.contains("limited view")
        )
        df["ada"] = (
            cat_lower.str.contains("wheelchair|accessible|ada") |
            ticket_lower.str.contains("wheelchair|accessible|ada")
        )

        # -----------------------------------------------------------------------
        # 10. Availability logic
        #     Seats NOT in object-statuses default to available (sparse set)
        # -----------------------------------------------------------------------
        df["available"] = (
            (
                df["availability_status"].isna() |
                (df["availability_status"].str.lower() == "free")
            )
            & (df["forSale"] == True)
            & (~df["category"].str.upper().eq("KILLS"))
        )

        # -----------------------------------------------------------------------
        # 11. Split reserved vs GA
        # -----------------------------------------------------------------------
        reserved_seats = df[
            df["row"].notna() & (df["row"] != "GA")
        ].reset_index(drop=True)

        ga_areas = df[
            df["row"].isna() | (df["row"] == "GA")
        ].reset_index(drop=True)

        ga_areas = ga_areas[ga_areas["category"] != "Box Office Upgrades"]

        # -----------------------------------------------------------------------
        # 12. Expand GA areas → individual seat rows
        # -----------------------------------------------------------------------
        ga_expanded = pd.DataFrame()

        if len(ga_areas) > 0:
            # Get capacities from chart_data
            ga_capacity_map = _extract_ga_capacity_map(chart_data)
            print(f"ga_capacity_map: {ga_capacity_map}")

            # Also check areaPlaces from rendering-info
            area_places = (
                rendering_data
                    .get("forSaleConfigsPerEvent", {})
                    .get(event_key, {})
                    .get("areaPlaces", {})
            )
            if isinstance(area_places, list):
                merged = {}
                for d in area_places:
                    merged.update(d)
                area_places = merged

            ga_areas = ga_areas.copy()

            if ga_capacity_map:
                ga_areas["ga_capacity"] = ga_areas["objectLabelOrUuid"].map(ga_capacity_map)
            elif area_places:
                ga_areas["ga_capacity"] = ga_areas["objectLabelOrUuid"].map(area_places)
            else:
                ga_areas["ga_capacity"] = None

            ga_areas["qty_remaining"] = (
                ga_areas["ga_capacity"].fillna(0) -
                ga_areas["quantity"].fillna(0)
            ).clip(lower=0)

            ga_remaining = ga_areas[ga_areas["qty_remaining"] > 0].copy()

            if len(ga_remaining) > 0:
                ga_expanded = ga_remaining.loc[
                    ga_remaining.index.repeat(ga_remaining["qty_remaining"].astype(int))
                ].copy()

                ga_expanded["seat_number"] = (
                    ga_expanded
                        .groupby("objectLabelOrUuid")
                        .cumcount() + 1
                )
                ga_expanded["row"] = "GA"
                ga_expanded["available"] = True
                ga_expanded = ga_expanded.reset_index(drop=True)

        # -----------------------------------------------------------------------
        # 13. Combine
        # -----------------------------------------------------------------------
        if len(ga_expanded) > 0:
            all_seats = pd.concat([reserved_seats, ga_expanded], ignore_index=True)
        else:
            all_seats = reserved_seats.copy()

        # -----------------------------------------------------------------------
        # 14. Seat pattern classification
        # -----------------------------------------------------------------------
        all_seats = _determine_seat_pattern(all_seats)

        # -----------------------------------------------------------------------
        # 15. Status column (sold vs available) — match prekindle_scraper: all seats with status
        # -----------------------------------------------------------------------
        all_seats["status"] = all_seats["available"].map(lambda x: "available" if x else "sold")

        # -----------------------------------------------------------------------
        # 16. Final filters: remove ADA, zero-price, null seat_number (keep both sold and available)
        # -----------------------------------------------------------------------
        all_seats = all_seats[all_seats["ada"] == False]
        all_seats = all_seats[
            all_seats["price"].notna() & ((all_seats["price"] > 0) | (all_seats["price"] == -1))
        ]
        all_seats = all_seats[all_seats["seat_number"].notna()]

        if all_seats.empty:
            print("[seatsio] All seats filtered out (ADA/price/seat_number)")

        # Clean up columns
        drop_cols = [
            "ada", "available", "forSale", "availability_status",
            "quantity", "ga_capacity", "qty_remaining",
        ]
        all_seats.drop(columns=[c for c in drop_cols if c in all_seats.columns], inplace=True)
        all_seats.rename(columns={"price": "total_price"}, inplace=True)
        all_seats = all_seats.sort_values(["section_name", "row", "seat_number"])
        all_seats.reset_index(drop=True, inplace=True)

        # Attach event metadata
        all_seats["event_id"]       = event_id
        all_seats["event_name"]     = event_name
        all_seats["venue_name"]     = venue_name
        all_seats["event_datetime"] = event_datetime
        all_seats["time_frame"]     = time_frame

        print(
            f"[seatsio] DONE | rows={len(all_seats)} "
            f"cols={list(all_seats.columns)}"
        )

        section_counts = (
            all_seats
                .groupby("section_name")
                .size()
                .sort_values(ascending=False)
                .to_dict()
        )
        print(f"section_counts: {section_counts}")

        return all_seats, logs

    finally:
        try:
            logs["statuscode"]    = response_status_code
            logs["total_stock"]   = int(len(all_seats)) if not all_seats.empty else 0
            logs["stock_by_section"] = (
                all_seats
                    .groupby("section_name")
                    .size()
                    .sort_values(ascending=False)
                    .to_dict()
                if not all_seats.empty else {}
            )
        except Exception:
            logs["statuscode"]    = 500
            logs["total_stock"]   = 0
            logs["stock_by_section"] = {}


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def _build_pricing_dicts(pricing_list: list) -> Tuple[dict, dict]:
    """
    Build two lookup dicts from pricing_list.

    pricing_list format:
      [{category: KEY (int), ticketTypes: [{ticketType, label, price}]}]

    Returns:
      category_key_to_price: {int(KEY): lowest_non_excluded_price}
      category_key_to_label: {int(KEY): ticket_type_label}
    """
    if not pricing_list:
        return {}, {}

    exclude_pattern = "|".join(map(re.escape, EXCLUDE_TICKET_TYPES))

    key_to_price = {}
    key_to_label = {}

    for item in pricing_list:
        cat_key = _safe_int(item.get("category"))
        if cat_key is None:
            continue

        ticket_types = item.get("ticketTypes", [])
        if not ticket_types:
            continue

        # Filter out VIP/package ticket types; fall back to all if none remain
        non_excluded = [
            tt for tt in ticket_types
            if not re.search(exclude_pattern, str(tt.get("label", "")), re.IGNORECASE)
        ]
        candidates = non_excluded if non_excluded else ticket_types

        # Pick the lowest-price ticket type
        best = min(candidates, key=lambda tt: float(tt.get("price", 0) or 0))
        price = float(best.get("price", 0) or 0)
        label = best.get("label", "")

        key_to_price[cat_key] = price
        key_to_label[cat_key] = label

    return key_to_price, key_to_label


# ---------------------------------------------------------------------------
# Chart / GA capacity helpers
# ---------------------------------------------------------------------------

def _extract_ga_capacity_map(chart_data: dict) -> dict:
    """Extract GA area capacities from decoded chart_data."""
    ga_map = {}

    # Pattern 1: subChart.generalAdmissionAreas[]
    for area in chart_data.get("subChart", {}).get("generalAdmissionAreas", []):
        if area.get("capacity", 0) > 1:
            ga_map[area["label"]] = area["capacity"]

    # Pattern 2: subChartFloors[].chart.generalAdmissionAreas[]
    for floor in chart_data.get("subChartFloors", []):
        for area in floor.get("chart", {}).get("generalAdmissionAreas", []):
            if area.get("capacity", 0) > 1:
                ga_map[area["label"]] = area["capacity"]

    # Pattern 3: subChartFloors[].generalAdmissionAreas[]
    for floor in chart_data.get("subChartFloors", []):
        for area in floor.get("generalAdmissionAreas", []):
            if area.get("capacity", 0) > 1:
                ga_map[area["label"]] = area["capacity"]

    return ga_map


def _build_object_categories_from_chart(chart_data: dict) -> dict:
    """
    Build objectCategories { objectLabel: categoryKey } from chart when
    rendering-info does not include objectCategories (e.g. Yapsody).
    Label format must match object-statuses: "Section E-L-113" (section-row-seat)
    or "Table-1-1" for table-based charts (section-table-seat).
    Supports: sections[].subChart.rows[].seats[] and sections[].subChart.tables[].seats[].
    """
    out = {}
    sub = chart_data.get("subChart") or {}
    for sec in sub.get("sections") or []:
        sec_label = sec.get("label") or ""
        sec_cat = sec.get("categoryKey")
        sec_sub = sec.get("subChart") or {}
        # Path 1: rows + seats (standard reserved seating)
        for row in sec_sub.get("rows") or []:
            row_label = str(row.get("label") or "")
            for seat in row.get("seats") or []:
                seat_label = str(seat.get("label") or "")
                cat = seat.get("categoryKey") if seat.get("categoryKey") is not None else sec_cat
                if sec_label and seat_label and cat is not None:
                    obj_label = f"{sec_label}-{row_label}-{seat_label}"
                    out[obj_label] = cat
        # Path 2: tables + seats (e.g. event 862207 Bingo Loco)
        for tbl in sec_sub.get("tables") or []:
            tbl_label = str(tbl.get("label") or "")
            for seat in tbl.get("seats") or []:
                seat_label = str(seat.get("label") or "")
                cat = seat.get("categoryKey") if seat.get("categoryKey") is not None else sec_cat
                if sec_label and seat_label and cat is not None:
                    obj_label = f"{sec_label}-{tbl_label}-{seat_label}"
                    out[obj_label] = cat
        # Path 3: booths (each booth can have seats or be a single object)
        for booth in sec_sub.get("booths") or []:
            booth_label = str(booth.get("label") or "")
            booth_seats = booth.get("seats") or []
            if booth_seats:
                for seat in booth_seats:
                    seat_label = str(seat.get("label") or "")
                    cat = seat.get("categoryKey") if seat.get("categoryKey") is not None else sec_cat
                    if sec_label and seat_label and cat is not None:
                        obj_label = f"{sec_label}-{booth_label}-{seat_label}"
                        out[obj_label] = cat
            elif sec_label and booth_label and (sec_cat is not None or booth.get("categoryKey") is not None):
                cat = booth.get("categoryKey") if booth.get("categoryKey") is not None else sec_cat
                out[f"{sec_label}-{booth_label}-1"] = cat
    return out


# ---------------------------------------------------------------------------
# Seat pattern classification
# ---------------------------------------------------------------------------

def _determine_seat_pattern(df: pd.DataFrame) -> pd.DataFrame:
    def classify_group(g: pd.DataFrame) -> str:
        if len(g) <= 1:
            return "Consecutive"
        seat_nums = sorted(g["seat_number"].dropna().astype(int))
        diffs = {b - a for a, b in zip(seat_nums, seat_nums[1:])}
        if diffs == {1}:
            return "Consecutive"
        if diffs == {2}:
            return "Even" if seat_nums[0] % 2 == 0 else "Odd"
        return "Consecutive"

    return (
        df
            .groupby(["section_name", "row"], group_keys=False)
            .apply(lambda g: g.assign(seat_pattern=classify_group(g)))
            .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Decode helpers (mirrors prekindle decoder logic)
# ---------------------------------------------------------------------------

def _decode_response(body: bytes, plain_json: bool = False) -> Optional[str]:
    """
    Decode a seatsio CDN response body.

    plain_json=True: rendering-info is plain JSON (just decompress gzip if needed)
    plain_json=False: try hex offset bruteforce (object-statuses, charts/published)
    """
    if not body:
        return None

    # Ensure bytes
    if isinstance(body, str):
        body = body.encode("latin-1")

    # 1. GZIP decompress
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)

    if plain_json:
        # rendering-info: just UTF-8 JSON
        return body.decode("utf-8")

    # 2. Direct JSON
    try:
        text = body.decode("utf-8")
        json.loads(text)
        return text
    except Exception:
        pass

    # 3. Bruteforce byte offset
    result = _bruteforce_offset_decode(body)
    if result:
        return result[0]

    # 4. Base64 + offset
    try:
        binary = base64.b64decode(body)
        result = _bruteforce_offset_decode(binary)
        if result:
            return result[0]
    except Exception:
        pass

    # 5. Hex string + offset
    try:
        text = body.decode("latin-1").strip()
        if text and all(c in "0123456789abcdefABCDEF" for c in text):
            enc_bytes = bytes.fromhex(text)
            result = _bruteforce_offset_decode(enc_bytes)
            if result:
                return result[0]
    except Exception:
        pass

    return None


def _bruteforce_offset_decode(raw_bytes: bytes) -> Optional[Tuple[str, int]]:
    """
    Try all 256 byte offsets to decode raw bytes into valid JSON.
    Smart prediction: try offsets that make first byte → '{' or '[' first.
    """
    if not raw_bytes:
        return None

    first_byte = raw_bytes[0]
    priority_offsets = [
        (first_byte - 0x7B) % 256,  # → '{'
        (first_byte - 0x5B) % 256,  # → '['
    ]

    seen = set()
    candidates = []
    for o in priority_offsets:
        if o not in seen:
            seen.add(o)
            candidates.append(o)
    for o in range(256):
        if o not in seen:
            candidates.append(o)

    for offset in candidates:
        try:
            decoded_bytes = bytes((b - offset) % 256 for b in raw_bytes)
            decoded_str = decoded_bytes.decode("utf-8")
            json.loads(decoded_str)
            return (decoded_str, offset)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue

    return None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _safe_int(val) -> Optional[int]:
    """Convert value to int, return None on failure."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
