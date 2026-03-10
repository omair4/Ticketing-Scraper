# Tixr Scraper

Scrapes ticket availability, pricing, and event listings from [tixr.com](https://www.tixr.com). Supports both general admission (GA) and reserved seating (seats.io) events.

## Quick Start

```bash
cd tixr_scraper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Scrape a Single Event

```bash
python main.py "https://www.tixr.com/groups/greenriverfestival/events/green-river-festival-2026-146792"
python main.py "https://www.tixr.com/groups/example/events/some-event-12345" --headless
python main.py "https://www.tixr.com/groups/example/events/some-event-12345" --format json csv -o ./output --debug
```

### Discover All Events (Catalog)

```bash
python discover.py                          # All groups
python discover.py --max-groups 3 --debug   # Test with 3 groups
python discover.py --headless -o events.json
```

### Orchestration (Bulk Scraping)

```bash
# Step 1: Discover events + initialize state DB
python orchestration/01_discover.py
python orchestration/01_discover.py --max-groups 5

# Step 2: Bulk scrape all discovered events
python orchestration/02_bulk_scraper.py
python orchestration/02_bulk_scraper.py --max-events 10 --concurrency 3

# Step 3: Refresh seatsio inventory (API-only, no browser)
python orchestration/03_refresh_inventory.py
python orchestration/03_refresh_inventory.py --concurrency 50
```

## Architecture

### Data Flow

```
main.py → BrowserAutomation (Playwright + DataDome handling)
  → navigate to event page
  → intercept /api/events/{id} via network listener
  → scenario detection from API response
    → get_scraper_for_scenario() factory (scrapers/__init__.py)
      → GeneralAdmissionScraper | SeatsIOScraper
    → scraper.extract() → ScrapeResult
  → exporters (JSON/CSV)
```

### Two Scraping Scenarios

| Scenario | Detection | Scraper | Data Source |
|----------|-----------|---------|------------|
| `general_admission` | `seatsIoConfiguration == null` | `GeneralAdmissionScraper` | Event API `sales[].tiers[]` |
| `seatsio` | `seatsIoConfiguration != null` | `SeatsIOScraper` | Event API + seats.io CDN |

Detection is automatic based on the `/api/events/{id}` response.

### Discovery Flow

```
discover.py / orchestration/01_discover.py
  → SitemapClient: parse sitemap.xml → group slugs (plain HTTP, no browser)
  → GroupCrawler (Playwright): navigate to each group page
    → intercept /api/groups/{id}/events → event list
  → deduplicate → save to JSON + initialize StateManager
```

### DataDome Bot Protection

Tixr uses DataDome which blocks direct HTTP API requests. All data must be captured via browser navigation + network interception.

**Mitigations:**
- Anti-detection init script (`navigator.webdriver = undefined`, `window.chrome = { runtime: {} }`)
- `--disable-blink-features=AutomationControlled` launch arg
- Conservative delays: 3-5s between events, 5s between groups
- Manual CAPTCHA fallback in headful mode (60s timeout)
- Cookie persistence across events in same browser session

**Exception:** seats.io CDN endpoints (`cdn-na/eu.seatsio.net`) have no DataDome — Script 3 (refresh) can hit them directly via `httpx`.

## Project Structure

```
tixr_scraper/
├── main.py                         # Single-event CLI entry point
├── browser.py                      # Playwright automation + DataDome handling
├── config.py                       # ScraperConfig dataclass + constants
├── models.py                       # Pydantic v2 data models
├── discover.py                     # Catalog discovery CLI entry point
├── requirements.txt
│
├── scrapers/
│   ├── __init__.py                 # get_scraper_for_scenario() factory
│   ├── base.py                     # BaseScraper ABC
│   ├── general_admission.py        # GA events — from /api/events/{id}
│   └── seatsio_scraper.py          # Reserved seating — seats.io integration
│
├── discovery/
│   ├── sitemap_client.py           # Parse sitemap.xml → group slugs
│   └── group_crawler.py            # Navigate groups → enumerate events via API
│
├── orchestration/
│   ├── 01_discover.py              # Script 1: sitemap + group crawl → state DB
│   ├── 02_bulk_scraper.py          # Script 2: concurrent event scraping
│   ├── 03_refresh_inventory.py     # Script 3: API-only seatsio refresh
│   ├── state_manager.py            # JSON file state tracking
│   ├── config.py                   # OrchestrationConfig
│   └── models.py                   # Orchestration data models
│
└── utils/
    ├── decoder.py                  # seats.io hex decode (shared with prekindle)
    └── exporters.py                # JSON/CSV export
```

## Output Format

### JSON Output

```json
{
  "event": {
    "event_id": "146792",
    "event_name": "Green River Festival 2026",
    "venue_name": "Greenfield Community College",
    "venue_address": "1 College Dr, Greenfield, MA, 01301",
    "venue_city": "Greenfield",
    "venue_state": "MA",
    "event_date": "2026-07-11T12:00:00Z",
    "event_url": "https://www.tixr.com/groups/greenriverfestival/events/...",
    "group_name": "Green River Festival",
    "event_category": "MusicEvent",
    "lineup": [
      {"name": "Artist Name", "type": "HEADLINER", "rank": 1}
    ]
  },
  "scenario_type": "general_admission",
  "ticket_categories": [
    {
      "category_name": "3-Day Pass - Early Bird",
      "category_id": "12345_67890",
      "price": 149.99,
      "total_price": 149.99,
      "is_out_of_stock": false,
      "ticket_type": "GA",
      "sale_state": "OPEN",
      "tier_name": "Early Bird",
      "max_tickets": 8,
      "delivery_type": "MOBILE"
    }
  ],
  "seats": [],
  "min_price": 149.99,
  "max_price": 299.99,
  "api_urls": {
    "event_api": "https://www.tixr.com/api/events/146792"
  }
}
```

### CSV Columns

| Column | Description |
|--------|-------------|
| `type` | `seat` or `ticket_category` |
| `category_name` | Ticket category name |
| `section`, `row`, `seat` | Seat location (seatsio only) |
| `x`, `y` | Chart coordinates (seatsio only) |
| `ADA` | Accessibility flag |
| `price`, `service_fee`, `total_price` | Pricing |
| `status` | `available`, `booked`, `held`, `out_of_stock` |
| `current_stock` | Available quantity (if known) |
| `sale_state` | Tixr state: `OPEN`, `QUEUED`, `SOLD_OUT`, `HIDE` |
| `tier_name` | Price tier name |
| `ticket_type` | Category: `GA`, `VIP`, `PARKING`, `CAMPING` |
| `delivery_type` | `MOBILE`, `WILL_CALL`, etc. |
| `event_name`, `venue`, `event_date` | Event metadata |

## Key Differences from PreKindle Scraper

| Aspect | PreKindle | Tixr |
|--------|-----------|------|
| **Bot protection** | Queue-IT (queue page) | DataDome (CAPTCHA) |
| **Pricing source (seats.io)** | HTML `pricing` param in seatsio init | Event API `sales[].tiers[]` |
| **Event discovery** | Search API (direct HTTP) | Sitemap + group page crawling (browser) |
| **Delivery methods** | Extracted from checkout page HTML | From API `sale.deliveryType` |
| **Stock/availability (GA)** | Dropdown counts from HTML | Sale state only (OPEN/SOLD_OUT) |
| **seats.io CDN region** | `cdn-na` | Check both `cdn-na` and `cdn-eu` |
| **Bulk concurrency** | 5 concurrent browsers | 3 concurrent (DataDome) |
| **Inter-event delay** | 3s | 5s (DataDome) |

## Configuration

### ScraperConfig (config.py)

| Setting | Default | Description |
|---------|---------|-------------|
| `headless` | `False` | Run browser headless (set `True` for servers) |
| `browser_timeout` | `30000` | Page load timeout (ms) |
| `datadome_wait` | `5000` | Extra wait for DataDome challenge (ms) |
| `inter_event_delay` | `3.0` | Seconds between events |
| `inter_group_delay` | `5.0` | Seconds between groups |
| `max_retries` | `3` | Max retries per event |

### OrchestrationConfig (orchestration/config.py)

| Setting | Default | Description |
|---------|---------|-------------|
| `max_concurrency` | `3` | Concurrent browser instances |
| `request_delay` | `5.0` | Seconds between events in bulk mode |
| `refresh_concurrency` | `50` | Concurrent API requests (seats.io refresh) |
| `refresh_delay` | `0.1` | Seconds between refresh requests |

## Tech Stack

- Python 3.9+
- Playwright (Chromium) — browser automation
- httpx — HTTP client (sitemaps, seats.io refresh)
- Pydantic v2 — data models and validation
- structlog — structured logging
- tenacity — retry logic
- BeautifulSoup4 — HTML parsing (fallback)
- lxml — XML parsing (sitemaps)

## Troubleshooting

### DataDome CAPTCHA

If you see "DataDome CAPTCHA detected" in logs:
1. Run in headful mode (default, no `--headless` flag)
2. Solve the CAPTCHA manually in the browser window (60s timeout)
3. Increase `datadome_wait` in config if challenges appear frequently

### No Event API Data

If the event API response isn't captured:
1. Check that the URL follows the pattern `/groups/{slug}/events/{name}-{id}`
2. Ensure the event ID at the end of the URL is correct
3. Run with `--debug` to see all captured network responses

### seats.io Data Missing

If seats.io data isn't captured for a reserved seating event:
1. The iframe may take longer to load — increase `page_load_delay`
2. Check captured responses for `rendering-info` and `object-statuses`
3. Run with `--debug` and check for decode failures
