# AdmitONE Ticket Scraper

A Python scraper for extracting ticket and seat availability data from AdmitONE event pages. Automatically handles multiple API scenarios including seats.io integrations, direct ticket APIs, GraphQL endpoints, and embedded widgets.

## Features

- **Multi-scenario support**: Automatically detects and handles 4 different ticket system types
- **Smart navigation**: Automatically clicks through "Buy Now" and "Select Seats" flows
- **Complete data extraction**: Gets section, row, seat number, price, and availability for all tickets
- **Multiple output formats**: Exports to JSON and CSV
- **Robust parsing**: Handles hex-encoded responses, JavaScript variables, and complex HTML structures

## Requirements

- **Python**: 3.9, 3.10, 3.11, 3.12, or 3.13 (Python 3.8+ and < 3.14)
- **Playwright**: Chromium browser (installed automatically)

## Installation

```bash
# Clone or download the scraper
cd admitone_scraper

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

## Quick Start

```bash
# Basic usage - just provide the event URL
python main.py "https://admitone.com/events/your-event-url"

# Run in headless mode (no browser window)
python main.py "https://admitone.com/events/your-event-url" --headless

# Custom output directory
python main.py "https://admitone.com/events/your-event-url" --output-dir ./results
```

## Command Line Options

```bash
python main.py <url> [options]

Options:
  --output-dir, -o    Output directory (default: ./output)
  --format, -f        Output format(s): json, csv, or both (default: json csv)
  --headless          Run browser in headless mode (no visible window)
  --debug             Enable debug logging
```

## Examples

### Scenario 1: SeatsIO Integration
Events with interactive seat maps using seats.io:
```bash
python main.py "https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/andrew-callaghan/andrew-callaghan/681d11117d3a4df5fd2d2c24"
```

### Scenario 2: Direct Ticket API
Events with direct API endpoints for seat availability:
```bash
# Events with individual seats
python main.py "https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/artist/byun-jin-sub-wish-list/694ee2570472d0f8bde99b4e"

# Events with ticket categories (no individual seats)
python main.py "https://admitone.com/events/winnipeg/pro/concerts/the-park-theatre/black-pistol-fire/black-pistol-fire/68c1dfdcf293219b233e8f6b"
```

### Scenario 3: GraphQL API
Events using GraphQL for ticket information:
```bash
python main.py "https://admitone.com/events/toronto/community/comedy/i-know-thi-one/8TB4NT"
```

### Scenario 4: Embed Widget
Events with ticket data embedded in HTML:
```bash
python main.py "https://admitone.com/events/calgary/pro/concerts/the-palace-theatre/jade-eagleson/jade-eagleson/689e0f85857db55262f707c3"
```

## Output Format

Results are saved to the `output/` directory (or your custom directory) with filenames based on the event name and timestamp.

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
  ],
  "ticket_categories": [
    {
      "category_id": "12345",
      "category_name": "General Admission",
      "base_price": 30.00,
      "service_fee": 11.00,
      "total_price": 41.00,
      "current_stock": 150,
      "status": "available"
    }
  ]
}
```

### CSV Output

For events with individual seats, CSV includes one row per seat:
| section | row | seat | price | service_fee | total_price | status | price_level | event_name | venue | event_date |
|---------|-----|------|-------|-------------|-------------|--------|-------------|------------|-------|------------|
| Orchestra 1 | A | 1 | 59.50 | 16.75 | 76.25 | available | Orchestra 1 | Andrew Callaghan | Queen Elizabeth Theatre | Feb 14, 2026 |

For events with ticket categories, CSV includes one row per category:
| category_name | base_price | service_fee | total_price | current_stock | status | event_name | venue | event_date |
|---------------|------------|-------------|-------------|---------------|-------|------------|-------|------------|
| General Admission | 30.00 | 11.00 | 41.00 | 150 | available | Jade Eagleson | The Palace Theatre | Jan 28, 2026 |

## Supported Scenarios

The scraper automatically detects which scenario to use based on the event's API structure. You don't need to specify this manually.

### Scenario 1: SeatsIO Integration

**What it is**: Events with interactive seat maps powered by seats.io

**How it works**:
1. Event page → Click "Buy Now"
2. Modal appears → Click "Select Seats"
3. seats.io interactive map loads
4. Scraper captures seat availability (hex-encoded) and pricing data

**APIs used**:
- `cdn-eu.seatsio.net/.../object-statuses` (hex-encoded seat data)
- `embed.admitonelive.com/api/events/seated/{id}` (pricing)

**Example events**: Andrew Callaghan, Tom Papa

---

### Scenario 2: Direct Ticket API

**What it is**: Events using AdmitONE's direct API endpoints

**How it works**:
1. Event page → Click "Buy Now"
2. Redirects to `tickets.admitone.com`
3. Click "Buy Now" again
4. Seating plan loads
5. Scraper captures seat availability and pricing from API responses

**APIs used**:
- `tickets.admitone.com/events/{id}/seating-plan/availability` (seat availability)
- `tickets.admitone.com/events/{id}` (event data with price bands)

**Example events**: Byun Jin Sub, Fox Stevenson, Maanu

**Note**: Some events in this scenario have individual seats, others have ticket categories only.

---

### Scenario 3: GraphQL API

**What it is**: Events using GraphQL for ticket information

**How it works**:
1. Event page has "Get Tickets" button (not "Buy Now")
2. Clicking opens `checkout.admitone.com` in a new tab
3. Checkout page makes GraphQL request
4. Scraper extracts ticket categories from GraphQL response

**APIs used**:
- `graphql.admitone.com` (GraphQL endpoint)
- Query: `OrderSummaryEvent` with eventId variable

**Example events**: I Know Thi$ One!, Drinkurwater Bass Bottle Tour

---

### Scenario 4: Embed Widget

**What it is**: Events with ticket data embedded directly in HTML

**How it works**:
1. Event page → Click "Buy Now"
2. Modal/iframe loads with `embed.admitonelive.com/event/{slug}`
3. Ticket data is in HTML tables and JavaScript variables
4. Scraper parses HTML and extracts ticket categories

**Data sources**:
- HTML table structure (`#ticket-selection-genad` or `.formtab`)
- JavaScript variables (`var EVENT = {...}`, `var level_limits = {...}`)

**Example events**: Jade Eagleson

---

## Troubleshooting

### No seats or tickets found

1. **Check if the event page loads correctly**: Run without `--headless` to see the browser
2. **Check API responses**: Enable `--debug` to see captured API endpoints
3. **Verify the URL**: Make sure you're using the full event URL from AdmitONE
4. **Check the scenario**: The scraper logs which scenario was detected - verify it's correct

### Prices showing as $0

This usually means the pricing API response wasn't captured or parsed correctly:
1. The pricing endpoint may require additional API calls
2. Check the debug logs for pricing-related errors
3. Some events may not have pricing available until later in the flow

### Browser automation issues

- **Make sure Playwright browsers are installed**: `playwright install chromium`
- **Try running without `--headless`**: See what's happening in the browser
- **Check if the site structure has changed**: AdmitONE may update their pages
- **Network issues**: Some events require stable internet connection

### Wrong scenario detected

If the scraper detects the wrong scenario:
1. Check the debug logs to see which URLs were captured
2. The detection logic prioritizes certain indicators - check if your event matches expected patterns
3. Some events may use hybrid approaches that confuse detection

## Project Structure

```
admitone_scraper/
├── main.py                      # CLI entry point
├── config.py                    # Configuration settings
├── models.py                    # Pydantic data models
├── browser.py                   # Playwright browser automation
├── scrapers/
│   ├── __init__.py              # Scraper factory
│   ├── base.py                  # Abstract base scraper
│   ├── seatsio_scraper.py       # Scenario 1: SeatsIO
│   ├── direct_api_scraper.py    # Scenario 2: Direct API
│   ├── graphql_scraper.py       # Scenario 3: GraphQL
│   └── embed_widget_scraper.py  # Scenario 4: Embed Widget
└── utils/
    ├── decoder.py               # Hex decoding utilities
    └── exporters.py             # JSON/CSV export
```
