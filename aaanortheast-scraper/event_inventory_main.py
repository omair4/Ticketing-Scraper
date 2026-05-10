"""Stage 6 -- Event inventory router.

Mirrors `tix4-scraper/event_inventory_main.py`. Owns the fetch, auto-detects
the SSR template from the response (the `data-tpl` attribute on the
`#li_script` only appears on the datetime page, not the event page), and
dispatches to the matching dotted parser module:

  - `event_inventory.quantity_first.py` -- seat-level (Broadway etc.)
  - `event_inventory.view_event_datetime.py` -- tier-level (Harry Potter etc.)

Each parser exports `parse(html, event) -> (DataFrame, partial_logs_dict)`.

Lambda input:
  {"eventId": str, "event_name": str, "venue_name": str,
   "event_datetime": str, "ticket_url": str, "scraper_type": str (optional hint),
   "qty": int, "time_frame": int, "run_id": str | None}

Lambda output:
  {"rows": int, "scraper_type": str, "eventId": str, "csv_path": str,
   "log_path": str, "total_stock": int}
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pandas as pd

import shared
from shared import detect_template, get_or_create_session

# Lambda flow uses creds.get_proxy_info; local flow injects local_creds as
# `creds` in test_local.py / run_pipeline.py before importing this module.
try:
    from creds import get_proxy_info  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    def get_proxy_info():
        return (None, None, None, None)

OUTPUT_DIR = Path(os.environ.get("AAANE_OUTPUT_DIR", "output"))


def _load_dotted_module(name: str, filename: str):
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PARSERS = {
    "quantity_first":      _load_dotted_module("quantity_first",      "event_inventory.quantity_first.py"),
    "view_event_datetime": _load_dotted_module("view_event_datetime", "event_inventory.view_event_datetime.py"),
}

INVENTORY_COLUMNS = [
    "eventId", "event_name", "venue_name", "event_datetime",
    "objectLabelOrUuid", "section_name", "row", "seat_number",
    "total_price", "status", "seat_pattern", "isRestrictedView",
    "category", "ticket_type", "time_frame",
]


def _detect_scraper_type(template: str, hint: str = "") -> str:
    if "quantity_first" in template:
        return "quantity_first"
    if "view_event_datetime" in template:
        return "view_event_datetime"
    if hint in _PARSERS:
        return hint
    return ""


async def _scrape_async(event: dict) -> tuple[pd.DataFrame, dict]:
    eid = event["eventId"]
    qty = int(event.get("qty", 6))
    logs = {
        "eventId":         eid,
        "event_name":      event.get("event_name", ""),
        "venue_name":      event.get("venue_name", ""),
        "event_datetime":  event.get("event_datetime", ""),
        "ticket_url":      event["ticket_url"],
        "scraper_type":    "",
        "qty":             qty,
        "time_frame":      int(event.get("time_frame", 0)),
        "statuscode":      None,
        "total_stock":     0,
        "stock_by_section": {},
        "error_message":   None,
    }

    session = await get_or_create_session()
    result = await session.fetch(event["ticket_url"])
    logs["statuscode"] = result.status

    if result.blocked:
        logs["error_message"] = f"blocked: {result.block_reason}"
        return pd.DataFrame(), logs

    template = result.template or detect_template(result.html)
    scraper_type = _detect_scraper_type(template, event.get("scraper_type", ""))
    if not scraper_type:
        logs["error_message"] = f"unknown template: {template!r}"
        return pd.DataFrame(), logs

    parser = _PARSERS[scraper_type]
    df, partial = parser.parse(result.html, event)
    logs["scraper_type"] = scraper_type
    logs["total_stock"] = partial.get("total_stock", 0)
    logs["stock_by_section"] = partial.get("stock_by_section", {})
    if partial.get("venue_name") and not event.get("venue_name"):
        logs["venue_name"] = partial["venue_name"]
    return df, logs


def _append_csv(df: pd.DataFrame, run_id: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / f"inventory_{run_id}.csv"
    header = not csv_path.exists()
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
    df, logs = shared.run_async(_scrape_async(event))

    run_id = event.get("run_id") or time.strftime("%Y%m%d_%H%M%S")
    csv_path = ""
    if df is not None and not df.empty:
        csv_path = _append_csv(df, run_id)
    log_path = _append_logs(logs, run_id)

    return {
        "rows":         int(len(df)) if df is not None else 0,
        "scraper_type": logs.get("scraper_type", ""),
        "eventId":      event.get("eventId", ""),
        "csv_path":     csv_path,
        "log_path":     log_path,
        "total_stock":  logs.get("total_stock", 0),
        "error":        logs.get("error_message"),
    }
