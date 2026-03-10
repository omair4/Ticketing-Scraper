# Tixr Scraper — Implementation Details

Technical documentation covering the implementation of each component, data flow internals, and design decisions.

## Table of Contents

1. [Browser Automation & DataDome](#browser-automation--datadome)
2. [Event API Response Structure](#event-api-response-structure)
3. [General Admission Scraper](#general-admission-scraper)
4. [seats.io Scraper](#seatsio-scraper)
5. [Catalog Discovery](#catalog-discovery)
6. [Orchestration Pipeline](#orchestration-pipeline)
7. [Data Models](#data-models)
8. [Reused Components](#reused-components)
9. [Known Limitations](#known-limitations)

---

## Browser Automation & DataDome

### `browser.py` — BrowserAutomation

The browser automation layer handles Playwright lifecycle, DataDome bot protection, and network response interception.

#### Anti-Detection Measures

```python
# Launch args to disable automation flags
args=[
    '--disable-blink-features=AutomationControlled',
    '--disable-dev-shm-usage',
    '--no-sandbox',
]

# Init script to remove webdriver fingerprint
await page.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = { runtime: {} };
""")
```

These measures prevent DataDome from detecting Playwright as an automation tool. Without them, DataDome blocks requests immediately.

#### Response Interception

All data extraction is done via network interception, not direct HTTP requests. DataDome validates that API requests originate from a legitimate browser navigation context.

```python
self.page.on('response', self._on_response)
```

The `_on_response` handler captures responses matching these patterns:
- `/api/events/` — primary event data (JSON)
- `/api/groups/` — group events listing (JSON)
- `/api/page/requirements` — page metadata (JSON)
- `seatsio.net` — seats.io CDN endpoints (hex-encoded or JSON)
- `object-statuses` — seat availability
- `rendering-info` — category definitions
- `charts/published` — chart layout

#### DataDome CAPTCHA Handling

```python
async def _is_datadome_blocked(self) -> bool:
    content = await self.page.content()
    indicators = ['geo.captcha-delivery.com', 'datadome', 'captcha-delivery']
    return any(indicator in content.lower() for indicator in indicators)
```

When a CAPTCHA is detected:
1. Log a warning
2. Wait up to 60 seconds for manual resolution (headful mode)
3. Fail gracefully if timeout expires

#### Navigation Flow — Single Event

```
navigate_to_event(event_url)
  1. page.goto(event_url, wait_until='domcontentloaded')
  2. Wait for load state + DataDome (5s)
  3. Check for DataDome CAPTCHA → manual fallback
  4. Extract event API data from captured /api/events/{id}
  5. If seatsio: wait for seats.io iframe + CDN responses (15s)
  6. Capture page HTML
  7. Detect scenario from API data
  → NavigationResult
```

#### Navigation Flow — Group Page

```
navigate_to_group(group_url)
  1. page.goto(group_url)
  2. Wait for load + DataDome
  3. Check CAPTCHA
  4. Return captured responses (group events API)
  → NavigationResult
```

---

## Event API Response Structure

The primary data source is the Tixr event API: `/api/events/{eventId}`. This single response contains everything needed for GA events.

### Key Fields

```json
{
  "id": 146792,
  "name": "Green River Festival 2026",
  "status": "PUBLISHED",
  "startDate": "2026-07-11T12:00:00Z",
  "endDate": "2026-07-13T22:00:00Z",
  "doorsOpenDate": "2026-07-11T11:00:00Z",
  "eventCategoryName": "MusicEvent",

  "venue": {
    "id": 12345,
    "name": "Greenfield Community College",
    "capacity": 5000,
    "seatingType": "GENERAL_ADMISSION",
    "timezone": "America/New_York",
    "address": {
      "address1": "1 College Dr",
      "city": "Greenfield",
      "state": "MA",
      "zipCode": "01301",
      "country": "US",
      "latitude": 42.5876,
      "longitude": -72.5988
    }
  },

  "group": {
    "id": 67890,
    "name": "Green River Festival",
    "subdomain": "greenriverfestival"
  },

  "sales": [
    {
      "id": 111,
      "name": "3-Day Pass",
      "category": "GA",
      "state": "OPEN",
      "maxTicketsAllowed": 8,
      "deliveryType": "MOBILE",
      "seatingType": "GENERAL_ADMISSION",
      "resaleState": "DISABLED",
      "tiers": [
        {
          "id": 222,
          "name": "Early Bird",
          "price": 149.99,
          "active": true,
          "startDate": "2026-01-01T00:00:00Z",
          "endDate": "2026-03-01T00:00:00Z"
        },
        {
          "id": 223,
          "name": "Regular",
          "price": 199.99,
          "active": false,
          "startDate": "2026-03-01T00:00:00Z",
          "endDate": "2026-07-11T00:00:00Z"
        }
      ]
    },
    {
      "id": 112,
      "name": "VIP Pass",
      "category": "VIP",
      "state": "SOLD_OUT",
      "tiers": [{"id": 224, "name": "VIP", "price": 499.99, "active": true}]
    }
  ],

  "seatsIoConfiguration": null,

  "lineups": [
    {
      "acts": [
        {
          "artist": {"name": "Artist Name", "imageUrl": "https://..."},
          "type": "HEADLINER",
          "rank": 1
        }
      ]
    }
  ]
}
```

### Sale States

| State | Meaning |
|-------|---------|
| `OPEN` | Tickets available for purchase |
| `QUEUED` | Tickets available but behind a queue |
| `SOLD_OUT` | All tickets sold |
| `HIDE` | Hidden from public view |
| `CLOSED` | Sale period ended |

### Tier Activation

Each sale can have multiple price tiers (e.g., Early Bird → Regular → Door). Only one tier is typically `active` at a time. The scraper captures all active tiers, falling back to all tiers if none are marked active.

---

## General Admission Scraper

### `scrapers/general_admission.py` — GeneralAdmissionScraper

The simplest scraper — extracts ticket categories directly from the event API `sales[]` array.

#### Extraction Logic

```
For each sale in event_data['sales']:
    For each active tier in sale['tiers']:
        Create TicketCategory:
            category_name = "{sale.name} - {tier.name}" (or just sale.name)
            category_id = "{sale.id}_{tier.id}"
            price = tier.price (all-in, includes fees)
            is_out_of_stock = sale.state in (SOLD_OUT, HIDE, CLOSED)
            sale_state = sale.state
            ticket_type = sale.category (GA, VIP, PARKING, CAMPING)
            delivery_type = sale.deliveryType
            max_tickets = sale.maxTicketsAllowed
```

#### Key Points

- **Price is all-in**: Tixr tier prices include service fees (unlike PreKindle which has separate base + fee)
- **No stock counts**: Tixr API only exposes sale state, not remaining quantity. We capture `is_out_of_stock` but cannot provide `current_stock`
- **Tier naming**: If the tier name differs from the sale name, we combine them (e.g., "3-Day Pass - Early Bird"). If they match, we use just the sale name

---

## seats.io Scraper

### `scrapers/seatsio_scraper.py` — SeatsIOScraper

For events with `seatsIoConfiguration != null`. Combines data from 4 sources:

1. **Event API** (`/api/events/{id}`) — pricing via `sales[].tiers[].price`
2. **rendering-info** (plain JSON) — category definitions, seat→category mappings
3. **charts/published** (hex-encoded) — visual layout, coordinates, ADA flags
4. **object-statuses** (hex-encoded) — per-seat availability

#### Price Mapping (Key Difference from PreKindle)

PreKindle gets pricing from HTML `pricing` param in the seatsio.SeatingChart initialization script. Tixr gets pricing from the event API.

```python
def _extract_pricing_from_api(self, event_data):
    """Map sale_name → {price, tier_name, sale_name, category}"""
    for sale in event_data.get('sales', []):
        for tier in active_tiers:
            pricing[sale_name] = {
                'price': Decimal(str(tier['price'])),
                'tier_name': tier['name'],
                'sale_name': sale['name'],
            }
    return pricing
```

The pricing dict is keyed by sale name, which should match the category labels in seats.io rendering-info.

#### Price Lookup Strategy

When assigning a price to a seat, the scraper tries these strategies in order:

1. **Direct label match**: `category_label` found as key in pricing dict
2. **Category map label**: chart category key → map to label → lookup in pricing
3. **Case-insensitive match**: lowercase comparison
4. **First available**: use the first price in the dict as fallback

#### Seat Building Pipeline

```
1. Extract rendering-info → category definitions, objectCategories mappings
2. Decode charts/published → rows[], sections[], GA areas, tables
3. Decode object-statuses → per-seat availability (sparse: missing = available)
4. Build seats:
   For each row in chart:
     For each seat in row:
       - Build composite label: "Section-Row-Seat"
       - Skip if in excluded_objects (not for sale)
       - Lookup category from rendering-info.objectCategories
       - Lookup status from object-statuses (default: available)
       - Lookup price via category label
       - Create Seat model
5. Build ticket_categories from aggregated category counts
6. Add GA areas as TicketCategory entries
```

#### Fallback Paths

If the primary chart→seats extraction fails:

1. **`_build_seats_from_object_statuses`**: Parse labels from object-statuses keys, look up categories from rendering-info
2. **`_build_seats_from_availability`**: Last resort — parse seat labels, try partial price matching

#### Hex Decoding

seats.io CDN returns hex-encoded JSON with a byte offset. The `utils/decoder.py` module handles this:

```python
# Smart prediction: try offsets that make first byte = '{' or '['
priority_offsets = [
    (first_byte - 0x7B) % 256,  # '{' = 0x7B
    (first_byte - 0x5B) % 256,  # '[' = 0x5B
]
# Then brute-force remaining 254 offsets
```

**Note**: `rendering-info` is plain JSON (no encoding), while `charts/published` and `object-statuses` are hex-encoded.

---

## Catalog Discovery

### Step 1: Sitemap Parsing (`discovery/sitemap_client.py`)

**No browser needed** — tixr.com sitemaps are accessible via plain HTTP.

```
1. Fetch https://www.tixr.com/sitemap.xml → sitemap index
2. Find group-sitemap-*.xml URLs
3. For each group sitemap:
   - Parse XML
   - Extract URLs matching /groups/{slug}
   - Return slug part
4. Deduplicate → List[str] of ~80+ group slugs
```

Uses `httpx` with `tenacity` retry (3 attempts, exponential backoff).

### Step 2: Group → Events Crawling (`discovery/group_crawler.py`)

**Browser required** — DataDome blocks direct API calls to group events.

```
For each group_slug:
  1. Navigate to https://www.tixr.com/groups/{slug}
  2. Wait for DataDome + page load
  3. Capture /api/page/requirements → extract groupId
  4. Capture /api/groups/{groupId}/events → event list JSON
  5. Parse events → List[TixrEventSummary]
  6. Delay 5s before next group
```

The crawler processes groups sequentially (not concurrently) to minimize DataDome detection risk.

#### Event Summary Fields

```python
TixrEventSummary:
    event_id: int          # 146792
    event_name: str        # "Green River Festival 2026"
    group_id: int          # 67890
    group_name: str        # "Green River Festival"
    group_subdomain: str   # "greenriverfestival"
    venue_name: str
    venue_city: str
    venue_state: str
    start_date: str        # ISO datetime
    event_url: str         # Full URL
    has_seatsio: bool      # seatsIoConfiguration is not null
    sales_count: int       # Number of sale types
    is_sold_out: bool      # All sales in terminal state
```

---

## Orchestration Pipeline

Three scripts for end-to-end bulk scraping, matching the PreKindle orchestration pattern.

### Script 1: `01_discover.py`

```
1. Parse sitemaps → group slugs
2. Launch Playwright, crawl each group page
3. Intercept group events API → collect all events
4. Deduplicate by event_id
5. Save discovered_events.json
6. Initialize StateManager (scraping.json)
```

### Script 2: `02_bulk_scraper.py`

```
1. Read pending events from StateManager
2. For each event (behind asyncio.Semaphore(3)):
   a. Mark in_progress
   b. Call main.scrape_event(event_url)
   c. Export to output/bulk_scrape_{timestamp}/by_scenario/{type}/
   d. Mark completed or failed in state
   e. Delay 5s
3. Print summary statistics
```

Concurrency is lower than PreKindle (3 vs 5) because DataDome is more aggressive than Queue-IT.

### Script 3: `03_refresh_inventory.py`

For seatsio events only — re-fetches seats.io CDN endpoints directly (no browser needed, no DataDome on CDN).

```
1. Find previous scrape JSON results
2. For each seatsio result:
   a. Extract saved API URLs (rendering-info, chart, object-statuses)
   b. Fetch fresh data via httpx (concurrent, 50 at a time)
   c. Re-run SeatsIOScraper extraction pipeline
   d. Export refreshed result
3. Print summary
```

### State Manager

JSON file at `orchestration/state/scraping.json` tracks each event:

```json
{
  "events": {
    "146792": {
      "event_id": "146792",
      "event_url": "https://...",
      "event_name": "Green River Festival 2026",
      "status": "pending",       // pending → in_progress → completed/failed
      "scenario_type": null,     // set on completion
      "scraped_at": null,
      "output_json_path": null,
      "error_message": null,
      "retries": 0
    }
  }
}
```

---

## Data Models

### `models.py` — Pydantic v2

All models use Pydantic v2 with `Decimal` for prices (converted to `float` on export).

| Model | Purpose |
|-------|---------|
| `SeatStatus` | Enum: available, booked, held, reserved, unknown |
| `SaleState` | Enum: OPEN, QUEUED, SOLD_OUT, HIDE, CLOSED |
| `Seat` | Individual seat with section/row/seat, price, status, coordinates |
| `TicketCategory` | GA ticket with price, state, tier, delivery info |
| `EventInfo` | Event metadata: name, venue, dates, group, lineup |
| `ScrapeResult` | Top-level result with seats[], categories[], pricing, API URLs |
| `TixrEventSummary` | Catalog entry from discovery |

### Price Handling

- Tixr prices are **all-in** (fees included in `tier.price`)
- Stored as `Decimal` internally, exported as `float`
- `total_price` computed field adds `service_fee` if present (for compatibility)
- Validators handle `$`, `,`, `USD` stripping

---

## Reused Components

### From `prekindle_scraper/`

| File | Reuse Type | Notes |
|------|------------|-------|
| `utils/decoder.py` | Direct copy | Identical seats.io hex decoding logic |
| `scrapers/base.py` | Adapted | Same ABC pattern, different extract() signature (event_api_data vs event_info_override) |
| `utils/exporters.py` | Adapted | Added Tixr-specific CSV columns (sale_state, tier_name, delivery_type) |
| `orchestration/state_manager.py` | Adapted | Tixr event fields (event_url vs ticket_url, group_name) |
| `orchestration/config.py` | Adapted | Lower concurrency defaults, longer delays |
| `models.py` | New (Pydantic patterns reused) | Added SaleState, TixrEventSummary, venue details |

### Architecture Patterns

- **Strategy + Factory**: `get_scraper_for_scenario()` selects scraper class
- **Async context manager**: `BrowserAutomation.__aenter__/__aexit__`
- **All scrapers extend BaseScraper** with async `extract()` method
- **structlog** for logging, **tenacity** for retries
- **JSON state file** for orchestration progress tracking

---

## Known Limitations

### No Stock Counts for GA Events

The Tixr API does not expose remaining ticket quantity. We can only capture sale state (`OPEN`, `SOLD_OUT`, etc.), not how many tickets remain.

### DataDome Rate Limiting

Aggressive scraping will trigger DataDome escalation (CAPTCHA → IP block). Mitigations:
- Conservative delays (5s between events, 5s between groups)
- Low concurrency (3 browsers max)
- Headful mode for CAPTCHA solving
- Consider proxy rotation for large-scale scraping

### Group Events API Not Always Auto-Triggered

Some group pages may not automatically call `/api/groups/{id}/events`. The current implementation relies on this being intercepted from the page load. If the group page uses lazy loading or pagination, some events may be missed.

### seats.io CDN Region

Tixr may use either `cdn-na.seatsio.net` or `cdn-eu.seatsio.net`. The response interception captures both patterns. For refresh (Script 3), the saved URL preserves the correct region.

### Price Mapping Accuracy (seats.io)

The price-to-seat mapping relies on matching Tixr sale names to seats.io category labels. If these don't align exactly, the fallback strategies (case-insensitive, partial match) may assign incorrect prices. Check logs for "pricing not found" warnings.
