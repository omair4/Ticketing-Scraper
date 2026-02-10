# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AdmitONE Scraper — a Python system for scraping ticket/seat availability and event listings from admitone.com. Two independent modules with separate virtual environments:

- **`event_scraper/`** — Scrapes ticket availability for a single event (seats, prices, categories). Runs as CLI or AWS Lambda.
- **`website_scraper/`** — Discovers and lists all events across cities/categories from admitone.com.

## Setup & Commands

Each module has its own `.venv` and `requirements.txt`. Always `cd` into the module directory first.

```bash
# Event scraper setup
cd event_scraper
pip install -r requirements.txt
playwright install chromium

# Website scraper setup
cd website_scraper
pip install -r requirements.txt
playwright install chromium
```

### Running

```bash
# Event scraper — scrape one event
cd event_scraper
python main.py "<event_url>"
python main.py "<event_url>" --headless --debug --format json csv -o ./output

# Website scraper — discover all events
cd website_scraper
python main.py
python main.py --per-city --cities toronto calgary --event-type pro --headless
```

### Docker / Lambda

```bash
cd event_scraper
docker build -t admitone-scraper:latest .
sam build && sam deploy --guided
```

### Testing

No formal test suite. Manual testing via CLI. Installation check: `python test_installation.py` in `event_scraper/`.

## Architecture

### Event Scraper Data Flow

```
main.py → BrowserAutomation (Playwright, async context manager)
  → navigate to event, click through purchase flow
  → capture API responses via network listener (_on_response)
  → scenario detection based on captured URLs
    → get_scraper_for_scenario() factory (scrapers/__init__.py)
      → SeatsIOScraper | DirectAPIScraper | GraphQLScraper
        | EmbedWidgetScraper | ExternalRedirectScraper
    → scraper.extract() → ScrapeResult
  → exporters (JSON/CSV)
```

### Five Scraping Scenarios (auto-detected)

| Scenario | Trigger URL pattern | Scraper |
|----------|-------------------|---------|
| `seatsio` | `cdn-eu.seatsio.net` or `embed.admitonelive.com` | `SeatsIOScraper` — hex-decoded seat maps + pricing |
| `direct_api` | `tickets.admitone.com` | `DirectAPIScraper` — REST API for seats/categories |
| `graphql` | `graphql.admitone.com` | `GraphQLScraper` — GraphQL OrderSummaryEvent query |
| `embed_widget` | `embed.admitonelive.com/event/` | `EmbedWidgetScraper` — HTML table + JS variable parsing |
| `external_redirect` | Non-AdmitONE domain redirect | `ExternalRedirectScraper` — third-party vendor links |

### Website Scraper Data Flow

```
main.py → GraphQLEventsScraper (fast, direct API) or BrowserEventsScraper (fallback)
  → EventsScrapeResult with List[EventSummary]
```

GraphQL queries are captured via `discovery/discover_graphql.py` and saved to `discovery/graphql_queries.json`. The scraper uses these for direct fetching; falls back to browser crawling if unavailable.

### Key Design Patterns

- **Strategy + Factory**: `get_scraper_for_scenario()` selects scraper class by scenario string
- **Async context manager**: `BrowserAutomation.__aenter__/__aexit__` manages Playwright lifecycle
- **All scrapers extend `BaseScraper`** with an async `extract()` method
- **Pydantic v2 models** (`models.py`): `ScrapeResult`, `EventInfo`, `Seat`, `TicketCategory`, `ResaleListing`, `CapturedResponse`
- **Dataclass config** (`config.py`): `ScraperConfig` with browser, network, retry, and output settings

### Hex Decoding (utils/decoder.py)

seats.io returns hex-encoded JSON with a byte offset. `_bruteforce_offset_decode()` tries all 256 offsets, with smart prediction for offsets yielding `{` or `[`. Handles base64+hex, direct hex, and plain JSON.

## Key Files

- `event_scraper/browser.py` — Playwright automation, response capture, scenario detection, anti-bot measures
- `event_scraper/models.py` — All Pydantic data models with price parsing and validation
- `event_scraper/config.py` — `ScraperConfig` dataclass, `APIPatterns`, `BUTTON_SELECTORS`, `ADMITONE_DOMAINS`
- `event_scraper/lambda_handler.py` — AWS Lambda entry point (input: `eventId`, `ticket_url`, etc.)
- `website_scraper/discovery/graphql_client.py` — Direct GraphQL API client
- `website_scraper/discovery/cities.py` / `categories.py` — City and category mapping constants

## Tech Stack

Python 3.9–3.13, Playwright (Chromium), httpx, Pydantic v2, structlog, tenacity, BeautifulSoup4. AWS Lambda deployment via Docker + SAM.

## Conventions

- All scraping is async (`asyncio`). Entry points use `asyncio.run()`.
- Logging via `structlog` — ConsoleRenderer for CLI, JSONRenderer for Lambda.
- Exports go to `output/` subdirectories with timestamp-based filenames.
- Prices are `Decimal` in models, converted to `float` on export.
- `config.py` defines `ADMITONE_DOMAINS` — used to distinguish internal vs external redirects.
- Lambda processes one event per invocation; use concurrent Lambda executions for bulk scraping.
