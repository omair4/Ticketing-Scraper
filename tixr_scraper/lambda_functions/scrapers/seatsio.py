from __future__ import annotations

from typing import Any, Dict, List

import httpx

from ..shared.config import SEATSIO_CDN_BASE
from ._decoder import decode_charts_response, parse_json_safe


def _get(url: str) -> str:
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    # seats.io often returns binary; we pass bytes through to decoder
    return resp.text


def scrape_seatsio(
    workspace_key: str | None,
    event_key: str | None,
    chart_key: str | None,
    pricing_list: List[Dict[str, Any]] | None,
) -> List[Dict[str, Any]]:
    """Fetch seat-level inventory from seats.io CDN."""

    if not workspace_key or not event_key:
        return []

    headers = {"X-Workspace-Key": workspace_key}

    # rendering-info
    rendering_url = f"{SEATSIO_CDN_BASE}/api/events/{event_key}/rendering-info"
    rendering_resp = httpx.get(rendering_url, headers=headers, timeout=30)
    rendering_resp.raise_for_status()
    rendering_json = parse_json_safe(rendering_resp.text) or {}

    # object-statuses
    statuses_url = f"{SEATSIO_CDN_BASE}/api/events/{event_key}/object-statuses"
    statuses_resp = httpx.get(statuses_url, headers=headers, timeout=30)
    statuses_resp.raise_for_status()
    statuses_json = parse_json_safe(statuses_resp.text) or {}

    seats: List[Dict[str, Any]] = []

    objects = statuses_json.get("objects") or {}
    price_by_category: Dict[str, Dict[str, Any]] = {}

    if pricing_list:
        for pl in pricing_list:
            # Very simple mapping; can be enriched later
            category = pl.get("name") or pl.get("label") or "default"
            price_by_category[category] = pl

    for object_id, payload in objects.items():
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        category = (payload.get("category") or {}).get("label") if isinstance(payload.get("category"), dict) else None
        price_info = price_by_category.get(category or "default", {})
        seat = {
            "object_id": object_id,
            "section": payload.get("section"),
            "row": payload.get("row"),
            "seat": payload.get("seat"),
            "status": status,
            "category": category,
            "price": price_info.get("price"),
        }
        seats.append(seat)

    return seats

