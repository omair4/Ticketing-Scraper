# scrapers/seatsio.py
from creds import get_proxy_info
import json
import gzip
import time
from decimal import Decimal
from typing import Dict, Optional, Tuple, Any
import base64
import pandas as pd
import re

import requests


# --------------------------------------------------
# Public API
# --------------------------------------------------

def scrape(event: dict, proxy: Optional[Dict] = None) -> pd.DataFrame:
    ticket_url = event["ticket_url"]

    eventId = event["eventId"]
    find_tickets_id = event["find_tickets_id"]
    workspace_key = event["workspace_key"]
    event_key = event["event_key"]
    chart_key = event["chart_key"]
    published_key = event["published_key"]
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

    print(
        f"[seatsio API] START "
        f"event_name={event_name} "
        f"venue_name={venue_name} "
        f"event_datetime={event_datetime} "
        f"url={ticket_url}"
    )

    try:
        proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
        proxy = {
            'http': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}',
            'https': f'http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}'
        }

        # -------------- #
        # PRICING
        # -------------- #
        rows = []

        for pricing_index, item in enumerate(pricing_list):
            category = item.get("category", "")

            # extract price level number (int if exists)
            match = re.search(r"price level\s*(\d+)", category, re.IGNORECASE)
            category_key = int(match.group(1)) if match else None

            for tt in item.get("ticketTypes", []):
                rows.append({
                    "pricing_index": pricing_index,   # ← original order
                    "category": category,
                    "category_key": category_key,
                    "ticketType_id": str(tt.get("ticketType", "")),
                    "price": float(tt.get("price", 0)),
                    "ticket_type": tt.get("label", "")
                })

        pricing_df = pd.DataFrame(rows)

        pricing_df["ticket_type_lower"] = pricing_df["ticket_type"].str.lower()
        exclude_phrases = ["vip", "greet", "package", "me +3",'4-pack']

        pattern = "|".join(map(re.escape, exclude_phrases))

        def choose_best(group):
            mask = ~group["ticket_type_lower"].str.contains(pattern, na=False)
            filtered = group[mask]

            if not filtered.empty:
                return filtered.loc[filtered["price"].idxmin()]
            else:
                return group.loc[group["price"].idxmin()]

        final_pricing_df = (
            pricing_df
            .groupby("category", group_keys=False)
            .apply(choose_best)
            .sort_values("pricing_index")  # ← keeps original pricing_list order
            .reset_index(drop=True)
            .drop(columns="ticket_type_lower")
        )

        # -------------- #
        # SEATSIO API
        # -------------- #

        seatsio_request_url = f'https://cdn-na.seatsio.net/system/public/{workspace_key}/events/object-statuses?event_key={event_key}'

        headers = {
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
            "x-client-tool": "Renderer",
            "accept": "*/*",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "sec-fetch-storage-access": "none",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "priority": "u=1, i",
        }

        response = requests.get(seatsio_request_url,headers=headers,proxies=proxy)
        print(seatsio_request_url)
        response_status_code = response.status_code
        response.raise_for_status()

        body = response.content


        # ensure bytes
        if isinstance(body, str):
            body = body.encode("latin-1")

        # -----------------------------
        # 1️⃣ GZIP
        # -----------------------------
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)

        decoded = None

        # -----------------------------
        # 2️⃣ Try direct JSON
        # -----------------------------
        try:
            decoded = body.decode("utf-8")
            json.loads(decoded)
        except:
            decoded = None

        # -----------------------------
        # 3️⃣ Try offset brute force
        # -----------------------------
        if not decoded:
            result = _bruteforce_offset_decode(body)
            if result:
                decoded = result[0]

        # -----------------------------
        # 4️⃣ Try base64 + offset
        # -----------------------------
        if not decoded:
            try:
                binary_data = base64.b64decode(body)
                result = _bruteforce_offset_decode(binary_data)
                if result:
                    decoded = result[0]
            except:
                pass

        if not decoded:
            raise ValueError("Failed to fully decode seats.io response")

        seatsio_response_data = json.loads(decoded)

        availability_list = []

        for obj in seatsio_response_data:

            objectLabelOrUuid = obj.get("objectLabelOrUuid")

            availability_status = obj.get("status")
            quantity = obj.get("quantity")
            version = obj.get("version")

            availability_list.append({
                "objectLabelOrUuid": objectLabelOrUuid,
                "availability_status": availability_status,
                "quantity": quantity,
                "version": version,
            })


        availability_lookup = {}

        for obj in seatsio_response_data:

            key = obj.get("uuid") or obj.get("objectLabelOrUuid")
            if not key:
                continue

            availability_lookup[key] = {
                "availability_status": obj.get("status"),
                "quantity": obj.get("quantity"),
                "version": obj.get("version"),
            }

        availability_df = pd.DataFrame(availability_list)
        total_seats = len(availability_df)
        available_seats = len(availability_df[availability_df.availability_status == "free"])

        availability_df["seat_type"] = availability_df["quantity"].apply(
            lambda x: "GA" if x > 1 else "RSVD"
        )
        print(f'AVAILABILITY API: Total seats: {total_seats}')
        print(f'AVAILABILITY API: Total available seats: {available_seats}')


        # -----------------------------
        # Find rendering info
        # -----------------------------
        rendering_data = None
        rendering_request_url = f'https://cdn-na.seatsio.net/system/public/{workspace_key}/rendering-info?event_key={event_key}'

        headers = {
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
            "x-client-tool": "Renderer",
            "accept": "*/*",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
            "sec-fetch-storage-access": "none",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "priority": "u=1, i",
        }

        response = requests.get(rendering_request_url,headers=headers,proxies=proxy)
        response_status_code = response.status_code
        response.raise_for_status()

        body = response.content

        # ensure bytes
        if isinstance(body, str):
            body = body.encode("latin-1")

        # decompress only if gzip
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)

        decoded = body.decode("utf-8")
        rendering_data = json.loads(decoded)

        if not rendering_data:
            raise ValueError("No valid rendering-info with objects found")

        else:

            # --------------------------------------------------
            # 1️⃣ Category lookup (force keys to int)
            # --------------------------------------------------
            category_lookup = {
                int(cat["key"]): cat["label"]
                for cat in rendering_data.get("categories", [])
            }

            # --------------------------------------------------
            # 2️⃣ Base DF
            # --------------------------------------------------
            df_object_categories = pd.DataFrame(
                rendering_data.get("objectCategories", {}).items(),
                columns=["objectLabelOrUuid", "category_key"]
            )

            # FIX: objectCategories values are strings → convert to int
            df_object_categories["category_key"] = (
                df_object_categories["category_key"].astype(int)
            )

            # Map label
            df_object_categories["category"] = (
                df_object_categories["category_key"]
                .map(category_lookup)
            )

            # --------------------------------------------------
            # 3️⃣ Extract event config
            # --------------------------------------------------
            for_sale_configs = rendering_data.get("forSaleConfigsPerEvent", {})

            event_config = next(
                (v for v in for_sale_configs.values() if isinstance(v, dict) and "objects" in v),
                None
            )

            # --------------------------------------------------
            # 4️⃣ Apply Seats.io logic
            # --------------------------------------------------
            if not event_config:
                # If no config exists → everything is for sale
                df_object_categories["forSale"] = True
            else:
                objects_set = set(event_config.get("objects", []))
                flag_value = event_config.get("forSale", True)

                df_object_categories["forSale"] = (
                    df_object_categories["objectLabelOrUuid"]
                    .isin(objects_set)
                    .map({True: flag_value, False: not flag_value})
                )
            # --------------------------------------------------
            # 5️⃣ Left merge with final_pricing_df
            # --------------------------------------------------

            df_object_categories_with_price = df_object_categories.merge(
                final_pricing_df[["category", "price","ticket_type"]],
                how="left",
                left_on="category",
                right_on="category"
            )

            # REMOVE tables etc
            df_object_categories_with_price = df_object_categories_with_price[
                ~df_object_categories_with_price["objectLabelOrUuid"].str.contains(
                    "table|suite|fast pass", case=False, na=False
                )
            ].reset_index(drop=True)


        df_object_categories_availability = df_object_categories_with_price.merge(
            availability_df,
            how="left",
            left_on="objectLabelOrUuid",
            right_on="objectLabelOrUuid"
        )

        # if ga is booked then remove it
        mask_remove_ga = (
            (df_object_categories_availability["seat_type"] == "GA") &
            (df_object_categories_availability["availability_status"] == "booked")
        )

        df_object_categories_availability = (
            df_object_categories_availability[~mask_remove_ga]
            .reset_index(drop=True)
        )

        # remove "reserved seating" extra token
        clean_label = (
            df_object_categories_availability["objectLabelOrUuid"]
                .str.replace(r"\s*-\s*Reserved Seating", "", regex=True, case=False)
                .str.replace(r"Reserved Seating-", "", regex=True, case=False)
        )

        parts = clean_label.str.split("-", expand=True)

        df_object_categories_availability["section_name"] = parts[0].str.strip()
        df_object_categories_availability["row"] = (
            parts[1].str.strip() if parts.shape[1] > 1 else None
        )
        df_object_categories_availability["seat_number"] = (
            parts[2] if parts.shape[1] > 2 else None
        )

        # Standing Room → GA row / seat 999
        mask_standing = df_object_categories_availability["row"].str.contains(
            "standing room", case=False, na=False
        )

        df_object_categories_availability.loc[mask_standing, "row"] = "GA"
        df_object_categories_availability.loc[mask_standing, "seat_number"] = 999
        # For GA rows only
        mask_ga = df_object_categories_availability["seat_type"] == "GA"
        df_object_categories_availability.loc[mask_ga, "row"] = "GA"
        df_object_categories_availability.loc[mask_ga, "seat_number"] = 999
        
        # Extract numeric seat number
        df_object_categories_availability["seat_number"] = (
            df_object_categories_availability["seat_number"]
                .astype(str)
                .str.extract(r'(\d+)')[0]
                .astype(float)
        )

        # Extract trailing letters (disclosure)

        seat_part = (
            df_object_categories_availability['objectLabelOrUuid']
                .astype(str)
                .str.split('-')
                .str.get(2)
        )

        df_object_categories_availability['disclosure'] = (
            seat_part.astype(str).str.extract(r'\d+([A-Za-z]+)')[0]
        )


        # --------------------------------------------------
        # Normalize text columns to lowercase safely
        # --------------------------------------------------
        category_lower = df_object_categories_availability['category'].str.lower().fillna('')
        ticket_lower = df_object_categories_availability['ticket_type'].str.lower().fillna('')
        disclosure_lower = df_object_categories_availability['disclosure'].str.lower().fillna('')

        # --------------------------------------------------
        # isRestrictedView logic
        # --------------------------------------------------
        df_object_categories_availability['isRestrictedView'] = (
            disclosure_lower.str.contains('ov') |
            category_lower.str.contains('limited view') |
            ticket_lower.str.contains('limited view')
        )

        # --------------------------------------------------
        # ADA logic
        # --------------------------------------------------
        df_object_categories_availability['ada'] = (
            category_lower.str.contains('wheelchair|accessible|ada') |
            ticket_lower.str.contains('wheelchair|accessible|ada')
        )


        # --------------------------------------------------
        # availability logic
        # --------------------------------------------------

        df_object_categories_availability["available"] = (
            (
                df_object_categories_availability["availability_status"].isna()
                | (df_object_categories_availability["availability_status"].str.lower() == "free")
            )
            & (df_object_categories_availability["category"].str.upper() != "KILLS")

            & (df_object_categories_availability["forSale"] == True)
        )

        df_object_categories_availability = df_object_categories_availability[['objectLabelOrUuid','section_name', 'row', 'seat_number', 'disclosure',
                                                                            
            'category_key', 'category', 'forSale', 'price','isRestrictedView','ada',
            'ticket_type', 'availability_status', 'quantity', 'version',
            'available', ]]

        df_object_categories_availability.drop(columns=['disclosure','availability_status','version','forSale'], inplace=True)

        reserved_seats = df_object_categories_availability[
            df_object_categories_availability["row"].notna() &
            (df_object_categories_availability["row"] != "GA")
        ].reset_index(drop=True)

        ga_areas = df_object_categories_availability[
            df_object_categories_availability["row"].isna() |
            (df_object_categories_availability["row"] == "GA")
        ].reset_index(drop=True)
        reserved_count = len(reserved_seats)
        reserved_count_available = len(reserved_seats[reserved_seats.available == True])
        reserved_count_sold = len(reserved_seats[reserved_seats.available != True])
        if reserved_count != reserved_count_available + reserved_count_sold:
            raise ValueError(f'reserved_count {reserved_count} != reserved_count_available {reserved_count_available} + reserved_count_sold {reserved_count_sold}')

        # --------------------------------------------------
        # GENERAL ADMISSION 
        # --------------------------------------------------
        ga_expanded = pd.DataFrame()
        ga_capacity_map = {}

        ga_areas = ga_areas[ga_areas.category != 'Box Office Upgrades']
        if len(ga_areas)>0:
            # --------------------------------------------------
            # Get areaPlaces dict safely
            # --------------------------------------------------

            area_places = (
                rendering_data
                    .get('forSaleConfigsPerEvent', {})
                    .get(event_key, {})
                    .get('areaPlaces', {})
            )

            if isinstance(area_places, list):
                merged = {}
                for d in area_places:
                    merged.update(d)
                area_places = merged

            # --------------------------------------------------
            # Validate logic
            # --------------------------------------------------
            # if area_places == {}:
            #     # whitelisted_event_keys = ['-2852852648697672657']

            #     # if event_key not in whitelisted_event_keys:
            #     #     raise ValueError(f'empty area places.. ')



            #     # IF required, do this call to get capacities

            #     # -----------------------------
            #     # Find seating charts response
            #     # -----------------------------

            print('capturing CHART due to area_places {}')
            charts_request_url = f'https://cdn-na.seatsio.net/system/public/{workspace_key}/charts/{chart_key}/published/{published_key}'

            response = requests.get(charts_request_url,headers=headers,proxies=proxy)
            response.raise_for_status()

            body = response.content

            # Ensure bytes
            if isinstance(body, str):
                body = body.encode("latin-1")

            # -----------------------------
            # 1️⃣ GZIP
            # -----------------------------
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)

            decoded_json = None

            # -----------------------------
            # 2️⃣ Try direct UTF-8 JSON
            # -----------------------------
            try:
                decoded_json = body.decode("utf-8")
                json.loads(decoded_json)
            except:
                decoded_json = None

            # -----------------------------
            # 3️⃣ Try bruteforce offset on raw bytes
            # -----------------------------
            if not decoded_json:
                result = _bruteforce_offset_decode(body)
                if result:
                    decoded_json = result[0]

            # -----------------------------
            # 4️⃣ Try base64 + offset
            # -----------------------------
            if not decoded_json:
                try:
                    binary_data = base64.b64decode(body)
                    result = _bruteforce_offset_decode(binary_data)
                    if result:
                        decoded_json = result[0]
                except:
                    pass

            # -----------------------------
            # 5️⃣ Try hex + offset
            # -----------------------------
            if not decoded_json:
                try:
                    text = body.decode("latin-1").strip()
                    if text and all(c in "0123456789abcdefABCDEF" for c in text):
                        encoded_bytes = bytes.fromhex(text)
                        result = _bruteforce_offset_decode(encoded_bytes)
                        if result:
                            decoded_json = result[0]
                except:
                    pass

            # -----------------------------
            # Final check
            # -----------------------------
            if not decoded_json:
                raise ValueError("Failed to decode charts JSON")

            chart_data = json.loads(decoded_json)

            # ga_capacity_map = {}

            # for floor in chart_data.get("subChartFloors", []):
            #     for area in floor.get("generalAdmissionAreas", []):
            #         capacity = area.get("capacity", 0)

            #         if capacity > 1:
            #             ga_capacity_map[area.get("label")] = capacity

            ga_capacity_map = {}

            # case 1
            for area in chart_data.get("subChart", {}).get("generalAdmissionAreas", []):
                if area.get("capacity", 0) > 1:
                    ga_capacity_map[area["label"]] = area["capacity"]

            # case 2
            for floor in chart_data.get("subChartFloors", []):
                for area in floor.get("chart", {}).get("generalAdmissionAreas", []):
                    if area.get("capacity", 0) > 1:
                        ga_capacity_map[area["label"]] = area["capacity"]

            # case 3 (this chart format)
            for floor in chart_data.get("subChartFloors", []):
                for area in floor.get("generalAdmissionAreas", []):
                    if area.get("capacity", 0) > 1:
                        ga_capacity_map[area["label"]] = area["capacity"]

            ga_areas["ga_capacity"] = ga_areas["objectLabelOrUuid"].map(ga_capacity_map)

            ga_areas['qty_remaining'] = (
                ga_areas['ga_capacity'].fillna(0) - ga_areas['quantity'].fillna(0)
            ).clip(lower=0)


            # else:
            
            #     # --------------------------------------------------
            #     # Map into GA dataframe
            #     # --------------------------------------------------
            #     ga_areas['area_capacity'] = ga_areas['objectLabelOrUuid'].map(area_places)

            #     ga_areas['qty_remaining'] = (
            #         ga_areas['area_capacity'].fillna(0) - ga_areas['quantity'].fillna(0)
            #     ).clip(lower=0)

            # Keep only rows that have remaining capacity
            ga_remaining = ga_areas[ga_areas['qty_remaining'] > 0].copy()

            # Repeat rows based on qty_remaining
            ga_expanded = ga_remaining.loc[
                ga_remaining.index.repeat(ga_remaining['qty_remaining'])
            ].copy()

            # Enumerate seat numbers per GA section
            ga_expanded['seat_number'] = (
                ga_expanded.groupby('objectLabelOrUuid')
                        .cumcount() + 1
            )

            # Force row to GA
            ga_expanded['row'] = "GA"

            # Mark as available
            ga_expanded['available'] = True

            ga_expanded = ga_expanded.reset_index(drop=True)

        if len(ga_expanded)>0:
            all_seats = pd.concat([reserved_seats, ga_expanded], ignore_index=True)
        else:
            all_seats = reserved_seats

        all_seats.drop(columns=[ 'quantity'], inplace=True)
        ga_area_count = len(ga_areas)
        total_count = reserved_count + ga_area_count
        print(f'Total reserved seats: {reserved_count}')
        print(f'Total available reserved seats: {reserved_count_available}')
        print(f'Total sold reserved seats: {reserved_count_sold}')
        print(f'Total GA areas: {ga_area_count}')
        print(f'Total GA seats available: {len(ga_expanded)}')

        all_seats.rename(columns={'price': 'total_price'}, inplace=True)

        pre_count = len(all_seats)

        def determine_seat_pattern(df: pd.DataFrame) -> pd.DataFrame:
            def classify_group(g: pd.DataFrame) -> str:
                if len(g) <= 1:
                    return "Consecutive"

                seat_nums = sorted(g["seat_number"].astype(int))
                diffs = {b - a for a, b in zip(seat_nums, seat_nums[1:])}

                # Strict consecutive
                if diffs == {1}:
                    return "Consecutive"

                # Strict parity
                if diffs == {2}:
                    return "Even" if seat_nums[0] % 2 == 0 else "Odd"

                # Mixed not allowed → fallback
                return "Consecutive"

            return (
                df
                .groupby(["section_name", "row"], group_keys=False)
                .apply(lambda g: g.assign(seat_pattern=classify_group(g)))
                .reset_index(drop=True)
            )
        all_seats = determine_seat_pattern(all_seats)

        post_count = len(all_seats)
        if pre_count != post_count:
            raise ValueError(f'pre_count {pre_count} != post_count {post_count}')


        ########### REMOVE ADA
        ########### REMOVE UNAVAILABLE
        all_seats = all_seats[
            (all_seats.available == True) &
            (all_seats.ada == False)
        ]
        all_seats = all_seats[all_seats.total_price > 0]
        all_seats.drop(
            columns=["ada", "available", "area_capacity", "qty_remaining","availability_status"],
            errors="ignore",
            inplace=True
        )
        all_seats = all_seats[~all_seats.seat_number.isna()]
        all_seats.reset_index(drop=True, inplace=True)
        all_seats = all_seats.sort_values(by=["section_name", "row", "seat_number"])
        all_seats["eventId"]=eventId
        all_seats["event_name"]=event_name
        all_seats["venue_name"]=venue_name
        all_seats["event_datetime"]=event_datetime
        all_seats["time_frame"]= time_frame

        print(
            f"[seatsio] post-filter seats = {len(all_seats)} "
            f"(removed ADA/unavailable)"
        )

        print(
            f"[seatsio] DONE | "
            f"rows={len(all_seats)} "
            f"cols={list(all_seats.columns)}"
        )

        section_counts = (
            all_seats
            .groupby("section_name")
            .size()
            .sort_values(ascending=False)
            .to_dict()
        )

        print(f"ga_capacity_map: {ga_capacity_map}")
        print(f"section_counts: {section_counts}")

        #----------------- UPDATE SECTIONS 
        if venue_name == "Tower Theatre":

            all_seats["seat_number"] = pd.to_numeric(all_seats["seat_number"], errors="coerce")

            # -------------------------
            # BALCONY
            # -------------------------
            mask_balcony = all_seats["section_name"] == "Reserved Balcony Seat"

            all_seats.loc[mask_balcony & (all_seats.seat_number <= 7), "section_name"] = "Balcony Left"
            all_seats.loc[mask_balcony & all_seats.seat_number.between(8,20), "section_name"] = "Balcony Center"
            all_seats.loc[mask_balcony & (all_seats.seat_number >= 21), "section_name"] = "Balcony Right"


            # -------------------------
            # FLOOR / TERRACE
            # -------------------------
            mask_floor = all_seats["section_name"] == "Reserved Floor Seat"

            rows_front  = ["EE","FF","GG","HH","II"]
            rows_middle = ["JJ","KK","LL","MM","NN","OO"]
            rows_rear   = ["PP","QQ","RR","SS","TT","UU","VV"]

            # FRONT TERRACE
            mask_front = mask_floor & all_seats.row.isin(rows_front)

            all_seats.loc[mask_front & (all_seats.seat_number <= 5), "section_name"] = "Front Terrace Left"
            all_seats.loc[mask_front & all_seats.seat_number.between(6,20), "section_name"] = "Front Terrace Center"
            all_seats.loc[mask_front & (all_seats.seat_number >= 21), "section_name"] = "Front Terrace Right"


            # MIDDLE TERRACE
            mask_middle = mask_floor & all_seats.row.isin(rows_middle)

            all_seats.loc[mask_middle & (all_seats.seat_number <= 5), "section_name"] = "Middle Terrace Left"
            all_seats.loc[mask_middle & all_seats.seat_number.between(6,20), "section_name"] = "Middle Terrace Center"
            all_seats.loc[mask_middle & (all_seats.seat_number >= 21), "section_name"] = "Middle Terrace Right"


            # REAR TERRACE
            mask_rear = mask_floor & all_seats.row.isin(rows_rear)

            all_seats.loc[mask_rear & (all_seats.seat_number <= 5), "section_name"] = "Rear Terrace Left"
            all_seats.loc[mask_rear & all_seats.seat_number.between(6,20), "section_name"] = "Rear Terrace Center"
            all_seats.loc[mask_rear & (all_seats.seat_number >= 21), "section_name"] = "Rear Terrace Right"


            # -------------------------
            # ORCHESTRA PIT (AA–EE)
            # -------------------------
            pit_rows = ["AA","BB","CC","DD","EE"]
            mask_pit = mask_floor & all_seats.row.isin(pit_rows)

            all_seats.loc[mask_pit & (all_seats.seat_number <= 5), "section_name"] = "Orchestra Pit Left"
            all_seats.loc[mask_pit & all_seats.seat_number.between(6,15), "section_name"] = "Orchestra Pit Center"
            all_seats.loc[mask_pit & (all_seats.seat_number >= 16), "section_name"] = "Orchestra Pit Right"



        return all_seats, logs



    finally:
        try:
            logs['statuscode'] = response_status_code
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



# --------------------------------------------------
# Utilities
# --------------------------------------------------

def _bruteforce_offset_decode(raw_bytes: bytes, source: str = "") -> Optional[tuple[str, int]]:
    """
    Try all 256 byte offsets to decode raw bytes into valid JSON.

    Uses smart prediction first: computes which offsets would make the
    first byte decode to '{' (0x7B) or '[' (0x5B), then falls back to
    iterating all 256 offsets.

    Args:
        raw_bytes: Raw bytes (already converted from hex or base64)
        source: Label for logging (e.g. "hex", "base64")

    Returns:
        Tuple of (decoded_json_str, offset) on success, None on failure
    """
    if not raw_bytes:
        return None

    first_byte = raw_bytes[0]

    # Smart prediction: try offsets that would make the first byte
    # decode to '{' (0x7B) or '[' (0x5B)
    priority_offsets = [
        (first_byte - 0x7B) % 256,
        (first_byte - 0x5B) % 256,
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
            decoded_str = decoded_bytes.decode('utf-8')
            json.loads(decoded_str)
            return (decoded_str, offset)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue

    return None


def decode_seatsio_response(response_text: str) -> Optional[str]:
    """
    Decode seats.io object-statuses response.

    The response may be:
    1. Base64-encoded binary data that contains hex-encoded JSON with offset
    2. Direct hex-encoded JSON with offset
    3. Plain JSON
    """
    import base64

    if not response_text:
        return None

    # Try 1: Direct JSON parse
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass

    # Try 2: Base64 decode first, then bruteforce byte offset
    try:
        if any(c in response_text for c in '+/=') or len(response_text) > 1000:
            binary_data = base64.b64decode(response_text)
            result = _bruteforce_offset_decode(binary_data, source="base64")
            if result:
                return result[0]
    except Exception:
        pass

    # Try 3: Direct hex offset decode
    cleaned = response_text.strip()
    if all(c in '0123456789abcdefABCDEF' for c in cleaned):
        try:
            encoded_bytes = bytes.fromhex(cleaned)
            result = _bruteforce_offset_decode(encoded_bytes, source="hex")
            if result:
                return result[0]
        except Exception:
            pass

    # Try 4: Raw binary (e.g. when fetched with .content.decode('latin-1'))
    try:
        if response_text and all(ord(c) < 256 for c in response_text):
            raw_bytes = response_text.encode('latin-1')
            result = _bruteforce_offset_decode(raw_bytes, source="binary")
            if result:
                return result[0]
    except Exception:
        pass

    return None


def decode_charts_response(response_data) -> Optional[str]:
    """
    Decode seats.io charts/published response.
    Supports:
    - gzip compressed
    - plain JSON
    - hex offset
    - base64 offset
    """

    if not response_data:
        return None

    # If it's bytes (from selenium-wire), handle directly
    if isinstance(response_data, bytes):

        # 1️⃣ GZIP
        if response_data[:2] == b"\x1f\x8b":
            try:
                decompressed = gzip.decompress(response_data)
                return decompressed.decode("utf-8")
            except Exception:
                pass

        # fallback: decode as latin-1 string
        response_text = response_data.decode("latin-1", errors="ignore")

    else:
        response_text = response_data

    # 2️⃣ Direct JSON
    try:
        json.loads(response_text)
        return response_text
    except json.JSONDecodeError:
        pass

    # 3️⃣ Hex offset
    cleaned = response_text.strip()
    if cleaned and all(c in "0123456789abcdefABCDEF" for c in cleaned):
        try:
            encoded_bytes = bytes.fromhex(cleaned)
            decoded = _bruteforce_offset_decode(encoded_bytes)
            if decoded:
                return decoded
        except Exception:
            pass

    # 4️⃣ Base64 + offset
    try:
        import base64
        binary_data = base64.b64decode(response_text)
        decoded = _bruteforce_offset_decode(binary_data)
        if decoded:
            return decoded
    except Exception:
        pass

    # 5️⃣ Raw binary offset
    try:
        raw_bytes = response_text.encode("latin-1")
        decoded = _bruteforce_offset_decode(raw_bytes)
        if decoded:
            return decoded
    except Exception:
        pass

    return None


def parse_json_safe(text: str) -> Optional[Any]:
    """Safely parse JSON, attempting decoding if needed."""
    if not text:
        return None

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try decode then parse
    decoded = decode_seatsio_response(text)
    if decoded:
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            pass

    return None