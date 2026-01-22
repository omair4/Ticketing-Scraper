# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AdmitONE Ticket Scraper - A Python-based web scraper that extracts seat availability data from AdmitONE event pages. Handles two distinct API scenarios with different navigation flows and data structures.

## Common Development Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run the scraper
python main.py "https://admitone.com/events/..."

# Run with options
python main.py <url> --headless --output-dir ./results --format json csv --debug

# Test installation
python test_installation.py
```

## Architecture Overview

### Multi-Scenario Design

The scraper uses a **Strategy Pattern** to handle two different API scenarios:

1. **Scenario 1 (seatsio)**: Events using seats.io integration
   - Detected by URLs containing `seatsio.net` or `embed.admitonelive.com`
   - Navigation: Event page → Buy Now → Select Seats → seats.io map
   - APIs: Hex-encoded seats.io status + embed.admitonelive.com pricing

2. **Scenario 2 (direct_api)**: Events using direct ticket API
   - Detected by URLs containing `tickets.admitone.com`
   - Navigation: Event page → Buy Now → Redirect → Buy Now again → Seating plan
   - APIs: tickets.admitone.com availability + event data with price bands

### Key Components

**Browser Automation (`browser.py`)**
- Uses Playwright for async browser control
- Captures ALL network responses via `page.on('response')` interception
- Handles 1-step vs 2-step navigation flows adaptively
- Auto-detects scenario based on captured API URLs
- Returns `NavigationResult` with captured responses + detected scenario

**Scenario Detection Logic**
```python
# In browser.py:_detect_scenario()
# Scans captured_responses for URL patterns:
'seatsio.net' → seatsio
'tickets.admitone.com' + 'seating-plan' → direct_api
'embed.admitonelive.com' → seatsio
```

**Scraper Pattern (`scrapers/`)**
- `base.py`: Abstract `BaseScraper` with common utilities
- `seatsio_scraper.py`: Parses hex-encoded seats.io + pricing API
- `direct_api_scraper.py`: Parses tickets.admitone.com APIs
- `__init__.py`: `get_scraper_for_scenario()` factory function

**Data Flow**
```
main.py
  → BrowserAutomation.navigate_to_seats()
    → Captures network responses
    → Detects scenario
  → get_scraper_for_scenario(scenario)
  → scraper.extract(captured_responses, page_html, event_url)
    → Returns ScrapeResult
  → export_results(result, formats)
```

## Critical Implementation Details

### Scenario 1 (seats.io) Data Correlation

**Problem**: Seat availability and pricing come from separate APIs

**Solution**:
1. Parse `embed.admitonelive.com/api/events/seated/{id}` → `all_levels` dict
   - Maps level_id → {level, price, online_service_fee}
2. Decode hex response from `cdn-eu.seatsio.net/.../object-statuses`
   - Uses `decode_hex_offset(hex_string, offset=41)` from `utils/decoder.py`
   - Returns seats with labels like "Orchestra 1-A-12"
3. Parse seat label: "Orchestra 1-A-12" → section="Orchestra 1", row="A", seat="12"
4. Match section name (lowercase) to price level name → lookup price + fee

**Key function**: `seatsio_scraper.py:_correlate_seats_with_prices()`

### Scenario 2 (direct_api) Data Correlation

**Problem**: Seats have `price_band_id` but actual prices are in `ticket_types`

**Solution**:
1. Parse `tickets.admitone.com/events/{id}` → extract `price_bands` and `ticket_types`
2. Build lookup: `price_band_id → price` from ticket_types
3. Build lookup: `price_band_id → name` from price_bands
4. Parse availability API: each seat has `price_band_id`, `block`, `row`, `name`
5. Map: seat.price_band_id → price + level_name

**Key function**: `direct_api_scraper.py:_build_price_lookup()`

### Hex Decoding (seats.io only)

seats.io returns responses as hex-encoded strings with offset=41:
```python
# utils/decoder.py
encoded_bytes = bytes.fromhex(hex_string)
decoded = "".join(chr(b - 41) for b in encoded_bytes)
```

`try_decode_response()` attempts multiple offsets: [41, 0, 32, 64]

### Section Name Normalization

Abbreviations are normalized to readable names:
```python
# In base.py:normalize_section_name()
'MEZZL' → 'Mezzanine Left'
'ORCHR' → 'Orchestra Right'
'ORCHC' → 'Orchestra Center'
```

### Network Interception

Why: DOM parsing is unreliable; direct API data is authoritative

How: `browser.py` sets up `page.on('response', self._on_response)`
- Filters for patterns: 'seatsio.net', 'embed.admitonelive.com', 'tickets.admitone.com', '/api/', etc.
- Captures body for JSON responses
- Stores as `CapturedResponse` objects

### Navigation Strategy

**Adaptive clicking**: Tries multiple selectors for "Buy Now" buttons
- Primary: `button:has-text("Buy Now")`
- Fallbacks: `a:has-text("Buy Now")`, generic text matching `/buy|get tickets/i`

**Modal detection**: After first Buy Now, checks for "Select Seats" button
- If found → clicks it (Scenario 1)
- If not found → checks if on tickets.admitone.com → clicks Buy Now again (Scenario 2)

## Data Models

**Pydantic models in `models.py`**:
- `Seat`: section, row, seat, price, service_fee, status, price_level
  - Computed field: `total_price` = price + service_fee
  - Validators: clean strings, parse prices (handles "$59.50", "59.50", 59.50)
- `EventInfo`: event_id, event_name, venue_name, event_url, etc.
- `ScrapeResult`: event, seats[], scenario_type, errors[], warnings[]
  - Auto-calculates: total_seats_found, available_seats_count, min/max prices

## Configuration

**`config.py`**:
- `ScraperConfig`: Browser timeouts, wait delays, headless mode
- `APIPatterns`: URL patterns for scenario detection
- `BUTTON_SELECTORS`: CSS selectors for navigation buttons

**Defaults**:
- headless=False (set True for production)
- browser_timeout=30000ms
- navigation_timeout=15000ms
- click_delay=1000ms
- output_dir='./output'

## Output Format

**JSON**: Complete structured data with event metadata, all seats, statistics
**CSV**: Flattened seat rows with event context columns

Both formats validated via Pydantic models before export.

## Error Handling Philosophy

- Network errors: Log and continue with partial data
- API parsing errors: Try multiple decode offsets, log failures
- Missing prices: Default to $0 with warning
- Navigation failures: Return error in ScrapeResult, don't crash
- Validation errors: Skip invalid seats, add to warnings

All errors/warnings accumulated in `ScrapeResult.errors` and `ScrapeResult.warnings`

## Adding New Scenarios

To support a new API pattern:

1. Create new scraper class inheriting from `BaseScraper` in `scrapers/`
2. Implement `get_scenario_name()` and `extract()` methods
3. Register in `scrapers/__init__.py:get_scraper_for_scenario()`
4. Add detection pattern to `browser.py:_detect_scenario()`
5. Add URL patterns to `config.py:APIPatterns`

## Logging

Uses `structlog` for structured logging:
- `logger.info()`: Navigation steps, API captures, extraction progress
- `logger.debug()`: Individual API URLs, detailed parsing
- `logger.error()`: Failed operations, parsing errors
- `logger.warning()`: Missing data, fallback behaviors

Enable debug mode: `--debug` flag
