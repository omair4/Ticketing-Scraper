from __future__ import annotations

from typing import Any, Iterable

from playwright.async_api import Page

from .browser_setup import fetch_in_page
from .config import PATHS, SITEMAP_URL, TIXR_BASE_URL


async def fetch_sitemap_index(page: Page) -> list[str]:
    """Fetch sitemap.xml and return all group-sitemap URLs."""

    text = await page.evaluate(
        """
        async (url) => {
          const res = await fetch(url, { headers: { 'Accept': 'application/xml' } });
          return await res.text();
        }
        """,
        SITEMAP_URL,
    )
    urls: list[str] = []
    import re

    for match in re.finditer(r"<loc>([^<]+)</loc>", text):
        loc = match.group(1).strip()
        if "group-sitemap" in loc:
            urls.append(loc)
    return urls


async def fetch_group_slugs_from_sitemap(page: Page, sitemap_url: str) -> list[str]:
    """Fetch a group-sitemap-*.xml file and extract group slugs."""

    text = await page.evaluate(
        """
        async (url) => {
          const res = await fetch(url, { headers: { 'Accept': 'application/xml' } });
          return await res.text();
        }
        """,
        sitemap_url,
    )
    import re

    slugs: list[str] = []
    for match in re.finditer(r"<loc>([^<]+)</loc>", text):
        loc = match.group(1).strip()
        m = re.search(r"tixr\\.com/groups/([^/]+)", loc)
        if m:
            slugs.append(m.group(1))
    # dedupe, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for slug in slugs:
        if slug not in seen:
            seen.add(slug)
            result.append(slug)
    return result


async def resolve_group_id(page: Page, slug: str) -> int | None:
    """Call /api/page/requirements for a group URL and return setup.groupId."""

    from urllib.parse import urlencode

    params = urlencode({"url": f"/groups/{slug}", "queryParams": ""})
    url = f"{TIXR_BASE_URL}{PATHS.PAGE_REQUIREMENTS}?{params}"
    data = await fetch_in_page(page, url)
    setup = data.get("setup") or {}
    group_id = setup.get("groupId")
    if isinstance(group_id, int):
        return group_id
    return None


async def fetch_group_events(page: Page, group_id: int) -> list[dict[str, Any]]:
    """GET /api/groups/{group_id}/events -> list of events."""

    url = f"{TIXR_BASE_URL}{PATHS.GROUPS}/{group_id}/events"
    data = await fetch_in_page(page, url)
    if isinstance(data, list):
        return data
    return []


async def fetch_event_detail(page: Page, event_id: int) -> dict[str, Any]:
    """GET /api/events/{event_id} -> full event JSON."""

    url = f"{TIXR_BASE_URL}{PATHS.EVENTS}/{event_id}"
    data = await fetch_in_page(page, url)
    if isinstance(data, dict):
        return data
    return {}


def dedupe_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate events by id while preserving order."""

    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for ev in events:
        eid = ev.get("id")
        if isinstance(eid, int) and eid not in seen:
            seen.add(eid)
            result.append(ev)
    return result

