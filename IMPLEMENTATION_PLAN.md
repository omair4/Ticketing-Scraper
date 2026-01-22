# AdmitONE Ticket Scraper - Implementation Plan

## Overview

This scraper extracts seat availability data (section, row, seat number, price) from AdmitONE events. It handles two distinct API scenarios and multi-step navigation flows.

---

## Scenario Analysis

### Scenario 1: seats.io Integration (e.g., Andrew Callaghan Event)

**URL Pattern**: `admitone.com/events/.../andrew-callaghan/...`

**Navigation Flow**:
1. Land on event page
2. Click "Buy Now" button
3. Modal appears with "Select Seats" button
4. Click "Select Seats"
5. seats.io interactive map loads

**APIs Called**:
1. **Seat Availability** (hex-encoded):
   ```
   https://cdn-eu.seatsio.net/system/public/{public_key}/events/object-statuses?event_key={event_key}
   ```
   - Response is hex-encoded, needs decoding with offset=41
   - Returns seat labels and status (free/booked)

2. **Pricing Data**:
   ```
   https://embed.admitonelive.com/api/events/seated/{event_id}?methods[]=all_levels&methods[]=seatedPriceLevelFees...
   ```
   - Returns `all_levels` with level_id → price mapping
   - Returns `seatedPriceLevelFees` for fee mapping

**Data Mapping**:
- Seat label format: "Gold Circle", "Orchestra 1", "Mezzanine", etc.
- Price comes from `all_levels[level_id].price`
- Service fee from `all_levels[level_id].online_service_fee`

---

### Scenario 2: Direct Ticket API (e.g., Byun Jin Sub Event)

**URL Pattern**: `admitone.com/events/.../byun-jin-sub.../...`

**Navigation Flow**:
1. Land on event page
2. Click "Buy Now" button
3. Redirects to tickets.admitone.com
4. Another "Buy Now" button appears
5. Click it to load seating plan

**APIs Called**:
1. **Seating Plan + Availability**:
   ```
   https://tickets.admitone.com/events/{event_id}/seating-plan/availability?force_refresh=0&with_areas=1
   ```
   - Returns `seats` array with seat details
   - Each seat has `price_band_id`, `block`, `row`, `name` (seat number)

2. **Event Data (Price Bands)**:
   ```
   https://tickets.admitone.com/events/{event_id}
   ```
   - Returns `price_bands` array with id → name/price mapping

**Data Mapping**:
- Section: `seat.block.name` (e.g., "MEZZL", "ORCHR")
- Row: `seat.row.name` (e.g., "AA", "BB")
- Seat: `seat.name` or from `display_as` (e.g., "1", "2")
- Price: Look up `seat.price_band_id` in `price_bands` array

---

## File Structure

```
admitone_scraper/
├── main.py                 # Entry point
├── config.py               # Configuration settings
├── models.py               # Pydantic data models
├── browser.py              # Playwright browser automation
├── detector.py             # Scenario detection logic
├── scrapers/
│   ├── __init__.py
│   ├── base.py             # Base scraper class
│   ├── seatsio_scraper.py  # Scenario 1: seats.io
│   └── direct_api_scraper.py  # Scenario 2: Direct API
├── utils/
│   ├── __init__.py
│   ├── decoder.py          # Hex decoding utilities
│   └── exporters.py        # JSON/CSV export
├── requirements.txt
└── README.md
```

---

## Implementation Steps

### Step 1: Setup & Dependencies
- Install Playwright, httpx, pydantic
- Configure browser settings

### Step 2: Browser Automation
- Navigate to event URL
- Detect which buttons exist ("Buy Now", "Select Seats", etc.)
- Handle 1-step or 2-step flows
- Capture network requests

### Step 3: Scenario Detection
- Check captured URLs for patterns:
  - `cdn-eu.seatsio.net` → Scenario 1
  - `tickets.admitone.com` → Scenario 2

### Step 4: Data Extraction
- Parse API responses
- Map seats to prices
- Normalize output format

### Step 5: Export
- Output to JSON and/or CSV
- Include all required fields: section, row, seat, price

---

## Key Technical Decisions

1. **Playwright over Selenium**: Better async support, auto-waiting, network interception
2. **Network Interception**: Capture API responses directly instead of parsing DOM
3. **Strategy Pattern**: Different scrapers for different scenarios
4. **Pydantic Models**: Validate and normalize output data

---

## Output Format

```json
{
  "event_name": "Andrew Callaghan: All Gas No Brakes",
  "venue": "Queen Elizabeth Theatre",
  "date": "Saturday, February 14, 2026",
  "scraped_at": "2026-01-22T10:30:00Z",
  "total_seats": 150,
  "available_seats": 120,
  "seats": [
    {
      "section": "Orchestra 1",
      "row": "A",
      "seat": "1",
      "price": 59.50,
      "service_fee": 16.75,
      "total_price": 76.25,
      "status": "available"
    }
  ]
}
```
