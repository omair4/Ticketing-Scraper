"""POC step 2: fetch the first N event-detail pages with Camoufox.

Reuses a single Camoufox browser/context across all fetches so the
`cf_clearance` cookie obtained on the first navigation rides through
every subsequent one — far faster and far less likely to re-trigger
Cloudflare's challenge gate than spawning a fresh browser per event.

Source of event IDs: the most recent listing-page run saved to
`poc/output/camoufox.html`. Filters to entries with `id` starting with
'E' (real events; 'URL' prefix entries are category/venue pages).

Output:
  poc/output/events/E<id>.html — one file per event
  Summary table on stdout
"""
from __future__ import annotations

import asyncio
import random
import sys
from dataclasses import dataclass
from pathlib import Path

# Windows console default is cp1252; event titles include chars like ™ / ’.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from poc.shared import (
    OUTPUT_DIR,
    URL as LISTING_URL,
    Timer,
    is_blocked,
    parse_events,
    playwright_proxy,
)

LISTING_HTML = OUTPUT_DIR / "camoufox.html"
EVENTS_DIR = OUTPUT_DIR / "events"

EVENT_URL_FMT = (
    "https://aaanortheast.memberdeals.com/packages.php"
    "?sub=venue_events&action=view_event&id={id}"
)

DEFAULT_LIMIT = 10
PER_PAGE_TIMEOUT_MS = 60_000
SETTLE_TIMEOUT_MS = 15_000
CHALLENGE_WAIT_MS = 60_000
MIN_DELAY_S = 1.5
MAX_DELAY_S = 3.5


@dataclass
class EventFetch:
    event_id: str          # e.g. "E24885"
    numeric_id: str        # e.g. "24885"
    name: str
    status: int | None = None
    blocked: bool = False
    block_reason: str = ""
    html_size: int = 0
    elapsed_s: float = 0.0
    html_path: str = ""
    error: str | None = None


def select_events(limit: int) -> list[EventFetch]:
    if not LISTING_HTML.exists():
        raise SystemExit(
            f"Missing listing HTML at {LISTING_HTML}. "
            f"Run `python -m poc.try_camoufox` first."
        )
    html = LISTING_HTML.read_text(encoding="utf-8", errors="replace")
    items = [e for e in parse_events(html) if e.get("id", "").startswith("E")]
    chosen = items[:limit]
    return [
        EventFetch(
            event_id=e["id"],
            numeric_id=e["id"][1:],
            name=e.get("name", ""),
        )
        for e in chosen
    ]


async def _wait_past_challenge(page, timeout_ms: int) -> None:
    """Block until the page is no longer on Cloudflare's 'Just a moment...'
    interstitial. Returns when title changes OR timeout elapses (in which
    case the caller will detect the block via is_blocked())."""
    try:
        await page.wait_for_function(
            "() => !/Just a moment|Attention Required|Sorry, you have been blocked/.test(document.title)",
            timeout=timeout_ms,
        )
    except Exception:
        pass


def _probe_exit_ip(proxy: dict) -> str | None:
    """Hit lumtest.com through the proxy and return the exit IP. Lets us
    pass an explicit IP into Camoufox's geoip= so timezone/locale align."""
    try:
        from curl_cffi import requests as cffi
        url = (
            f"http://{proxy['username']}:{proxy['password']}@"
            f"{proxy['server'].split('://', 1)[1]}"
        )
        r = cffi.get(
            "https://lumtest.com/myip.json",
            proxies={"http": url, "https": url},
            timeout=20,
            verify=True,
        )
        return r.json().get("ip")
    except Exception:
        return None


async def fetch_all(events: list[EventFetch]) -> None:
    from camoufox.async_api import AsyncCamoufox

    # sticky=True pins the Bright Data exit IP for this whole run so the
    # cf_clearance cookie obtained on the listing visit stays valid across
    # all 10 event-detail navigations.
    proxy = playwright_proxy(sticky=True)
    cf_kwargs: dict = {
        "headless": True,
        "humanize": True,
        # Pin browser locale to en-US so Cloudflare doesn't see a fingerprint
        # mismatch (US IP + Russian-locale browser = bot signal).
        "locale": ["en-US"],
    }
    if proxy:
        exit_ip = _probe_exit_ip(proxy)
        print(f"[proxy] exit IP via {proxy['username'].split('-session-')[0]}... -> {exit_ip}")
        cf_kwargs["proxy"] = proxy
        # Pass the explicit IP so Camoufox's geoip lookup is deterministic
        # rather than dependent on whatever IP the in-process resolver sees.
        cf_kwargs["geoip"] = exit_ip if exit_ip else True

    EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    async with AsyncCamoufox(**cf_kwargs) as browser:
        page = await browser.new_page()

        # 1) Warm-up: visit the listing first. Two purposes:
        #    a) Solve Cloudflare's challenge once and bank the cf_clearance
        #       cookie before we deep-link into event detail pages.
        #    b) On every subsequent goto(), the browser auto-sets Referer to
        #       the listing URL, which CF's Bot Management scores as "human
        #       clicked from the index page."
        print(f"[warmup] {LISTING_URL}")
        with Timer() as t_warm:
            await page.goto(
                LISTING_URL, wait_until="domcontentloaded",
                timeout=PER_PAGE_TIMEOUT_MS,
            )
            await _wait_past_challenge(page, CHALLENGE_WAIT_MS)
            try:
                await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
            except Exception:
                pass
        warm_title = await page.title()
        print(f"    title={warm_title!r} {t_warm.elapsed:.2f}s")
        if "Just a moment" in warm_title or "Attention Required" in warm_title:
            print("    WARNING: warmup did not clear Cloudflare; downstream fetches will likely 403.")

        # 2) Per-event navigations now ride the cf_clearance cookie + sticky IP.
        for i, ev in enumerate(events, start=1):
            url = EVENT_URL_FMT.format(id=ev.numeric_id)
            print(f"[{i}/{len(events)}] {ev.event_id} {ev.name!r}")
            try:
                with Timer() as t:
                    resp = await page.goto(
                        url, wait_until="domcontentloaded",
                        timeout=PER_PAGE_TIMEOUT_MS,
                    )
                    await _wait_past_challenge(page, CHALLENGE_WAIT_MS)
                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=SETTLE_TIMEOUT_MS,
                        )
                    except Exception:
                        pass
                    html = await page.content()
                ev.elapsed_s = round(t.elapsed, 2)
                ev.status = resp.status if resp else None
                ev.html_size = len(html)
                blocked, reason = is_blocked(ev.status, html)
                ev.blocked = blocked
                ev.block_reason = reason
                out = EVENTS_DIR / f"{ev.event_id}.html"
                out.write_text(html, encoding="utf-8", errors="replace")
                ev.html_path = str(out)
                print(
                    f"    status={ev.status} size={ev.html_size:>8}B "
                    f"blocked={ev.blocked} {ev.elapsed_s}s"
                )
            except Exception as e:
                ev.error = f"{type(e).__name__}: {e}"
                print(f"    ERROR: {ev.error}")

            if i < len(events):
                delay = random.uniform(MIN_DELAY_S, MAX_DELAY_S)
                await asyncio.sleep(delay)


def print_summary(events: list[EventFetch]) -> None:
    print()
    header = (
        f"{'event_id':<9} {'status':<7} {'blocked':<8} "
        f"{'size':>9} {'elapsed':>8}   name"
    )
    print(header)
    print("-" * 100)
    for ev in events:
        if ev.error:
            print(f"{ev.event_id:<9} ERROR    -        -          -      {ev.name} :: {ev.error}")
            continue
        print(
            f"{ev.event_id:<9} "
            f"{(str(ev.status) if ev.status is not None else '-'):<7} "
            f"{('yes' if ev.blocked else 'no'):<8} "
            f"{ev.html_size:>9} "
            f"{ev.elapsed_s:>7.2f}s   "
            f"{ev.name[:50]}"
        )
    n_pass = sum(1 for e in events if not e.error and not e.blocked and e.html_size > 100_000)
    print()
    print(f"PASS: {n_pass}/{len(events)}    output dir: {EVENTS_DIR}")


def main() -> int:
    limit = DEFAULT_LIMIT
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            print(f"usage: python -m poc.fetch_events [limit] (default {DEFAULT_LIMIT})")
            return 2

    events = select_events(limit)
    print(f"Fetching {len(events)} events from {LISTING_HTML.name}\n")
    asyncio.run(fetch_all(events))
    print_summary(events)

    n_pass = sum(1 for e in events if not e.error and not e.blocked and e.html_size > 100_000)
    return 0 if n_pass == len(events) else 1


if __name__ == "__main__":
    sys.exit(main())
