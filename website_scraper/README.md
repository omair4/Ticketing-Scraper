# AdmitONE Website Scraper

Scrapes all events from admitone.com (Pro Events and Community Events).

## Overview

This project discovers and extracts event listings from the AdmitONE platform across all cities and categories. For ticket/seat availability scraping, see `../event_scraper/` as reference.

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

## GraphQL Schema Discovery

Before using direct GraphQL fetching, run the discovery script to capture schemas from the site:

```bash
# Option 1: Run the discovery script (Playwright)
python discovery/discover_graphql.py

# Option 2: Use Playwright MCP in Cursor to navigate to
# https://admitone.com/events/toronto and capture GraphQL network requests
```

This saves queries to `discovery/graphql_queries.json`. The scraper then uses these for direct GraphQL fetching (faster). If not discovered, it falls back to browser-based scraping.

## Usage

```bash
# Scrape all events (Canada-wide, default - fastest)
python main.py

# Scrape per city (slower, city-specific)
python main.py --per-city

# Scrape specific cities
python main.py --cities toronto calgary

# Output options
python main.py --output-dir ./results --format json csv

# Headless mode
python main.py --headless

# Filter by event type
python main.py --event-type pro
```

## Structure

```
website_scraper/
├── main.py            # CLI entry point
├── config.py          # Configuration
├── models.py          # Pydantic models (EventSummary, EventsScrapeResult)
├── browser.py         # Playwright browser automation
├── discovery/         # Event discovery (GraphQL, browser crawling)
├── utils/             # Export utilities
├── docs/              # Implementation plan
└── output/            # Scraped data output
```

## Reference

- **event_scraper/**: Ticket/seat availability scrapers (seats.io, direct API, etc.)
