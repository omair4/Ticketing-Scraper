"""Shared helpers for the AAA Northeast production scraper.

Promoted from the working POC under `poc/`. Hosts:

  - URL constants and template URL formatters
  - Bright Data sticky-session proxy helpers (host/port/user/pass via `creds.get_proxy_info`
    when running under Lambda, env-var fallback otherwise)
  - Cloudflare interstitial detection (`is_blocked`)
  - Listing-page JSON parser (`parse_events_json`)
  - Red-text DOM filter (`filter_events_by_red_text`)
  - Showtime URL parser (`parse_showtimes`)
  - Template detector (`detect_template`)
  - Inventory parsers per template (`parse_quantity_first_tickets`,
    `parse_view_event_datetime_tickets`)
  - Event metadata extractor (`extract_event_metadata`)
  - `CFSession` async context manager: one Camoufox browser + listing warmup,
    reused across all per-event/per-showtime fetches in a process so
    `cf_clearance` rides through.

All functions are sync except the async session methods.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

LISTING_URL = (
    "https://aaanortheast.memberdeals.com/pages.php"
    "?sub=all-shows-events"
    "&ebg_source=navigation"
    "&ebg_medium=topnavsub-viewall"
    "&ebg_campaign=all-shows-and-events"
    "&ebg_content=shows-sports-concerts"
)

EVENT_URL_FMT = (
    "https://aaanortheast.memberdeals.com/packages.php"
    "?sub=venue_events&action=view_event&id={id}"
)

DATETIME_URL_FMT = (
    "https://aaanortheast.memberdeals.com/packages.php"
    "?sub=venue_events&action=view_event_datetime"
    "&id={id}&date={date}&time={time}&qty={qty}"
)

DEFAULT_QTY = 6

# ---------------------------------------------------------------------------
# Proxy (Bright Data ISP, sticky session)
#
# Lambda flow: `creds.get_proxy_info()` -> (host, port, user, pass).
# Local flow: env vars POC_PROXY_HOST/PORT/USER/PASS (same module that the POC
#             already uses) — we read from those when creds isn't available.
# ---------------------------------------------------------------------------

_FALLBACK_PROXY = {
    "host":     os.environ.get("POC_PROXY_HOST", "brd.superproxy.io"),
    "port":     os.environ.get("POC_PROXY_PORT", "33335"),
    "user":     os.environ.get("POC_PROXY_USER", "brd-customer-hl_b335a920-zone-proxy_test_omair"),
    "password": os.environ.get("POC_PROXY_PASS", "6aeisn18mfjn"),
}
PROXY_DISABLED = os.environ.get("POC_PROXY_DISABLED", "0") == "1"
PROXY_COUNTRY = os.environ.get("POC_PROXY_COUNTRY", "us").strip().lower()

# One sticky-session id per process. Same value is reused for every call to
# playwright_proxy(sticky=True), so all navigations in this process exit through
# the same Bright Data IP and CF's cf_clearance cookie stays valid.
_SESSION_RAND = random.randint(10_000_000, 99_999_999)


def _resolve_proxy_creds() -> dict | None:
    """Return {host, port, user, password} or None when proxy is disabled."""
    if PROXY_DISABLED:
        return None
    try:
        from creds import get_proxy_info  # type: ignore[import-not-found]
        host, port, user, password = get_proxy_info()
        if host and port and user and password:
            return {"host": host, "port": port, "user": user, "password": password}
    except Exception:
        pass
    return _FALLBACK_PROXY


def _build_username(base_user: str, sticky: bool) -> str:
    parts = [base_user]
    if PROXY_COUNTRY:
        parts.append(f"country-{PROXY_COUNTRY}")
    if sticky:
        parts.append(f"session-rand{_SESSION_RAND}")
    return "-".join(parts)


def proxy_url(sticky: bool = True) -> str | None:
    """HTTP proxy URL with embedded creds (for httpx / curl_cffi)."""
    creds = _resolve_proxy_creds()
    if not creds:
        return None
    user = _build_username(creds["user"], sticky)
    return f"http://{user}:{creds['password']}@{creds['host']}:{creds['port']}"


def playwright_proxy(sticky: bool = True) -> dict | None:
    """Playwright/Patchright/Camoufox-shaped proxy dict."""
    creds = _resolve_proxy_creds()
    if not creds:
        return None
    return {
        "server":   f"http://{creds['host']}:{creds['port']}",
        "username": _build_username(creds["user"], sticky),
        "password": creds["password"],
    }


# ---------------------------------------------------------------------------
# Cloudflare interstitial detection
# ---------------------------------------------------------------------------

CF_INTERSTITIAL_TITLES = (
    "<title>Just a moment...</title>",
    "<title>Attention Required! | Cloudflare</title>",
    "<title>Sorry, you have been blocked</title>",
)
INTERSTITIAL_MAX_BODY = 100_000


def is_blocked(status_code: int | None, body: str) -> tuple[bool, str]:
    """Return (blocked, reason). Reason is empty when not blocked."""
    if status_code is not None and status_code in (403, 429, 503):
        return True, f"http {status_code}"
    if not body:
        return True, "empty body"
    for title in CF_INTERSTITIAL_TITLES:
        if title in body:
            return True, f"interstitial: {title[7:-8]}"
    if len(body) < INTERSTITIAL_MAX_BODY and "window.ebg.analytics" not in body:
        return True, f"short body ({len(body)}B), no real content"
    return False, ""


# ---------------------------------------------------------------------------
# Listing-page parser: window.ebg.analytics.listing.items
# ---------------------------------------------------------------------------

_ANALYTICS_START = re.compile(r"window\.ebg\.analytics\s*=\s*\{")


def _walk_object(text: str, start: int) -> tuple[int, int] | None:
    """Given the index of an opening '{', return (start, end_exclusive) of the
    matching '}'. Respects double-quoted strings and backslash escapes."""
    depth = 0
    i = start
    in_str = False
    escape = False
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    return None


def _extract_analytics(html: str) -> dict | None:
    m = _ANALYTICS_START.search(html)
    if not m:
        return None
    open_brace = m.end() - 1
    span = _walk_object(html, open_brace)
    if not span:
        return None
    try:
        return json.loads(html[span[0]:span[1]])
    except json.JSONDecodeError:
        return None


def parse_events_json(html: str) -> list[dict]:
    """Listing-page items from `window.ebg.analytics.listing.items`."""
    obj = _extract_analytics(html)
    if not obj:
        return []
    items = (obj.get("listing") or {}).get("items") or []
    return [i for i in items if isinstance(i, dict)]


# ---------------------------------------------------------------------------
# Red-text filter: keep only events whose <li> card has non-empty
# .grid-card-h2 OR .promo-color child text.
# ---------------------------------------------------------------------------

def filter_events_by_red_text(html: str, all_events: list[dict]) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    by_id: dict[str, dict] = {}
    for ev in all_events:
        ev_id = str(ev.get("id", ""))
        # Listing JSON ids are prefixed with "E" for real events; DOM uses the
        # raw numeric id. Some entries have "URL" prefixes for category pages.
        if not ev_id:
            continue
        numeric = ev_id.lstrip("E")
        by_id[numeric] = ev

    kept: list[dict] = []
    for li in soup.select("li.grid-template-seen[data-filter-entity-id]"):
        ent_id = li.get("data-filter-entity-id") or ""
        if ent_id not in by_id:
            continue
        red_nodes = li.select(".grid-card-h2, .promo-color")
        red_text = " | ".join(
            t for t in (n.get_text(strip=True) for n in red_nodes) if t
        )
        if not red_text:
            continue
        kept.append({**by_id[ent_id], "red_text": red_text})
    return kept


# ---------------------------------------------------------------------------
# Event-detail page helpers
# ---------------------------------------------------------------------------

_TPL_RE = re.compile(r'data-tpl="([^"]+)"')

SHOWTIME_HREF_RE = re.compile(
    r'href="/packages\.php\?sub=venue_events(?:&amp;|&)action=view_event_datetime'
    r'(?:&amp;|&)id=(\d+)'
    r'(?:&amp;|&)date=(\d{4}-\d{2}-\d{2})'
    r'(?:&amp;|&)time=(\d{2}:\d{2}:\d{2})"',
)


def detect_template(html: str) -> str:
    """Returns e.g. 'ebg_v2/quantity_first' or 'ebg_v2/view_event_datetime'."""
    m = _TPL_RE.search(html)
    return m.group(1) if m else ""


def parse_showtimes(event_html: str) -> list[tuple[str, str, str]]:
    """Return deduped list of (numeric_id, date, time) tuples in document order."""
    seen: set[tuple[str, str, str]] = set()
    ordered: list[tuple[str, str, str]] = []
    for m in SHOWTIME_HREF_RE.finditer(event_html):
        key = (m.group(1), m.group(2), m.group(3))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def extract_event_metadata(event_html: str) -> dict:
    """Pulls event_name + venue fields from the SSR analytics blob and <title>."""
    obj = _extract_analytics(event_html) or {}
    page = obj.get("page") or {}
    venue_name = page.get("venue_name") or ""
    venue_city = page.get("venue_city") or ""
    venue_state = page.get("venue_state") or ""
    venue_zip = page.get("venue_zip")
    event_name = page.get("event_name") or ""

    if not event_name:
        m = re.search(r"<title>([^<]+)</title>", event_html)
        if m:
            t = m.group(1).strip()
            # Listing titles look like: "Tickets - The Lion King - Broadway - "
            t = re.sub(r"^Tickets\s*-\s*", "", t)
            t = re.sub(r"\s*-\s*$", "", t)
            event_name = t

    venue_full = ", ".join(p for p in (venue_name, venue_city, venue_state) if p)
    if venue_zip:
        venue_full = f"{venue_full} {venue_zip}".strip()

    return {
        "event_name": event_name,
        "venue_name": venue_full or venue_name,
        "venue_city": venue_city,
        "venue_state": venue_state,
    }


# ---------------------------------------------------------------------------
# Inventory parsers
# ---------------------------------------------------------------------------

def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    s = str(value).strip().replace("$", "").replace(",", "")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def parse_quantity_first_tickets(html: str) -> list[dict]:
    """Seat-level rows from `.ticket-panel.js-qf-ticket` panels.

    Returns dicts with: id, price (Decimal | None), section, row, seats (raw str),
    savings (raw str), marked. The `seats` field is left raw so the caller can
    handle whichever format the page uses (single int, comma list, or range)
    when qty>1. See README.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for el in soup.select(".ticket-panel.js-qf-ticket"):
        rows.append({
            "id":       el.get("data-id"),
            "price":    _to_decimal(el.get("data-price")),
            "section":  el.get("data-section") or "",
            "row":      el.get("data-row") or "",
            "seats":    el.get("data-seats") or "",
            "savings":  el.get("data-savings-sum") or "",
            "marked":   el.get("data-marked"),
        })
    return rows


def expand_seats_field(seats_raw: str, qty: int) -> list[int]:
    """Interpret `data-seats` as either:
      - comma-separated: "114,115,116,117,118,119" -> [114,115,116,117,118,119]
      - range: "114-119" -> [114,115,116,117,118,119]
      - single starting seat: "114" with qty=6 -> [114,115,116,117,118,119]
    Returns [] when the value can't be parsed.
    """
    s = (seats_raw or "").strip()
    if not s:
        return []
    if "," in s:
        nums = [n.strip() for n in s.split(",") if n.strip()]
        out: list[int] = []
        for n in nums:
            try:
                out.append(int(n))
            except ValueError:
                return []
        return out
    if "-" in s:
        try:
            lo_s, hi_s = s.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                lo, hi = hi, lo
            return list(range(lo, hi + 1))
        except ValueError:
            return []
    try:
        start = int(s)
    except ValueError:
        return []
    return list(range(start, start + max(qty, 1)))


def parse_view_event_datetime_tickets(html: str) -> list[dict]:
    """Tier-level rows from `.se-listing-item` blocks.

    Returns dicts with: tier_name, qty_options (list[int] from <option value=N>),
    price (Decimal | None), description.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for el in soup.select(".se-listing-item"):
        title_el = el.select_one(".se-listing-item-details-title")
        price_el = el.select_one(".se-listing-item-price")
        desc_el = el.select_one(".se-listing-item-details-description-box")
        # Pull qty options from the <select> rather than concatenated text.
        qty_options: list[int] = []
        for opt in el.select(".se-listing-item-section-qty option"):
            v = (opt.get("value") or "").strip()
            try:
                n = int(v)
            except ValueError:
                continue
            if n > 0:
                qty_options.append(n)
        # Price text is "Our Price: $162.00" or similar.
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", price_text)
        price = _to_decimal(m.group(1)) if m else None
        rows.append({
            "tier_name":   title_el.get_text(strip=True) if title_el else "",
            "qty_options": sorted(set(qty_options)),
            "price":       price,
            "price_text":  price_text,
            "description": desc_el.get_text(" ", strip=True) if desc_el else "",
        })
    return rows


# ---------------------------------------------------------------------------
# Camoufox session manager
# ---------------------------------------------------------------------------

PER_PAGE_TIMEOUT_MS = 120_000
SETTLE_TIMEOUT_MS = 15_000
CHALLENGE_WAIT_MS = 60_000


def _probe_exit_ip(proxy: dict) -> str | None:
    """Hit lumtest.com through the proxy and return the exit IP. Lets us pass
    an explicit IP into Camoufox's geoip= so timezone/locale align with the
    actual exit IP rather than whatever Camoufox's in-process resolver sees."""
    try:
        from curl_cffi import requests as cffi
        url = (
            f"http://{proxy['username']}:{proxy['password']}@"
            f"{proxy['server'].split('://', 1)[1]}"
        )
        r = cffi.get(
            "https://lumtest.com/myip.json",
            proxies={"http": url, "https": url},
            timeout=20, verify=True,
        )
        return r.json().get("ip")
    except Exception:
        return None


@dataclass
class CFFetchResult:
    status: int | None
    html: str
    elapsed_s: float
    blocked: bool
    block_reason: str
    template: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked and self.status not in (None, 403, 429, 503)


@dataclass
class CFSession:
    """Async context manager wrapping AsyncCamoufox + listing warmup.

    Usage:
        async with CFSession() as s:
            r = await s.fetch(EVENT_URL_FMT.format(id=24885))
            print(r.status, len(r.html))

    One instance per process. Solves Cloudflare's challenge once on the listing
    page, banks `cf_clearance`, and reuses the same browser/page for all
    subsequent navigations.
    """
    headless: bool = True
    locale: tuple[str, ...] = ("en-US",)
    sticky_proxy: bool = True
    warmup_url: str = LISTING_URL
    print_debug: bool = True

    _browser: Any = field(default=None, init=False, repr=False)
    _page: Any = field(default=None, init=False, repr=False)
    _last_url: str = field(default="", init=False, repr=False)
    _proxy: dict | None = field(default=None, init=False, repr=False)
    _exit_ip: str | None = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> "CFSession":
        from camoufox.async_api import AsyncCamoufox  # type: ignore

        self._proxy = playwright_proxy(sticky=self.sticky_proxy)
        cf_kwargs: dict = {
            "headless": self.headless,
            "humanize": True,
            "locale": list(self.locale),
        }
        if self._proxy:
            self._exit_ip = _probe_exit_ip(self._proxy)
            if self.print_debug:
                ident = self._proxy["username"].split("-session-")[0]
                print(f"[proxy] exit IP via {ident}... -> {self._exit_ip}")
            cf_kwargs["proxy"] = self._proxy
            cf_kwargs["geoip"] = self._exit_ip if self._exit_ip else True

        self._cm = AsyncCamoufox(**cf_kwargs)
        self._browser = await self._cm.__aenter__()
        self._page = await self._browser.new_page()
        await self._warmup()
        return self

    async def __aexit__(self, *exc) -> None:
        with suppress(Exception):
            await self._cm.__aexit__(*exc)

    async def _wait_past_challenge(self, timeout_ms: int = CHALLENGE_WAIT_MS) -> None:
        with suppress(Exception):
            await self._page.wait_for_function(
                "() => !/Just a moment|Attention Required|Sorry, you have been blocked/.test(document.title)",
                timeout=timeout_ms,
            )

    async def _warmup(self) -> None:
        """Visit the listing page once to bank cf_clearance + give every later
        goto() a natural Referer."""
        if self.print_debug:
            print(f"[warmup] {self.warmup_url}")
        t0 = time.perf_counter()
        await self._page.goto(
            self.warmup_url, wait_until="domcontentloaded",
            timeout=PER_PAGE_TIMEOUT_MS,
        )
        await self._wait_past_challenge()
        with suppress(Exception):
            await self._page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        self._last_url = self.warmup_url
        if self.print_debug:
            elapsed = time.perf_counter() - t0
            try:
                title = await self._page.title()
            except Exception:
                title = "?"
            print(f"    title={title!r} {elapsed:.2f}s")

    async def fetch(
        self, url: str, *, timeout_ms: int = PER_PAGE_TIMEOUT_MS,
    ) -> CFFetchResult:
        t0 = time.perf_counter()
        resp = await self._page.goto(
            url, wait_until="domcontentloaded", timeout=timeout_ms,
        )
        await self._wait_past_challenge()
        with suppress(Exception):
            await self._page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        html = await self._page.content()
        elapsed = round(time.perf_counter() - t0, 2)
        status = resp.status if resp else None
        blocked, reason = is_blocked(status, html)
        self._last_url = url
        return CFFetchResult(
            status=status, html=html, elapsed_s=elapsed,
            blocked=blocked, block_reason=reason,
            template=detect_template(html) if not blocked else "",
        )

    async def jitter(self, lo: float = 1.5, hi: float = 3.5) -> None:
        await asyncio.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Process-singleton session reuse.
#
# Each Lambda handler is its own process and incurs Camoufox warmup once
# (cold start ~30-45s). For local pipelines that chain many handler calls in
# one process we want to reuse the same browser, banking the cf_clearance
# cookie and the proxy session across all calls.
#
# Pattern: handlers call `await get_or_create_session()` and use the returned
# session WITHOUT entering it as a context manager. Cleanup happens on
# process exit via a single `close_singleton_session()` call from
# run_pipeline.py / test_local.py finally blocks.
# ---------------------------------------------------------------------------

_singleton_session: "CFSession | None" = None
_singleton_lock: "asyncio.Lock | None" = None


async def get_or_create_session(**kwargs) -> CFSession:
    global _singleton_session, _singleton_lock
    if _singleton_lock is None:
        _singleton_lock = asyncio.Lock()
    async with _singleton_lock:
        if _singleton_session is None:
            session = CFSession(**kwargs)
            await session.__aenter__()
            _singleton_session = session
    return _singleton_session


async def _close_singleton_async() -> None:
    global _singleton_session
    if _singleton_session is not None:
        with suppress(Exception):
            await _singleton_session.__aexit__(None, None, None)
        _singleton_session = None


def close_singleton_session() -> None:
    """Sync close. Safe to call from finally blocks at process exit."""
    if _singleton_session is not None:
        try:
            run_async(_close_singleton_async())
        except Exception:
            pass
    _close_loop()


# ---------------------------------------------------------------------------
# Sync convenience runner: lets a Lambda handler run async code without caring
# about loop management.
#
# We keep ONE persistent loop per process so the singleton CFSession (which
# binds Camoufox/Playwright transports to a specific loop) survives across
# multiple `lambda_handler()` calls. `asyncio.run()` would close the loop and
# orphan those transports.
# ---------------------------------------------------------------------------

_persistent_loop: "asyncio.AbstractEventLoop | None" = None


def _get_loop() -> "asyncio.AbstractEventLoop":
    global _persistent_loop
    if _persistent_loop is None or _persistent_loop.is_closed():
        _persistent_loop = asyncio.new_event_loop()
    return _persistent_loop


def run_async(coro):
    """Run an awaitable from sync code (Lambda handlers / CLI commands)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _get_loop().run_until_complete(coro)
    # We're inside a running loop already (rare). Drive on a fresh side loop.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _close_loop() -> None:
    global _persistent_loop
    if _persistent_loop is not None and not _persistent_loop.is_closed():
        with suppress(Exception):
            _persistent_loop.close()
    _persistent_loop = None
