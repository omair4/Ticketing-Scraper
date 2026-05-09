"""Shared helpers for the AAA Northeast Cloudflare-bypass POC.

Each runner script (`try_curlcffi.py`, `try_patchright.py`, `try_camoufox.py`)
fetches the listing URL, then uses `is_blocked()` and `parse_events()` here so
all three judge success identically. Each runner emits one JSON line on stdout
that `compare.py` collects and renders as a table.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

URL = (
    "https://aaanortheast.memberdeals.com/pages.php"
    "?sub=all-shows-events"
    "&ebg_source=navigation"
    "&ebg_medium=topnavsub-viewall"
    "&ebg_campaign=all-shows-and-events"
    "&ebg_content=shows-sports-concerts"
)

EXPECTED_EVENT_COUNT_HINT = 299

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# --- Bright Data ISP proxy (POC credentials) -------------------------------
# Override any of these via environment variables. Set POC_PROXY_DISABLED=1
# to bypass the proxy and hit the site directly (e.g. for an A/B run).
PROXY_HOST = os.environ.get("POC_PROXY_HOST", "brd.superproxy.io")
PROXY_PORT = os.environ.get("POC_PROXY_PORT", "33335")
PROXY_USER = os.environ.get(
    "POC_PROXY_USER", "brd-customer-hl_b335a920-zone-proxy_test_omair"
)
PROXY_PASS = os.environ.get("POC_PROXY_PASS", "6aeisn18mfjn")
PROXY_DISABLED = os.environ.get("POC_PROXY_DISABLED", "0") == "1"
# Pin the proxy exit to a specific country. AAA Northeast is a US-only site,
# so any non-US IP is highly suspicious to Cloudflare and gets challenged.
# Empty string = let Bright Data pick (avoid).
PROXY_COUNTRY = os.environ.get("POC_PROXY_COUNTRY", "us").strip().lower()

# Bright Data sticky-session support. When `sticky=True`, the exit IP is pinned
# for the lifetime of this Python process — critical for keeping CF's
# `cf_clearance` cookie valid across multiple navigations. Implemented via
# Bright Data's `-country-<cc>-session-rand<N>` username suffix.
import random as _random  # noqa: E402

_SESSION_RAND = _random.randint(10_000_000, 99_999_999)


def _user(sticky: bool) -> str:
    parts = [PROXY_USER]
    if PROXY_COUNTRY:
        parts.append(f"country-{PROXY_COUNTRY}")
    if sticky:
        parts.append(f"session-rand{_SESSION_RAND}")
    return "-".join(parts)


def proxy_url(sticky: bool = False) -> str | None:
    """HTTP proxy URL with embedded creds (for curl_cffi / requests).

    `sticky=True` pins the exit IP for this process via Bright Data session ID."""
    if PROXY_DISABLED:
        return None
    return f"http://{_user(sticky)}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"


def playwright_proxy(sticky: bool = False) -> dict | None:
    """Proxy config dict for Playwright/Patchright/Camoufox.

    `sticky=True` pins the exit IP for this process via Bright Data session ID.
    Use this when you need cookie/session continuity across multiple navigations
    (e.g. defeating Cloudflare once and riding the cf_clearance cookie)."""
    if PROXY_DISABLED:
        return None
    return {
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": _user(sticky),
        "password": PROXY_PASS,
    }

# Markers that ONLY appear on real Cloudflare interstitial pages — not on
# legitimately served CF-protected pages. (`cdn-cgi/challenge-platform` and
# `Enable JavaScript and cookies` were dropped because both ship on regular
# pages too: the former as a script src, the latter as <noscript> boilerplate.)
CF_INTERSTITIAL_TITLES = (
    "<title>Just a moment...</title>",
    "<title>Attention Required! | Cloudflare</title>",
    "<title>Sorry, you have been blocked</title>",
)

# A real interstitial body is small (~10-50 KB). The real SSR is ~2.3 MB.
# Anything under this and missing real content is treated as blocked.
INTERSTITIAL_MAX_BODY = 100_000


def is_blocked(status_code: int | None, body: str) -> tuple[bool, str]:
    """Return (blocked, reason). Reason is empty when not blocked.

    A response is "blocked" when:
      - HTTP status is 403/429/503, OR
      - body is empty, OR
      - body contains a Cloudflare interstitial <title>, OR
      - body is suspiciously small AND lacks the page's real-content marker.
    """
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


def parse_events(html: str) -> list[dict]:
    """Extract `listing.items` from the inline `window.ebg.analytics` object.

    Returns [] when the object is missing or malformed (which is itself a
    useful signal — a Cloudflare interstitial has no such object)."""
    m = _ANALYTICS_START.search(html)
    if not m:
        return []
    open_brace = m.end() - 1
    span = _walk_object(html, open_brace)
    if not span:
        return []
    blob = html[span[0]:span[1]]
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return []
    listing = obj.get("listing") or {}
    items = listing.get("items") or []
    return [i for i in items if isinstance(i, dict)]


@dataclass
class MethodResult:
    method: str
    status: int | None = None
    blocked: bool = False
    block_reason: str = ""
    event_count: int = 0
    elapsed_s: float = 0.0
    error: str | None = None
    html_path: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def save_html(method: str, html: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{method}.html"
    path.write_text(html or "", encoding="utf-8", errors="replace")
    return path


def emit(result: MethodResult) -> None:
    """Print the result as a single JSON line on stdout for `compare.py`."""
    print(result.to_json(), flush=True)


class Timer:
    def __enter__(self):
        self._t = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *_):
        self.elapsed = time.perf_counter() - self._t
