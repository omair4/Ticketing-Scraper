"""
Lambda 2: Discover Tixr events for a given sitemap batch.

Analogue of `prekindle_show_discovery.py`:
- Takes one sitemap batch job from Step Functions (output of
  `tixr_provide_discovery_params.lambda_handler`).
- Uses Playwright (via shared helpers) to:
  - fetch the given group-sitemap XML,
  - extract group slugs,
  - resolve slugs to groupIds,
  - fetch `/api/groups/{groupId}/events`,
  - emit normalized event records.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from .shared.browser_setup import CaptchaError, with_tixr_session
from .shared.models import EventRecord
from .shared.tixr_api import (
    dedupe_events,
    fetch_group_events,
    fetch_group_slugs_from_sitemap,
    resolve_group_id,
)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Step Functions Map will pass a single batch element from the `batches` list
    returned by `tixr_provide_discovery_params`.

    Expected event shape:
    {
      "sitemap_url": "https://www.tixr.com/catalog/group-sitemap-1.xml",
      "description": "Tixr group sitemap batch"
    }
    """

    sitemap_url = (event or {}).get("sitemap_url")
    if not sitemap_url:
        return {"error": "missing sitemap_url", "events": [], "num_events": 0}

    async def _run() -> Dict[str, Any]:
        async def _inner(browser, page) -> Dict[str, Any]:
            slugs = await fetch_group_slugs_from_sitemap(page, sitemap_url)
            events: List[Dict[str, Any]] = []
            groups_processed = 0
            groups_with_events = 0
            errors: List[str] = []

            for slug in slugs:
                try:
                    group_id = await resolve_group_id(page, slug)
                    if not group_id:
                        continue
                    groups_processed += 1
                    group_events = await fetch_group_events(page, group_id)
                    if group_events:
                        groups_with_events += 1
                    for ev in group_events:
                        event_id = ev.get("id")
                        if not isinstance(event_id, int):
                            continue
                        ticket_url = ev.get("url") or ""
                        name = ev.get("name") or ""
                        venue = ev.get("venue") or {}
                        record = EventRecord(
                            event_id=event_id,
                            event_name=name,
                            group_slug=slug,
                            group_id=group_id,
                            ticket_url=ticket_url,
                            venue_name=venue.get("name") if isinstance(venue, dict) else None,
                            start_date=ev.get("startDate"),
                            end_date=ev.get("endDate"),
                        )
                        events.append(record.model_dump())
                except CaptchaError as ce:
                    errors.append(str(ce))
                    break
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{slug}: {exc}")
                    continue

            deduped = dedupe_events(events)
            return {
                "sitemap_url": sitemap_url,
                "groups_processed": groups_processed,
                "groups_with_events": groups_with_events,
                "events": deduped,
                "num_events": len(deduped),
                "errors": errors,
            }

        return await with_tixr_session(_inner)

    result: Dict[str, Any] = asyncio.run(_run())
    return result

