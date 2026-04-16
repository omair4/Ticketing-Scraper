#!/usr/bin/env python3
"""
Randomized scenario coverage test runner.

1. Discover all shows from /shows
2. Classify each to determine scraper_type
3. Randomly select 2 shows per scenario (or all if fewer exist)
4. For each: fetch schedule, scrape first showtime's inventory
5. Save JSON + summary per show to test_outputs/

Outputs:
    test_outputs/<scraper_type>/<slug>__<event_id_token>.json
    test_outputs/_SUMMARY.json  (overview of all runs)
"""
import sys
import os
import json
import random
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

# Inject creds stub (no DB, no proxy)
class _local_creds:
    @staticmethod
    def get_db_info():
        return None, None, None
    @staticmethod
    def get_proxy_info():
        return None, None, None, None
sys.modules["creds"] = _local_creds

import httpx
from bs4 import BeautifulSoup

from tix4_show_discovery import _scrape_shows
from tix4_determine_scraper import _first_buy_url, classify
from tix4_event_schedule import _parse_schedule

BASE_URL = "https://tix4.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
OUT_DIR = Path(__file__).parent / "test_outputs"

RANDOM_SEED = 42  # set None for true random; locked for reproducibility
SAMPLES_PER_SCENARIO = 2


def load_module(scraper_type: str):
    import importlib.util
    path = Path(__file__).parent / f"event_inventory.{scraper_type}.py"
    spec = importlib.util.spec_from_file_location(scraper_type, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    OUT_DIR.mkdir(exist_ok=True)
    random.seed(RANDOM_SEED)

    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        # Step 1: discover
        print("=" * 60)
        print("STEP 1: Discovery")
        print("=" * 60)
        resp = client.get(f"{BASE_URL}/shows")
        resp.raise_for_status()
        shows = _scrape_shows(resp.text)
        print(f"Found {len(shows)} shows")

        # Shuffle to randomize classification order
        random.shuffle(shows)

        # Step 2: classify each show
        print("\n" + "=" * 60)
        print("STEP 2: Classify every show (to build scenario pools)")
        print("=" * 60)

        scenario_pools: dict[str, list] = {
            "native_seatmap":   [],
            "external_seatmap": [],
            "native_ga":        [],
            "external_ga":      [],
            "native_ga_p6":     [],
            "js_only_seatmap":  [],
        }
        classification_errors = []

        for i, show in enumerate(shows):
            slug = show["show_slug"]
            print(f"  [{i+1}/{len(shows)}] {slug:<50}", end=" ")
            try:
                resp_ev = client.get(f"{BASE_URL}/event/{slug}")
                resp_ev.raise_for_status()
                buy_url = _first_buy_url(resp_ev.text)
                if not buy_url:
                    print("(no buy_url)")
                    continue
                full_url = f"{BASE_URL}{buy_url}" if buy_url.startswith("/") else buy_url
                resp_buy = client.get(full_url)
                resp_buy.raise_for_status()
                soup = BeautifulSoup(resp_buy.text, "lxml")
                scraper_type = classify(soup)
                provider_el = soup.select_one('input[name="external_api_provider_id"]')
                provider_id = provider_el.get("value", "") if provider_el else ""
                scenario_pools[scraper_type].append({
                    **show,
                    "buy_url": buy_url,
                    "provider_id": provider_id,
                })
                print(f"-> {scraper_type:<20}  provider={provider_id!r}")
            except Exception as e:
                print(f"ERROR: {e!r}")
                classification_errors.append({"slug": slug, "error": repr(e)})

            time.sleep(0.3)

        # Step 3: randomly sample up to SAMPLES_PER_SCENARIO per type
        print("\n" + "=" * 60)
        print("STEP 3: Random sample per scenario")
        print("=" * 60)
        selected: dict[str, list] = {}
        for scraper_type, pool in scenario_pools.items():
            n = min(SAMPLES_PER_SCENARIO, len(pool))
            selected[scraper_type] = random.sample(pool, n) if n > 0 else []
            print(f"  {scraper_type}: pool={len(pool)}, selected={n}")
            for s in selected[scraper_type]:
                print(f"      -> {s['show_slug']}")

        # Step 4: for each selected, fetch schedule + inventory, save JSON
        print("\n" + "=" * 60)
        print("STEP 4: Run inventory per selected show")
        print("=" * 60)

        summary = []
        for scraper_type, items in selected.items():
            scenario_dir = OUT_DIR / scraper_type
            scenario_dir.mkdir(exist_ok=True)
            mod = load_module(scraper_type)

            for item in items:
                slug = item["show_slug"]
                print(f"\n--- {scraper_type} | {slug} ---")

                # Get schedule
                try:
                    resp_ev = client.get(f"{BASE_URL}/event/{slug}")
                    resp_ev.raise_for_status()
                    schedule = _parse_schedule(resp_ev.text)
                    if not schedule:
                        print("  No schedule.")
                        continue
                except Exception as e:
                    print(f"  Schedule ERROR: {e!r}")
                    continue

                first_showtime = schedule[0]
                buy_url_path = first_showtime["buy_url_path"]
                event_id_token = first_showtime["event_id_token"]
                event_dt = first_showtime["event_datetime"]
                event_dt_str = event_dt.strftime("%Y-%m-%d %H:%M:%S") if event_dt else ""

                scraper_event = {
                    "eventId":        event_id_token,
                    "buy_url":        buy_url_path,
                    "event_name":     item["title"],
                    "venue_name":     "tix4.com",
                    "event_datetime": event_dt_str,
                    "time_frame":     0,
                    "scraper_type":   scraper_type,
                }

                try:
                    df, logs = mod.scrape(scraper_event, proxy=None)
                except Exception as e:
                    print(f"  Scrape ERROR: {e!r}")
                    continue

                # Save per-show JSON
                output_path = scenario_dir / f"{slug}__{event_id_token}.json"
                payload = {
                    "show_slug": slug,
                    "show_title": item["title"],
                    "scraper_type": scraper_type,
                    "provider_id": item["provider_id"],
                    "event_id_token": event_id_token,
                    "event_datetime": event_dt_str,
                    "buy_url_path": buy_url_path,
                    "full_buy_url": f"{BASE_URL}{buy_url_path}",
                    "logs": logs,
                    "seat_count": int(len(df)),
                    "price_min": float(df["total_price"].min()) if not df.empty else None,
                    "price_max": float(df["total_price"].max()) if not df.empty else None,
                    "stock_by_section": logs.get("stock_by_section", {}),
                    "seats": df.to_dict(orient="records") if not df.empty else [],
                }
                # Make seat_number / numeric fields JSON-safe
                def _fix(obj):
                    if isinstance(obj, dict):
                        return {k: _fix(v) for k, v in obj.items()}
                    if isinstance(obj, list):
                        return [_fix(v) for v in obj]
                    if hasattr(obj, "item"):  # numpy scalar
                        try:
                            return obj.item()
                        except Exception:
                            return obj
                    return obj

                payload = _fix(payload)

                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, default=str)

                print(f"  Saved -> {output_path.relative_to(Path(__file__).parent)}")
                print(f"  seats={len(df)}, sections={len(logs.get('stock_by_section',{}))}, "
                      f"price=${payload['price_min']}-{payload['price_max']}")

                summary.append({
                    "scraper_type": scraper_type,
                    "show_slug": slug,
                    "show_title": item["title"],
                    "provider_id": item["provider_id"],
                    "event_id_token": event_id_token,
                    "event_datetime": event_dt_str,
                    "full_buy_url": f"{BASE_URL}{buy_url_path}",
                    "seat_count": int(len(df)),
                    "sections": int(len(logs.get("stock_by_section", {}))),
                    "price_min": payload["price_min"],
                    "price_max": payload["price_max"],
                    "output_file": str(output_path.relative_to(Path(__file__).parent)),
                    "stock_by_section": logs.get("stock_by_section", {}),
                })

                time.sleep(0.3)

        # Overall summary
        summary_path = OUT_DIR / "_SUMMARY.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({
                "random_seed": RANDOM_SEED,
                "samples_per_scenario": SAMPLES_PER_SCENARIO,
                "scenario_counts": {k: len(v) for k, v in scenario_pools.items()},
                "classification_errors": classification_errors,
                "tests": summary,
            }, f, indent=2, default=str)

        print("\n" + "=" * 60)
        print(f"FINAL SUMMARY -> {summary_path.relative_to(Path(__file__).parent)}")
        print("=" * 60)
        for row in summary:
            print(f"  {row['scraper_type']:<20} {row['show_slug']:<45} "
                  f"seats={row['seat_count']:<5} ${row['price_min']}-{row['price_max']}")


if __name__ == "__main__":
    main()
