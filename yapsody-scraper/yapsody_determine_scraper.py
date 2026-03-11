"""
Lambda 2: Navigate booking page, intercept seatsio / pricing_info XHR,
extract workspace_key, chart_key, event_key, and pricing_list.

Input:  { event_id, venue_code, booking_url }
Output: { event_id, scraper_type, workspace_key, event_key, chart_key, pricing_list }
"""
import sys
sys.path.append('/mnt/access')

from creds import get_db_info
from creds import get_proxy_info
import json

import importlib.util
import os
import asyncio
import re
import httpx
import pymysql
from bs4 import BeautifulSoup


def _load_dotted_module(name: str, filename: str):
    """Load a file whose name contains dots (e.g. event_inventory.browser.py)."""
    path = os.path.join(os.path.dirname(__file__), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_browser_mod = _load_dotted_module("browser", "event_inventory.browser.py")
async_browser = _browser_mod.async_browser

DATABASE = "yapsody"

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_performance_id(html: str, event_id: str) -> str | None:
    """
    Find the first performance_id in the event index HTML.

    Looks for links like:
      /event/book/{event_id}/{performance_id}
      href="...yapsody.com/event/book/{event_id}/{performance_id}"
    Also looks for JSON data embedded in <script> tags.
    """
    # Pattern 1: /event/book/{event_id}/{perf_id}
    pattern = rf"/event/book/{re.escape(event_id)}/(\d+)"
    match = re.search(pattern, html)
    if match:
        return match.group(1)

    # Pattern 2: data- attribute containing performance id
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(attrs={"data-performance-id": True}):
        return tag["data-performance-id"]

    # Pattern 3: JSON embedded in <script> — look for performanceId
    scripts = soup.find_all("script")
    for script in scripts:
        if not script.string:
            continue
        m = re.search(r'"performanceId"\s*:\s*(\d+)', script.string)
        if m:
            return m.group(1)
        m = re.search(r"performanceId['\"]?\s*[:=]\s*['\"]?(\d+)", script.string)
        if m:
            return m.group(1)

    return None


def _parse_workspace_key(url: str) -> str | None:
    """Extract workspace key from seatsio CDN URL.

    URL pattern: .../system/public/{workspace_key}/rendering-info...
                 .../system/public/{workspace_key}/events/...
    """
    match = re.search(r"/system/public/([^/]+)/", url)
    return match.group(1) if match else None


def _parse_chart_key(url: str) -> str | None:
    """Extract chart key from seatsio charts/published URL.

    URL pattern: .../charts/{chart_key}/published/...
    """
    match = re.search(r"/charts/([^/]+)/published/", url)
    return match.group(1) if match else None


def _extract_pricing_from_dom(page_content: str) -> list | None:
    """
    Fallback for Next.js venues where pricing_info is server-side rendered.
    Looks for seats_io_categories in inline JSON or script tags.
    """
    # Look for seats_io_categories in page source
    match = re.search(
        r'"seats_io_categories"\s*:\s*(\[[\s\S]*?\])',
        page_content,
    )
    if match:
        try:
            raw = match.group(1)
            # Clean up common JS artifacts
            raw = re.sub(r",\s*([}\]])", r"\1", raw)
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Look for full price block
    match = re.search(
        r'"price"\s*:\s*\{[^}]*"seats_io_categories"\s*:\s*(\[[\s\S]*?\])',
        page_content,
    )
    if match:
        try:
            raw = match.group(1)
            raw = re.sub(r",\s*([}\]])", r"\1", raw)
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    return None


async def _run_browser(venue_code: str, event_id: str, performance_id: str,
                       proxy_host, proxy_port, proxy_username, proxy_password):
    """
    Launch Playwright browser, navigate to booking page, intercept XHR.

    Returns:
        (workspace_key, chart_key, pricing_list)
    """
    book_url = f"https://{venue_code}.yapsody.com/event/book/{event_id}/{performance_id}"
    print(f"Navigating to booking page: {book_url}")

    captured_pricing: dict = {}       # {"data": [...]}
    captured_seatsio_urls: list = []  # list of seatsio CDN URLs seen

    async with async_browser(proxy_host, proxy_port, proxy_username, proxy_password) as browser:
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "accept-language": "en-US,en;q=0.9",
            },
        )

        page = await context.new_page()

        async def on_response(response):
            url = response.url
            status = response.status

            # Capture pricing_info XHR (AngularJS venues send this as XHR)
            if "/pricing_info" in url and status == 200:
                try:
                    data = await response.json()
                    captured_pricing["data"] = data
                    print(f"Captured pricing_info from: {url}")
                except Exception as e:
                    print(f"Failed to parse pricing_info JSON: {e}")

            # Track all seatsio CDN requests
            if "cdn-na.seatsio.net" in url:
                captured_seatsio_urls.append(url)
                print(f"Seatsio CDN request: {url}")

        page.on("response", on_response)

        await page.goto(book_url, wait_until="networkidle", timeout=30000)

        # Extra wait for dynamic content and lazy XHR calls
        await page.wait_for_timeout(5000)

        page_content = await page.content()

    # ---------------------------------------------------------------------------
    # Extract workspace_key and chart_key from captured CDN URLs
    # ---------------------------------------------------------------------------
    workspace_key = None
    chart_key = None

    for url in captured_seatsio_urls:
        if not workspace_key:
            workspace_key = _parse_workspace_key(url)
        if not chart_key and "/charts/" in url and "/published/" in url:
            chart_key = _parse_chart_key(url)
        if workspace_key and chart_key:
            break

    print(f"workspace_key: {workspace_key}")
    print(f"chart_key: {chart_key}")

    # ---------------------------------------------------------------------------
    # Extract pricing_list
    # ---------------------------------------------------------------------------
    pricing_list = None

    if "data" in captured_pricing:
        raw = captured_pricing["data"]
        # Structure: { performance_info: {...}, price: { seats_io_categories: [...], ... } }
        pricing_list = raw.get("price", {}).get("seats_io_categories")
        if pricing_list:
            print(f"pricing_list from XHR: {len(pricing_list)} categories")

    if not pricing_list:
        # Fallback: Next.js or SSR venues — try DOM extraction
        print("pricing_info not in XHR — trying DOM fallback")
        pricing_list = _extract_pricing_from_dom(page_content)
        if pricing_list:
            print(f"pricing_list from DOM: {len(pricing_list)} categories")

    if not pricing_list:
        raise ValueError("Could not capture pricing_list from XHR or DOM")

    if not workspace_key:
        raise ValueError("Could not extract workspace_key from seatsio CDN URLs")

    if not chart_key:
        raise ValueError("Could not extract chart_key from seatsio CDN URLs")

    return workspace_key, chart_key, pricing_list


def lambda_handler(event, context):
    event_id    = event["event_id"]
    venue_code  = event["venue_code"]
    booking_url = event["booking_url"]   # {venue_code}.yapsody.com/event/index/{id}/{slug}

    print(f"determine_scraper | event_id={event_id} venue_code={venue_code}")

    proxy_host, proxy_port, proxy_username, proxy_password = get_proxy_info()
    proxies = None
    if proxy_host:
        proxy_url = f"http://{proxy_username}:{proxy_password}@{proxy_host}:{proxy_port}"
        proxies = proxy_url

    # ------------------------------------------------------------------
    # Step 1: Fetch event index page to find performance_id
    # ------------------------------------------------------------------
    print(f"Fetching event page: {booking_url}")
    with httpx.Client(headers=HEADERS, timeout=30, proxy=proxies) as client:
        resp = client.get(booking_url)
        resp.raise_for_status()
        html = resp.text

    performance_id = _extract_performance_id(html, event_id)
    if not performance_id:
        raise ValueError(f"Could not find performance_id in event page HTML for event {event_id}")

    print(f"performance_id: {performance_id}")
    event_key = f"Performance-{performance_id}"
    ticket_url = f"https://{venue_code}.yapsody.com/event/book/{event_id}/{performance_id}"

    # ------------------------------------------------------------------
    # Step 2: Browser navigation + XHR interception
    # ------------------------------------------------------------------
    workspace_key, chart_key, pricing_list = asyncio.run(
        _run_browser(venue_code, event_id, performance_id,
                     proxy_host, proxy_port, proxy_username, proxy_password)
    )

    print(f"event_key: {event_key}")
    print(f"pricing_list categories: {len(pricing_list)}")

    # ------------------------------------------------------------------
    # Step 3: Update event_discovery in MySQL
    # ------------------------------------------------------------------
    host, user, password = get_db_info()
    conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        database=DATABASE,
        autocommit=True,
    )

    try:
        with conn.cursor() as cur:
            sql = """
                UPDATE event_discovery
                SET scraper_type    = %s,
                    performance_id  = %s,
                    ticket_url      = %s,
                    workspace_key   = %s,
                    event_key       = %s,
                    chart_key       = %s,
                    pricing_list    = %s,
                    modified_at     = UTC_TIMESTAMP()
                WHERE event_id = %s
            """
            cur.execute(sql, (
                "yapsody_seatsio",
                performance_id,
                ticket_url,
                workspace_key,
                event_key,
                chart_key,
                json.dumps(pricing_list),
                event_id,
            ))
            print(f"Updated event_discovery for event_id={event_id}")
    finally:
        conn.close()

    return {
        "statusCode": 200,
        "event_id":       event_id,
        "scraper_type":   "yapsody_seatsio",
        "performance_id": performance_id,
        "ticket_url":     ticket_url,
        "workspace_key":  workspace_key,
        "event_key":      event_key,
        "chart_key":      chart_key,
        "pricing_list":   pricing_list,
    }
