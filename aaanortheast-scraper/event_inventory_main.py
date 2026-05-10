"""Stage 6 -- Event inventory router.

Mirrors `tix4-scraper/event_inventory_main.py`. importlib-loads the dotted
scraper module that matches the inventory job's `scraper_type`, calls its
`scrape(scraper_event, proxy=proxy)` -> `(DataFrame, logs_dict)`, and (in
local mode) appends the rows to `output/inventory_<run_id>.csv` and the
logs dict to `output/logs_<run_id>.json`.

Lambda input:
  {"eventId": str, "event_name": str, "venue_name": str,
   "event_datetime": str, "ticket_url": str, "scraper_type": str,
   "qty": int, "time_frame": int, "run_id": str | None}

Lambda output:
  {"rows": int, "scraper_type": str, "eventId": str}
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pandas as pd

# Lambda flow uses `creds.get_proxy_info`; local flow injects local_creds as
# `creds` in test_local.py / run_pipeline.py before importing this module.
try:
    from creds import get_proxy_info  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - local fallback only
    def get_proxy_info():
        return (None, None, None, None)

OUTPUT_DIR = Path(os.environ.get("AAANE_OUTPUT_DIR", "output"))


def _load_dotted_module(name: str, filename: str):
    """Load a file whose name contains dots (e.g. event_inventory.quantity_first.py)."""
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SCRAPERS = {
    "quantity_first":      _load_dotted_module("quantity_first",      "event_inventory.quantity_first.py"),
    "view_event_datetime": _load_dotted_module("view_event_datetime", "event_inventory.view_event_datetime.py"),
}

INVENTORY_COLUMNS = [
    "eventId", "event_name", "venue_name", "event_datetime",
    "objectLabelOrUuid", "section_name", "row", "seat_number",
    "total_price", "status", "seat_pattern", "isRestrictedView",
    "category", "ticket_type", "time_frame",
]


def _append_csv(df: pd.DataFrame, run_id: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"inventory_{run_id}.csv"
    header = not csv_path.exists()
    # Reorder + add missing columns so the file schema is stable.
    cols = list(INVENTORY_COLUMNS)
    if "total_stock" in df.columns:
        cols = cols + ["total_stock"]
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[cols]
    df.to_csv(csv_path, mode="a", header=header, index=False)
    return str(csv_path)


def _append_logs(logs: dict, run_id: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / f"logs_{run_id}.json"
    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    existing.append(logs)
    log_path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")
    return str(log_path)


def lambda_handler(event, context):
    event = event or {}
    scraper_type = event.get("scraper_type", "")
    if scraper_type not in _SCRAPERS:
        raise ValueError(f"Unknown scraper_type: {scraper_type!r}")

    proxy_host, proxy_port, proxy_user, proxy_pass = get_proxy_info()
    proxy_str = None
    if proxy_host:
        proxy_str = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"

    scraper_event = {
        "eventId":        event["eventId"],
        "event_name":     event.get("event_name", ""),
        "venue_name":     event.get("venue_name", ""),
        "event_datetime": event.get("event_datetime", ""),
        "ticket_url":     event["ticket_url"],
        "scraper_type":   scraper_type,
        "qty":            int(event.get("qty", 6)),
        "time_frame":     int(event.get("time_frame", 0)),
    }

    mod = _SCRAPERS[scraper_type]
    df, logs = mod.scrape(scraper_event, proxy=proxy_str)

    run_id = event.get("run_id") or time.strftime("%Y%m%d_%H%M%S")
    csv_path = ""
    if df is not None and not df.empty:
        csv_path = _append_csv(df, run_id)
    log_path = _append_logs(logs, run_id)

    return {
        "rows":         int(len(df)) if df is not None else 0,
        "scraper_type": scraper_type,
        "eventId":      event["eventId"],
        "csv_path":     csv_path,
        "log_path":     log_path,
        "total_stock":  logs.get("total_stock", 0),
    }
