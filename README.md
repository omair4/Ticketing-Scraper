# AdmitONE Ticket Scraper

A Python scraper for extracting seat availability data from AdmitONE event pages. Handles multiple API scenarios including seats.io integrations and direct ticket APIs.

## Features

- **Multi-step navigation**: Automatically clicks through "Buy Now" and "Select Seats" flows
- **Scenario auto-detection**: Identifies whether the event uses seats.io or direct API
- **Complete data extraction**: Gets section, row, seat number, and price for all seats
- **Multiple output formats**: Exports to JSON and CSV
- **Hex decoding**: Handles obfuscated seats.io API responses

## Installation

```bash
# Clone or download the scraper
cd admitone_scraper

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

## Usage

### Basic Usage

```bash
python main.py "https://admitone.com/events/your-event-url"
```

### Options

```bash
python main.py <url> [options]

Options:
  --output-dir, -o    Output directory (default: ./output)
  --format, -f        Output format(s): json, csv, or both (default: json csv)
  --headless          Run browser in headless mode (no visible window)
  --debug             Enable debug logging
```

### Examples

```bash
# Scenario 1: seats.io event (Andrew Callaghan)
python3.13 main.py "https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/andrew-callaghan/andrew-callaghan/681d11117d3a4df5fd2d2c24"

# Scenario 2: Direct API event (Byun Jin Sub)
python main.py "https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/artist/byun-jin-sub-wish-list/694ee2570472d0f8bde99b4e"

# JSON output only
python main.py "https://admitone.com/events/..." --format json

# Headless mode with custom output directory
python main.py "https://admitone.com/events/..." --headless --output-dir ./results
```

## Output Format

### JSON Output

```json
{
  "event": {
    "event_id": "10031723",
    "event_name": "Andrew Callaghan: All Gas No Brakes",
    "venue_name": "Queen Elizabeth Theatre",
    "venue_address": "190 Princes' Blvd, Toronto ON M6K 3C3",
    "event_date": "Saturday, February 14, 2026",
    "event_url": "https://admitone.com/events/..."
  },
  "scraped_at": "2026-01-22T10:30:00Z",
  "scenario_type": "seatsio",
  "total_seats_found": 1224,
  "available_seats_count": 1150,
  "min_price": 39.50,
  "max_price": 199.00,
  "seats": [
    {
      "section": "Orchestra 1",
      "row": "A",
      "seat": "1",
      "price": 59.50,
      "service_fee": 16.75,
      "total_price": 76.25,
      "status": "available",
      "price_level": "Orchestra 1"
    }
  ]
}
```

### CSV Output

| section | row | seat | price | service_fee | total_price | status | price_level | event_name | venue | event_date |
|---------|-----|------|-------|-------------|-------------|--------|-------------|------------|-------|------------|
| Orchestra 1 | A | 1 | 59.50 | 16.75 | 76.25 | available | Orchestra 1 | Andrew Callaghan | Queen Elizabeth Theatre | Feb 14, 2026 |

## Architecture

```
admitone_scraper/
├── main.py                 # CLI entry point
├── config.py               # Configuration settings
├── models.py               # Pydantic data models
├── browser.py              # Playwright browser automation
├── scrapers/
│   ├── base.py             # Abstract base scraper
│   ├── seatsio_scraper.py  # Scenario 1: seats.io
│   └── direct_api_scraper.py  # Scenario 2: Direct API
└── utils/
    ├── decoder.py          # Hex decoding utilities
    └── exporters.py        # JSON/CSV export
```

## Supported Scenarios

### Scenario 1: seats.io Integration

**Detected by**: URLs containing `seatsio.net` or `embed.admitonelive.com`

**Navigation flow**:
1. Event page → Click "Buy Now"
2. Modal appears → Click "Select Seats"
3. seats.io map loads

**APIs used**:
- `cdn-eu.seatsio.net/.../object-statuses` (hex-encoded seat data)
- `embed.admitonelive.com/api/events/seated/{id}` (pricing)

### Scenario 2: Direct Ticket API

**Detected by**: URLs containing `tickets.admitone.com`

**Navigation flow**:
1. Event page → Click "Buy Now"
2. Redirect to tickets.admitone.com
3. Click "Buy Now" again
4. Seating plan loads

**APIs used**:
- `tickets.admitone.com/events/{id}/seating-plan/availability`
- `tickets.admitone.com/events/{id}` (event data with price bands)

## Troubleshooting

### No seats found

1. **Check if the event page loads correctly**: Run without `--headless` to see the browser
2. **Check API responses**: Enable `--debug` to see captured API endpoints
3. **Verify the URL**: Make sure you're using the full event URL

### Prices showing as $0

This usually means the pricing API response wasn't captured or parsed correctly:
1. The pricing endpoint may require additional API calls
2. Check the debug logs for pricing-related errors

### Browser automation issues

- Make sure Playwright browsers are installed: `playwright install chromium`
- Try running without `--headless` to see what's happening
- Check if the site structure has changed

## Extending

To add support for a new API scenario:

1. Create a new scraper class in `scrapers/`:
```python
from scrapers.base import BaseScraper

class NewScenarioScraper(BaseScraper):
    def get_scenario_name(self) -> str:
        return "new_scenario"
    
    async def extract(self, captured_responses, page_html, event_url):
        # Implementation here
        pass
```

2. Register in `scrapers/__init__.py`
3. Update scenario detection in `browser.py`

## Legal Notice

This scraper is provided for educational and personal use only. Please:
- Respect the website's Terms of Service
- Don't overload servers with excessive requests
- Consider reaching out to AdmitONE for official API access for commercial use
