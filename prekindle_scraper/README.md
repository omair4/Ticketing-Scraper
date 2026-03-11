# PreKindle Scraper

Scrapes ticket availability and event listings from prekindle.com. Independent module with its own virtual environment.

## Setup

```bash
cd prekindle_scraper
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Usage

### Single Event Scraping

```bash
# SeatsIO event (seated venue with seats.io integration)
python main.py "https://www.prekindle.com/promo/id/-2852852648567343600?sc=-2852852648567343600"

# General Admission event (HTML table tickets)
python main.py "https://www.prekindle.com/promo/id/-2853111880284254220?sc=-2853111880284254220"

# With options
python main.py "<promo_url>" --headless --debug --format json csv -o ./output
```

### Event Discovery

```bash
python discover.py                          # Fetch all events
python discover.py --limit 50              # 50 results per page
python discover.py --max-pages 1           # First page only
python discover.py --format json csv -o ./output
```

## Architecture

### Data Flow

```
main.py → BrowserAutomation (Playwright, async context manager)
  → navigate to promo page, extract event info
  → click GET TICKETS → pass Queue-IT → reach ticket page
  → scenario detection based on page content + captured URLs
    → get_scraper_for_scenario() factory (scrapers/__init__.py)
      → SeatsIOScraper | GeneralAdmissionScraper | SvgSeatmapScraper
    → scraper.extract() → ScrapeResult
  → exporters (JSON/CSV)
```

### Three Scraping Scenarios (auto-detected)

| Scenario | Detection | Scraper |
|----------|-----------|---------|
| `seatsio` | `seatsio.net` in captured responses or iframe in page | `SeatsIOScraper` — pricing from page HTML, seats from hex-decoded API |
| `general_admission` | HTML table with `<select>` dropdowns, `Total Price` text | `GeneralAdmissionScraper` — parses ticket table from server-rendered HTML |
| `svg_seatmap` | SVG elements with `wicketpath` attributes | `SvgSeatmapScraper` — **not supported** (returns error) |

### Navigation Flow

```
1. Promo page (/promo/id/{eventId})     ← extract event info here
2. Click "GET TICKETS"
3. Queue-IT (q.prekindle.com)            ← bot protection, auto-handled
4. Ticket page (/find-tickets or /checkout)
5. Detect scenario → run scraper
```

Event info (title, date, venue, age limit) is extracted from the promo page **before** clicking GET TICKETS, since the checkout page has minimal metadata.

### Key Differences from AdmitONE Event Scraper

| Aspect | AdmitONE | PreKindle |
|--------|----------|-----------|
| Pricing source | `embed.admitonelive.com` API | Page HTML (`pricing` param in seatsio init) |
| seats.io CDN | `cdn-eu.seatsio.net` | `cdn-na.seatsio.net` |
| Rendering | Client-side (React/APIs) | Server-side (Apache Wicket) |
| Bot protection | None | Queue-IT |
| Event IDs | MongoDB ObjectId (24 hex chars) | Signed 64-bit integers |
| Event info | From API responses | From promo page HTML |

## File Structure

```
prekindle_scraper/
├── main.py                              # CLI: single event scraping
├── discover.py                          # CLI: event discovery
├── config.py                            # ScraperConfig, URL patterns, selectors
├── models.py                            # Pydantic models (Seat, TicketCategory, EventInfo, ScrapeResult, PreKindleEventSummary)
├── browser.py                           # Playwright automation + scenario detection
├── scrapers/
│   ├── __init__.py                      # Factory: get_scraper_for_scenario()
│   ├── base.py                          # BaseScraper ABC
│   ├── seatsio_scraper.py               # Scenario 1: seats.io
│   ├── general_admission_scraper.py     # Scenario 2: HTML table
│   └── svg_seatmap_scraper.py           # Scenario 3: unsupported stub
├── utils/
│   ├── decoder.py                       # seats.io hex/base64 decoding
│   └── exporters.py                     # JSON/CSV export
├── discovery/
│   └── event_search.py                  # PreKindle search API client
├── output/                              # Results directory
├── requirements.txt
├── FINDINGS.md                          # Technical investigation notes
└── links.txt                            # Test event URLs
```

## Output

### JSON (single event)

Includes standard fields plus PreKindle-specific extensions:

- `api_urls` — discovered seats.io API URLs for direct re-querying without a browser
- `scrape_method` — `"browser"` or `"html_parse"`
- `delivery_methods` — available delivery options (Print at Home, Mobile, etc.)

### CSV (single event)

Unified format with a `type` column (`seat` or `ticket_category`).

### JSON (discovery)

List of all events with title, start time, venue, ticket URL, sold-out status.

## Adding New Scenarios

1. Create `scrapers/new_scenario_scraper.py` extending `BaseScraper`
2. Implement `extract()` and `get_scenario_name()`
3. Add detection logic in `browser.py` → `_detect_scenario()`
4. Register in `scrapers/__init__.py` factory dict

## Tech Stack

Python 3.9+, Playwright (Chromium), httpx, Pydantic v2, structlog, tenacity, BeautifulSoup4.
