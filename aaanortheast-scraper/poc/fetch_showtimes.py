"""POC step 3: fetch ticket-availability data for individual showtimes.

Two SSR templates exist on this site, both ship inventory in the static HTML
(no AJAX needed):

  - `ebg_v2/quantity_first` (e.g. The Lion King): seat-level rows in
    `.ticket-panel.js-qf-ticket[data-price][data-section][data-row][data-seats]`.
  - `ebg_v2/view_event_datetime` (e.g. Harry Potter): tier-level rows in
    `.se-listing-item` blocks with `.se-listing-item-details-title`,
    `.se-listing-item-section-qty`, `.se-listing-item-price`.

Flow:
  1. Read N event-detail HTMLs already saved by `fetch_events.py`.
  2. Parse each event's SSR `<a href="...view_event_datetime...">` links to
     enumerate (date, time) showtime pairs.
  3. For the first M showtimes per event, navigate to the datetime URL with
     `qty=1` appended.
  4. Extract ticket rows from the resulting HTML using whichever template the
     page declares (`data-tpl` on `#li_script`).

Reuses the Camoufox warmup pattern from `fetch_events.py`: one browser/context
across the whole run, listing-page warmup to bank `cf_clearance`, then sticky-
session proxy IP so the cookie stays valid.

Output:
  poc/output/showtimes/E<id>_<date>_<time>.html
  poc/output/showtimes/E<id>_<date>_<time>.tickets.json
  Summary table on stdout
"""
from __future__ import annotations

import asyncio
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Windows console default is cp1252; titles/venues include chars like ' / ™.
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
    playwright_proxy,
)

EVENTS_DIR = OUTPUT_DIR / "events"
SHOWTIMES_DIR = OUTPUT_DIR / "showtimes"

# Default targets: first 3 events that fetch_events.py captured.
DEFAULT_EVENT_IDS = ("E24885", "E22874", "E10885")
DEFAULT_PER_EVENT = 3
DEFAULT_QTY = 1

EVENT_URL_FMT = (
    "https://aaanortheast.memberdeals.com/packages.php"
    "?sub=venue_events&action=view_event&id={id}"
)
DATETIME_URL_FMT = (
    "https://aaanortheast.memberdeals.com/packages.php"
    "?sub=venue_events&action=view_event_datetime"
    "&id={id}&date={date}&time={time}&qty={qty}"
)

# Regex to pull showtime hrefs out of event-detail HTML. The SSR href has the
# form: /packages.php?sub=venue_events&action=view_event_datetime&id=24885&date=2026-05-08&time=19:00:00
# (HTML-encoded `&amp;` between params; no `qty` -- we append it ourselves.)
SHOWTIME_HREF_RE = re.compile(
    r'href="/packages\.php\?sub=venue_events(?:&amp;|&)action=view_event_datetime'
    r'(?:&amp;|&)id=(\d+)'
    r'(?:&amp;|&)date=(\d{4}-\d{2}-\d{2})'
    r'(?:&amp;|&)time=(\d{2}:\d{2}:\d{2})"',
)

PER_PAGE_TIMEOUT_MS = 120_000  # Cold-start cascade burns ~45s on warmup; first nav needs slack.
SETTLE_TIMEOUT_MS = 15_000
CHALLENGE_WAIT_MS = 60_000
MIN_DELAY_S = 1.5
MAX_DELAY_S = 3.5


@dataclass
class ShowtimeFetch:
    event_id: str          # "E24885"
    numeric_id: str        # "24885"
    date: str              # "2026-05-08"
    time: str              # "19:00:00"
    qty: int = DEFAULT_QTY
    status: int | None = None
    blocked: bool = False
    block_reason: str = ""
    html_size: int = 0
    elapsed_s: float = 0.0
    html_path: str = ""
    template: str = ""              # e.g. "ebg_v2/quantity_first"
    ticket_count: int = 0           # rows extracted from the HTML
    tickets_path: str = ""          # path to dumped JSON ticket list
    error: str | None = None

    @property
    def url(self) -> str:
        return DATETIME_URL_FMT.format(
            id=self.numeric_id, date=self.date, time=self.time, qty=self.qty,
        )

    @property
    def label(self) -> str:
        return f"{self.event_id} {self.date} {self.time}"


def parse_showtimes(html: str) -> list[tuple[str, str, str]]:
    """Return deduped list of (numeric_id, date, time) tuples in document order."""
    seen: set[tuple[str, str, str]] = set()
    ordered: list[tuple[str, str, str]] = []
    for m in SHOWTIME_HREF_RE.finditer(html):
        key = (m.group(1), m.group(2), m.group(3))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def select_showtimes(
    event_ids: list[str], per_event: int,
) -> list[ShowtimeFetch]:
    selections: list[ShowtimeFetch] = []
    for eid in event_ids:
        path = EVENTS_DIR / f"{eid}.html"
        if not path.exists():
            raise SystemExit(
                f"Missing event HTML at {path}. "
                f"Run `python -m poc.fetch_events` first."
            )
        html = path.read_text(encoding="utf-8", errors="replace")
        showtimes = parse_showtimes(html)
        if not showtimes:
            print(f"[warn] {eid}: no showtimes parsed", file=sys.stderr)
            continue
        for numeric_id, date, time_ in showtimes[:per_event]:
            selections.append(
                ShowtimeFetch(
                    event_id=eid,
                    numeric_id=numeric_id,
                    date=date,
                    time=time_,
                )
            )
    return selections


async def _wait_past_challenge(page, timeout_ms: int) -> None:
    try:
        await page.wait_for_function(
            "() => !/Just a moment|Attention Required|Sorry, you have been blocked/.test(document.title)",
            timeout=timeout_ms,
        )
    except Exception:
        pass


_TPL_RE = re.compile(r'data-tpl="([^"]+)"')


def detect_template(html: str) -> str:
    m = _TPL_RE.search(html)
    return m.group(1) if m else ""


def parse_tickets(html: str, template: str) -> list[dict]:
    """Extract ticket rows from the SSR HTML. Returns [] when nothing matches."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    if "quantity_first" in template:
        rows: list[dict] = []
        for el in soup.select(".ticket-panel.js-qf-ticket"):
            rows.append({
                "id": el.get("data-id"),
                "price": el.get("data-price"),
                "section": el.get("data-section"),
                "row": el.get("data-row"),
                "seats": el.get("data-seats"),
                "savings": el.get("data-savings-sum"),
                "marked": el.get("data-marked"),
            })
        return rows

    if "view_event_datetime" in template:
        rows = []
        for el in soup.select(".se-listing-item"):
            title_el = el.select_one(".se-listing-item-details-title")
            qty_el = el.select_one(".se-listing-item-section-qty")
            price_el = el.select_one(".se-listing-item-price")
            desc_el = el.select_one(".se-listing-item-details-description-box")
            rows.append({
                "tier_name": title_el.get_text(strip=True) if title_el else None,
                "qty": qty_el.get_text(" ", strip=True) if qty_el else None,
                "price": price_el.get_text(" ", strip=True) if price_el else None,
                "description": desc_el.get_text(" ", strip=True) if desc_el else None,
            })
        return rows

    # Unknown template -- fall back to anything with data-price.
    return [
        {"price": el.get("data-price"), "tag": el.name, "class": el.get("class")}
        for el in soup.select("[data-price]")
    ]


def _probe_exit_ip(proxy: dict) -> str | None:
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


async def fetch_all(targets: list[ShowtimeFetch]) -> None:
    from camoufox.async_api import AsyncCamoufox

    proxy = playwright_proxy(sticky=True)
    cf_kwargs: dict = {
        "headless": True,
        "humanize": True,
        "locale": ["en-US"],
    }
    if proxy:
        exit_ip = _probe_exit_ip(proxy)
        print(
            f"[proxy] exit IP via {proxy['username'].split('-session-')[0]}... "
            f"-> {exit_ip}"
        )
        cf_kwargs["proxy"] = proxy
        cf_kwargs["geoip"] = exit_ip if exit_ip else True

    SHOWTIMES_DIR.mkdir(parents=True, exist_ok=True)

    # Group showtimes by event so we can do listing -> event -> datetime once
    # per event. That referer chain looks the most human-like.
    by_event: dict[str, list[ShowtimeFetch]] = {}
    for t in targets:
        by_event.setdefault(t.event_id, []).append(t)

    async with AsyncCamoufox(**cf_kwargs) as browser:
        page = await browser.new_page()

        # 1) Listing-page warmup: solve CF once and bank cf_clearance.
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

        # 2) For each event: visit event page (so referer points to event),
        #    then walk through its showtime URLs.
        n_done = 0
        n_total = len(targets)
        for eid, items in by_event.items():
            event_url = EVENT_URL_FMT.format(id=items[0].numeric_id)
            print(f"\n[event] {eid} -> {event_url}")
            try:
                await page.goto(
                    event_url, wait_until="domcontentloaded",
                    timeout=PER_PAGE_TIMEOUT_MS,
                )
                await _wait_past_challenge(page, CHALLENGE_WAIT_MS)
                try:
                    await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
                except Exception:
                    pass
            except Exception as e:
                print(f"    event page error: {type(e).__name__}: {e}")

            await asyncio.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))

            for ev in items:
                n_done += 1
                print(f"[{n_done}/{n_total}] {ev.label}")
                try:
                    with Timer() as t:
                        resp = await page.goto(
                            ev.url, wait_until="domcontentloaded",
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
                    safe_time = ev.time.replace(":", "")
                    out = SHOWTIMES_DIR / f"{ev.event_id}_{ev.date}_{safe_time}.html"
                    out.write_text(html, encoding="utf-8", errors="replace")
                    ev.html_path = str(out)

                    if not blocked:
                        ev.template = detect_template(html)
                        tickets = parse_tickets(html, ev.template)
                        ev.ticket_count = len(tickets)
                        if tickets:
                            tpath = SHOWTIMES_DIR / (
                                f"{ev.event_id}_{ev.date}_{safe_time}.tickets.json"
                            )
                            import json as _json
                            tpath.write_text(
                                _json.dumps(
                                    {"template": ev.template, "tickets": tickets},
                                    indent=2,
                                ),
                                encoding="utf-8", errors="replace",
                            )
                            ev.tickets_path = str(tpath)

                    print(
                        f"    status={ev.status} size={ev.html_size:>8}B "
                        f"blocked={ev.blocked} tpl={ev.template or '?'} "
                        f"tickets={ev.ticket_count} {ev.elapsed_s}s"
                    )
                except Exception as e:
                    ev.error = f"{type(e).__name__}: {e}"
                    print(f"    ERROR: {ev.error}")

                if n_done < n_total:
                    await asyncio.sleep(random.uniform(MIN_DELAY_S, MAX_DELAY_S))


def print_summary(targets: list[ShowtimeFetch]) -> None:
    print()
    header = (
        f"{'showtime':<32} {'status':<7} {'blocked':<8} "
        f"{'template':<26} {'tix':>5} {'size':>9} {'elapsed':>8}"
    )
    print(header)
    print("-" * len(header))
    for ev in targets:
        if ev.error:
            print(f"{ev.label:<32} ERROR    -        {'-':<26}     -      -      :: {ev.error}")
            continue
        print(
            f"{ev.label:<32} "
            f"{(str(ev.status) if ev.status is not None else '-'):<7} "
            f"{('yes' if ev.blocked else 'no'):<8} "
            f"{(ev.template or '?'):<26} "
            f"{ev.ticket_count:>5} "
            f"{ev.html_size:>9} "
            f"{ev.elapsed_s:>7.2f}s"
        )
    n_data = sum(
        1 for e in targets
        if not e.error and not e.blocked and e.ticket_count > 0
    )
    print()
    print(f"WITH TICKET DATA: {n_data}/{len(targets)}    output dir: {SHOWTIMES_DIR}")


def main() -> int:
    event_ids = list(DEFAULT_EVENT_IDS)
    per_event = DEFAULT_PER_EVENT
    if len(sys.argv) > 1:
        try:
            per_event = int(sys.argv[1])
        except ValueError:
            print(
                f"usage: python -m poc.fetch_showtimes [per_event] (default {DEFAULT_PER_EVENT})"
            )
            return 2

    targets = select_showtimes(event_ids, per_event)
    if not targets:
        print("No showtimes selected; aborting.", file=sys.stderr)
        return 2
    print(
        f"Fetching {len(targets)} showtimes "
        f"({len(event_ids)} events x up to {per_event} each)\n"
    )
    asyncio.run(fetch_all(targets))
    print_summary(targets)

    n_data = sum(
        1 for e in targets
        if not e.error and not e.blocked and e.ticket_count > 0
    )
    return 0 if n_data == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
