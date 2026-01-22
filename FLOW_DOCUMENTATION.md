# AdmitONE Scraper - Detailed Implementation Flow

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        main.py                                   │
│  Entry point - parses args, orchestrates scraping               │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      browser.py                                  │
│  BrowserAutomation class                                         │
│  - Launches Playwright browser                                   │
│  - Navigates to event URL                                        │
│  - Clicks through purchase flow (1 or 2 steps)                   │
│  - Captures all API responses via network interception           │
│  - Detects which scenario applies                                │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Scenario Detection                             │
│  Based on captured URLs:                                         │
│  - seatsio.net OR embed.admitonelive.com → SeatsIOScraper       │
│  - tickets.admitone.com → DirectAPIScraper                       │
└───────────────────┬─────────────────────────┬───────────────────┘
                    │                         │
                    ▼                         ▼
┌───────────────────────────┐   ┌───────────────────────────────┐
│   seatsio_scraper.py      │   │   direct_api_scraper.py       │
│                           │   │                               │
│   1. Parse pricing from   │   │   1. Parse event data from    │
│      embed.admitonelive   │   │      tickets.admitone.com     │
│   2. Decode hex seats.io  │   │   2. Parse availability API   │
│   3. Correlate seats to   │   │   3. Map price_band_id to     │
│      price levels         │   │      ticket prices            │
└───────────────────────────┘   └───────────────────────────────┘
                    │                         │
                    └────────────┬────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      exporters.py                                │
│  - Validates data with Pydantic models                          │
│  - Exports to JSON and CSV                                       │
│  - Prints summary                                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Scenario 1: seats.io Flow (Andrew Callaghan Event)

### Navigation Steps

```
Step 1: Load Event Page
https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/andrew-callaghan/andrew-callaghan/681d11117d3a4df5fd2d2c24

Step 2: Click "Buy Now" button
→ Modal popup appears

Step 3: Click "Select Seats" button in modal
→ seats.io interactive map loads
```

### API Calls Captured

```
1. PRICING API (embed.admitonelive.com)
   URL: https://embed.admitonelive.com/api/events/seated/10031723?methods[]=all_levels&methods[]=seatedPriceLevelFees...
   
   Response contains:
   {
     "event_id": "10031723",
     "event": "Andrew Callaghan: All Gas No Brakes x Channel 5 'Carnival'",
     "venue": {
       "venue_name": "Queen Elizabeth Theatre",
       "address": "190 Princes' Blvd",
       "city": "Toronto"
     },
     "all_levels": {
       "49024921": {"level": "Accessible", "price": "49.50", "online_service_fee": 15.5},
       "49024923": {"level": "Mezzanine", "price": "39.50", "online_service_fee": 14.5},
       "49029339": {"level": "Gold Circle", "price": "69.50", "online_service_fee": 16.75},
       "49029340": {"level": "Gold Circle Platinum", "price": "80.00", "online_service_fee": 19.5},
       "49029341": {"level": "Orchestra 1", "price": "59.50", "online_service_fee": 16.75},
       "49029342": {"level": "Orchestra 2", "price": "49.50", "online_service_fee": 15.5}
     }
   }

2. SEAT AVAILABILITY API (seats.io)
   URL: https://cdn-eu.seatsio.net/system/public/{public_key}/events/object-statuses?event_key=10031723
   
   Response is HEX ENCODED!
   Raw: "7b226576656e744964223a353233313033322c..."
   
   Decoded (using offset=41):
   [
     {
       "eventId": 5231032,
       "eventKey": "seating-map-36796905-2026-05-08-00-00",
       "objectLabelOrUuid": "Orchestra 1-A-12",
       "status": "free"
     },
     {
       "eventId": 5231032,
       "objectLabelOrUuid": "Mezzanine-B-5",
       "status": "booked"
     }
   ]
```

### Data Correlation Logic

```python
# Step 1: Build price lookup from all_levels
price_by_name = {
    "accessible": {"price": 49.50, "fee": 15.5},
    "mezzanine": {"price": 39.50, "fee": 14.5},
    "gold circle": {"price": 69.50, "fee": 16.75},
    "orchestra 1": {"price": 59.50, "fee": 16.75},
    ...
}

# Step 2: For each seat from seats.io
seat_label = "Orchestra 1-A-12"

# Parse into: section="Orchestra 1", row="A", seat="12"

# Step 3: Match section to price level
# "orchestra 1" matches price_by_name["orchestra 1"]
# → price=59.50, fee=16.75

# Step 4: Create Seat object
Seat(
    section="Orchestra 1",
    row="A", 
    seat="12",
    price=59.50,
    service_fee=16.75,
    status="available"
)
```

---

## Scenario 2: Direct API Flow (Byun Jin Sub Event)

### Navigation Steps

```
Step 1: Load Event Page
https://admitone.com/events/toronto/pro/other/queen-elizabeth-theatre/artist/byun-jin-sub-wish-list/694ee2570472d0f8bde99b4e

Step 2: Click "Buy Now" button
→ Redirect to tickets.admitone.com

Step 3: Click "Buy Now" again on tickets.admitone.com
→ Seating plan modal loads
```

### API Calls Captured

```
1. EVENT DATA API
   URL: https://tickets.admitone.com/events/144917
   
   Response contains:
   {
     "seating_plan": {
       "name": "Byun Jin Sub / Wish List @ QET - Feb 15, 2026",
       "venue_id": 15364
     },
     "price_bands": [
       {"id": 71301, "name": "Orchestra 1", "colour": "#E5A30B"},
       {"id": 71302, "name": "Mezzanine 1", "colour": "#992C4E"},
       {"id": 71303, "name": "Orchestra 2", "colour": "#839E3D"},
       {"id": 71304, "name": "Orchestra 3", "colour": "#5D9CBB"}
     ],
     "ticket_types": [
       {"price_band_id": 71301, "price": "160.02"},
       {"price_band_id": 71302, "price": "140.00"}
     ]
   }

2. SEAT AVAILABILITY API
   URL: https://tickets.admitone.com/events/144917/seating-plan/availability?force_refresh=0&with_areas=1
   
   Response contains:
   {
     "seats": [
       {
         "id": 13484466,
         "price_band_id": 71302,
         "name": "1",
         "display_as": "AA-1 (MEZZL)",
         "status_slug": "available",
         "is_available": true,
         "block": {"name": "MEZZL", "local_id": 2},
         "row": {"name": "AA", "local_id": 115},
         "price_band": {"name": "Mezzanine 1"}
       },
       {
         "id": 13484467,
         "price_band_id": 71302,
         "name": "2",
         "display_as": "AA-2 (MEZZL)",
         "status_slug": "available",
         "block": {"name": "MEZZL"},
         "row": {"name": "AA"}
       }
     ]
   }
```

### Data Correlation Logic

```python
# Step 1: Build price lookup from ticket_types
price_by_band = {
    71301: Decimal("160.02"),  # Orchestra 1
    71302: Decimal("140.00"),  # Mezzanine 1
    ...
}

# Step 2: Build band name lookup from price_bands
band_names = {
    71301: "Orchestra 1",
    71302: "Mezzanine 1",
    ...
}

# Step 3: For each seat in availability response
seat_data = {
    "price_band_id": 71302,
    "name": "1",
    "block": {"name": "MEZZL"},
    "row": {"name": "AA"},
    "status_slug": "available"
}

# Extract: section="MEZZL", row="AA", seat="1"

# Step 4: Look up price using price_band_id
# 71302 → price=140.00, level="Mezzanine 1"

# Step 5: Create Seat object
Seat(
    section="Mezzanine Left",  # Normalized from "MEZZL"
    row="AA",
    seat="1",
    price=140.00,
    status="available",
    price_level="Mezzanine 1"
)
```

---

## Section Name Normalization

The scraper normalizes abbreviated section names to readable format:

| Raw | Normalized |
|-----|------------|
| MEZZL | Mezzanine Left |
| MEZZR | Mezzanine Right |
| MEZZC | Mezzanine Center |
| ORCHL | Orchestra Left |
| ORCHR | Orchestra Right |
| ORCHC | Orchestra Center |
| BALCL | Balcony Left |
| BALCR | Balcony Right |

---

## Error Handling

### Navigation Errors
- Button not found → Try alternative selectors
- Timeout → Retry with longer wait
- Page crash → Log error, return partial results

### API Parsing Errors
- Hex decode failure → Try multiple offsets (41, 0, 32)
- JSON parse error → Log and continue with other responses
- Missing price → Use default $0 and add warning

### Data Validation
- Pydantic models validate all output
- Invalid seats are skipped with warning
- Empty results trigger error flag

---

## Output Files

### JSON Structure
```json
{
  "event": {
    "event_id": "10031723",
    "event_name": "Andrew Callaghan",
    "venue_name": "Queen Elizabeth Theatre",
    "event_url": "..."
  },
  "scraped_at": "2026-01-22T...",
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
  "errors": [],
  "warnings": []
}
```

### CSV Columns
| section | row | seat | price | service_fee | total_price | status | price_level | event_name | venue | event_date |
