# aaanortheast-scraper (POC)

Cloudflare-bypass bake-off for `aaanortheast.memberdeals.com`. Three methods —
**`camoufox`**, **`curl_cffi`**, **`patchright`** (in run order) — each fetch
the all-shows-events listing once, report whether Cloudflare let them through,
and count events extracted from the embedded
`window.ebg.analytics.listing.items` JSON (~299 entries on the saved SSR copy).

All three methods route through a Bright Data ISP proxy by default
(`brd.superproxy.io:33335`). Credentials live in `poc/shared.py` and can be
overridden via `POC_PROXY_USER` / `POC_PROXY_PASS` / `POC_PROXY_HOST` /
`POC_PROXY_PORT`. Set `POC_PROXY_DISABLED=1` to A/B against direct egress.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
cd aaanortheast-scraper
uv sync
uv run patchright install chromium
uv run python -m camoufox fetch
```

## Run

Bake-off (all three, one comparison table):

```bash
uv run python -m poc.compare
```

Per-method, for triage:

```bash
uv run python -m poc.try_camoufox
uv run python -m poc.try_curlcffi
uv run python -m poc.try_patchright              # headed by default
POC_HEADLESS=1 uv run python -m poc.try_patchright
```

Direct (no proxy) A/B run:

```bash
POC_PROXY_DISABLED=1 uv run python -m poc.compare
```

Raw HTML dumps land in `poc/output/<method>.html` — open in a browser to
confirm the real listing rendered, not a Cloudflare interstitial.

## Decision rule for production

- `curl_cffi` PASS → ship `curl_cffi` (fastest, Lambda-friendly).
- `curl_cffi` FAIL, `patchright` PASS → ship Patchright.
- Only `camoufox` PASS → ship Camoufox; budget for the heavier deploy.
- All FAIL → IP/ASN problem, not tooling. Add a residential proxy and re-run.
