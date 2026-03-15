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


class EventExpiredError(Exception):
    """Raised when the event index page redirects away (event removed/expired)."""


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


# Selectors that work for both Angular (a.js-get-ticket-btn) and Next.js (button with text).
GET_TICKETS_SELECTORS = [
    "a.js-get-ticket-btn",           # Angular
    ".js-get-ticket-btn",
    'button:has-text("Get Tickets")',  # Next.js
    'a:has-text("Get Tickets")',
    'button:has-text("Buy Tickets")',
    'a:has-text("Buy Tickets")',
    'a.btn:has-text("Get Tickets")',
]


async def _get_performance_id_with_browser(
    booking_url: str, event_id: str,
    proxy_host, proxy_port, proxy_username, proxy_password,
) -> str | None:
    """
    Load event index with Playwright (client-rendered), click Get Tickets,
    extract performance_id from URL or from intercepted /performances API response.
    Handles both AngularJS and Next.js venues.
    """
    captured_performances: list = []  # list of performance dicts from API

    async with async_browser(proxy_host, proxy_port, proxy_username, proxy_password) as browser:
        context = await browser.new_context(
            user_agent=HEADERS["user-agent"],
            extra_http_headers={"accept-language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()

        async def on_response_async(response):
            url = response.url
            if "api.yapsody.com" in url and "/performances" in url and "pricing_info" not in url and "/performances/" not in url:
                try:
                    body = await response.body()
                    data = json.loads(body.decode("utf-8", errors="replace"))
                    inner = data.get("data", data)
                    if isinstance(inner, dict) and "performances" in inner:
                        perfs = inner["performances"]
                        if isinstance(perfs, list) and perfs:
                            captured_performances.extend(perfs)
                            print(f"Captured {len(perfs)} performances from API")
                except Exception:
                    pass

        page.on("response", on_response_async)

        await page.goto(booking_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        # Redirect check: event expired/removed
        final_url = page.url
        if f"/event/index/{event_id}" not in final_url and f"/event/book/{event_id}/" not in final_url:
            raise EventExpiredError(f"Event page redirected to {final_url} (event {event_id} may be expired)")

        # Optional: scroll to perf list (Angular only; no-op on Next.js)
        try:
            await page.locator("#perf-list").first.scroll_into_view_if_needed(timeout=5000)
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        # Wait for any "Get Tickets" element to appear (framework bootstrap)
        btn_found = False
        for sel in GET_TICKETS_SELECTORS:
            try:
                loc = page.locator(sel).first
                await loc.wait_for(state="visible", timeout=4000)
                btn_found = True
                break
            except Exception:
                continue
        if not btn_found:
            await page.wait_for_timeout(5000)
        else:
            await page.wait_for_timeout(1000)

        # Click first matching Get Tickets button
        for sel in GET_TICKETS_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.click()
                await page.wait_for_timeout(5000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                url = page.url
                m = re.search(r"/event/book/" + re.escape(event_id) + r"/(\d+)", url)
                if m:
                    return m.group(1)
                html = await page.content()
                pid = _extract_performance_id(html, event_id)
                if pid:
                    return pid
            except Exception:
                continue

        # Fallback: use first performance from intercepted API response
        if captured_performances:
            first_perf = captured_performances[0]
            pid = first_perf.get("id") or first_perf.get("performance_id")
            if pid is not None:
                return str(pid)

        html = await page.content()
    return _extract_performance_id(html, event_id)


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


def _parse_chart_version(url: str) -> str | None:
    """Extract published version from seatsio URL (e.g. .../published/6 -> '6')."""
    match = re.search(r"/published/(\d+)(?:/|$)", url)
    return match.group(1) if match else None


def _extract_pricing_from_dom(page_content: str) -> list | None:
    """
    Fallback for Next.js venues where pricing_info is server-side rendered.
    Looks for seats_io_categories in inline JSON or script tags.
    Handles both plain JSON and escaped JSON (RSC payloads use \\" for quotes).
    """
    def _try_parse_array(text: str, start: int) -> list | None:
        """From position `start` (pointing at '['), extract the balanced JSON array."""
        depth = 0
        for i in range(start, min(start + 20000, len(text))):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    raw = text[start:i + 1]
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        pass
                    # Try unescaping (RSC payloads)
                    try:
                        unescaped = raw.replace('\\"', '"').replace("\\\\", "\\")
                        return json.loads(unescaped)
                    except json.JSONDecodeError:
                        pass
                    return None
        return None

    # Pattern 1: unescaped JSON — "seats_io_categories": [...]
    for pattern in [r'"seats_io_categories"\s*:\s*\[',
                    r'"seatsIoCategories"\s*:\s*\[']:
        match = re.search(pattern, page_content)
        if match:
            arr_start = page_content.index("[", match.start())
            result = _try_parse_array(page_content, arr_start)
            if result:
                return result

    # Pattern 2: escaped JSON in RSC/Next.js payloads — \"seats_io_categories\":[...]
    for pattern in [r'\\"seats_io_categories\\":\[',
                    r'\\"seatsIoCategories\\":\[',
                    r'\\"seats_io_categories\\"\s*:\s*\[',
                    r'\\"seatsIoCategories\\"\s*:\s*\[']:
        match = re.search(pattern, page_content)
        if match:
            arr_start = page_content.index("[", match.start())
            result = _try_parse_array(page_content, arr_start)
            if result:
                return result

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

            # Capture full pricing_info XHR only (exclude .../pricing_info/access_code_enabled)
            if "pricing_info" in url and "access_code_enabled" not in url and status == 200:
                try:
                    body = await response.body()
                    data = json.loads(body.decode("utf-8", errors="replace"))
                    # API may return { price: {...} } or { data: { price: {...} }, status_code: 200 }
                    price_obj = data.get("price") or (data.get("data") or {}).get("price") or {}
                    has_cats = bool(price_obj.get("seats_io_categories") or price_obj.get("seatsIoCategories"))
                    if has_cats or "data" not in captured_pricing:
                        captured_pricing["data"] = data
                        print(f"Captured pricing_info from: {url}" + (" (with categories)" if has_cats else ""))
                except Exception as e:
                    print(f"Failed to parse pricing_info JSON: {e}")

            # Track all seatsio CDN requests
            if "cdn-na.seatsio.net" in url:
                captured_seatsio_urls.append(url)
                print(f"Seatsio CDN request: {url}")

        page.on("response", on_response)

        await page.goto(book_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)

        # Next.js venues: click "Choose Your Seats" to load seats.io iframe
        choose_seats_selectors = [
            'button:has-text("Choose Your Seats")',
            'a:has-text("Choose Your Seats")',
            '[data-testid]:has-text("Choose Your Seats")',
            'button:has-text("Select Best Seats")',
            'a:has-text("Select Best Seats")',
        ]
        for sel in choose_seats_selectors:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=3000)
                await btn.click()
                await page.wait_for_timeout(5000)
                print("Clicked Choose Your Seats — waiting for seats.io to load")
                break
            except Exception:
                continue

        # Wait for main page to request pricing_info (may fire before or after iframe)
        try:
            await page.wait_for_response(
                lambda r: "pricing_info" in r.url,
                timeout=15000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # Poll until we have workspace_key, chart_key, and pricing (iframe may load after)
        def _has_pricing():
            raw = captured_pricing.get("data") or {}
            price_obj = raw.get("price") or (raw.get("data") or {}).get("price") or {}
            return bool(price_obj.get("seats_io_categories") or price_obj.get("seatsIoCategories"))
        for _ in range(20):
            await page.wait_for_timeout(2000)
            has_wk = any(_parse_workspace_key(u) for u in captured_seatsio_urls)
            has_ck = any("/charts/" in u and "/published/" in u for u in captured_seatsio_urls)
            has_pr = _has_pricing()
            if has_wk and has_ck and has_pr:
                break
        await page.wait_for_timeout(2000)

        page_content = await page.content()
        await page.wait_for_timeout(1500)

    # ---------------------------------------------------------------------------
    # Extract workspace_key, chart_key, chart_version from captured CDN URLs
    # ---------------------------------------------------------------------------
    workspace_key = None
    chart_key = None
    chart_version = None

    for url in captured_seatsio_urls:
        if not workspace_key:
            workspace_key = _parse_workspace_key(url)
        if "/charts/" in url and "/published/" in url:
            if not chart_key:
                chart_key = _parse_chart_key(url)
            if not chart_version:
                chart_version = _parse_chart_version(url)
        if workspace_key and chart_key:
            break

    print(f"workspace_key: {workspace_key}")
    print(f"chart_key: {chart_key}")
    if chart_version:
        print(f"chart_version: {chart_version}")

    # ---------------------------------------------------------------------------
    # Extract pricing_list
    # ---------------------------------------------------------------------------
    pricing_list = None

    if "data" in captured_pricing:
        raw = captured_pricing["data"]
        # API may return { price: {...} } or { data: { price: {...} }, status_code: 200 }
        price_obj = raw.get("price") or (raw.get("data") or {}).get("price") or {}
        pricing_list = price_obj.get("seats_io_categories") or price_obj.get("seatsIoCategories")
        if pricing_list:
            print(f"pricing_list from XHR: {len(pricing_list)} categories")
        else:
            print(f"XHR pricing keys: {list(raw.keys())} | price keys: {list(price_obj.keys())}")

    if not pricing_list:
        # Fallback: DOM extraction
        print("pricing_info not in XHR — trying DOM fallback")
        pricing_list = _extract_pricing_from_dom(page_content)
        if pricing_list:
            print(f"pricing_list from DOM: {len(pricing_list)} categories")

    if not pricing_list:
        # Fallback: direct API fetch (full pricing_info endpoint), with retry
        pricing_url = f"https://api.yapsody.com/online/events/{event_id}/performances/{performance_id}/pricing_info"
        req_headers = dict(HEADERS)
        req_headers["Referer"] = book_url
        req_headers["Origin"] = f"https://{venue_code}.yapsody.com"
        for _ in range(2):
            try:
                with httpx.Client(headers=req_headers, timeout=20) as client:
                    resp = client.get(pricing_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        pricing_list = data.get("price", {}).get("seats_io_categories")
                        if pricing_list:
                            print(f"pricing_list from direct API: {len(pricing_list)} categories")
                            break
            except Exception as e:
                print(f"Direct pricing_info fetch failed: {e}")
            if pricing_list:
                break

    if not pricing_list and workspace_key and chart_key:
        # Last resort: build minimal pricing_list from seatsio rendering-info
        event_key_val = f"Performance-{performance_id}"
        try:
            ri_url = f"https://cdn-na.seatsio.net/system/public/{workspace_key}/rendering-info?event_key={event_key_val}"
            with httpx.Client(headers=HEADERS, timeout=15) as client:
                resp = client.get(ri_url)
                if resp.status_code == 200:
                    ri = resp.json()
                    cats = ri.get("categories") or []
                    unique_keys = []
                    seen = set()
                    for c in cats:
                        k = c.get("key") if isinstance(c, dict) else c
                        if k is not None:
                            ki = int(k) if not isinstance(k, int) else k
                            if ki not in seen:
                                seen.add(ki)
                                unique_keys.append(ki)
                    if unique_keys:
                        # Use -1 as sentinel for "price unknown" so inventory keeps seats (filter excludes only price==0)
                        pricing_list = [{"category": k, "ticketTypes": [{"label": "General", "price": -1}]} for k in sorted(unique_keys)]
                        print(f"pricing_list from rendering-info: {len(pricing_list)} categories (minimal, price unknown)")
        except Exception as e:
            print(f"Rendering-info fallback failed: {e}")

    if not pricing_list:
        raise ValueError("Could not capture pricing_list from XHR, DOM, or API")

    if not workspace_key:
        raise ValueError("Could not extract workspace_key from seatsio CDN URLs")

    if not chart_key:
        raise ValueError("Could not extract chart_key from seatsio CDN URLs")

    return workspace_key, chart_key, pricing_list, chart_version


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
    # Step 1: Get performance_id via Playwright (HTML is always client-rendered)
    # ------------------------------------------------------------------
    print(f"Loading event page with browser: {booking_url}")
    performance_id = None
    for attempt in range(2):
        try:
            performance_id = asyncio.run(
                _get_performance_id_with_browser(
                    booking_url, event_id,
                    proxy_host, proxy_port, proxy_username, proxy_password,
                )
            )
            if performance_id:
                break
            if attempt == 0:
                print("No performance_id; retrying...")
        except EventExpiredError:
            raise
        except Exception as e:
            if attempt == 0:
                print(f"Attempt 1 failed: {e}; retrying...")
                continue
            raise
    if not performance_id:
        raise ValueError(f"Could not find performance_id for event {event_id}")

    print(f"performance_id: {performance_id}")
    event_key = f"Performance-{performance_id}"
    ticket_url = f"https://{venue_code}.yapsody.com/event/book/{event_id}/{performance_id}"

    # ------------------------------------------------------------------
    # Step 2: Browser navigation + XHR interception
    # ------------------------------------------------------------------
    workspace_key, chart_key, pricing_list, chart_version = asyncio.run(
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

    payload = {
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
    if chart_version:
        payload["chart_version"] = chart_version
    return payload
