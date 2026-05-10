# aaanortheast-scraper

Production scraper for `aaanortheast.memberdeals.com`. Mirrors the multi-date
Lambda+local pipeline used by `tix4-scraper/`.

## Pipeline

```
show_discovery -> determine_scraper -> provide_schedule_params_list ->
event_schedule -> provide_inventory_params_list -> event_inventory_main
```

- **show_discovery** — fetches the all-shows-events listing through Camoufox
  + Bright Data sticky-session ISP proxy (defeats Cloudflare). Filters the 299
  events down to ones whose listing card has a non-empty `.grid-card-h2` or
  `.promo-color` child (the user-visible red badges like "Save on tickets
  today" or "Celebrating 15 years on Broadway"). Saves
  `output/listing_<run_id>.html`.
- **determine_scraper** — fetches one event-detail page, classifies the SSR
  template (`ebg_v2/quantity_first` for Broadway-style seat-level, or
  `ebg_v2/view_event_datetime` for tier-level), extracts venue metadata and
  every (date, time) showtime. Saves `output/events/E<id>.html`.
- **provide_schedule_params_list** — fan-out helper that walks discovery
  output and runs determine_scraper for each show, returning the event list
  ready for inventory.
- **event_schedule** — cached schedule reader. Re-parses showtimes from the
  cached event HTML; falls back to a fresh fetch if the cache is missing.
- **provide_inventory_params_list** — flattens (event, showtime) pairs into
  inventory jobs, each with `qty=N` baked into the URL. Default `qty=6`.
- **event_inventory_main** — Stage 6 router. importlib-loads the dotted
  scraper module (`event_inventory.<scraper_type>.py`) and writes rows to
  `output/inventory_<run_id>.csv` plus run logs to
  `output/logs_<run_id>.json`.

## Output schema

15 standard columns shared with `tix4-scraper/` and `yapsody-scraper/`:

```
eventId | event_name | venue_name | event_datetime | objectLabelOrUuid |
section_name | row | seat_number | total_price | status | seat_pattern |
isRestrictedView | category | ticket_type | time_frame
```

Tier-level rows (`view_event_datetime` template) carry one extra column,
`total_stock`, holding the maximum buyable quantity exposed by the page.
Seat-level rows leave it blank. The router writes both shapes to the same
CSV; `total_stock` is appended only if at least one tier row contributed.

## POC

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
cp local_creds.py.example local_creds.py   # edit if you have your own Bright Data zone
```

## Production run

Per-stage testing (CLI):

```bash
uv run python test_local.py discover                                # Stage 1
uv run python test_local.py event 24885                             # Stage 2
uv run python test_local.py inventory 24885 2026-05-09 19:00:00 --qty 6   # Stage 6
uv run python test_local.py full 24885 --qty 6                      # Stages 2 -> 6
uv run python test_local.py run-all --max-shows 3 --qty 6           # Full pipeline
```

End-to-end pipeline:

```bash
uv run python run_pipeline.py --qty 6 --max-shows 3 --days-ahead 30
```

Outputs: `output/inventory_<run_id>.csv`, `output/logs_<run_id>.json`,
`output/listing_<run_id>.html`, `output/events/E<id>.html`.

## POC bake-off run

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
