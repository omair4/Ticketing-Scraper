# Tixr.com Scraping — Scenario Breakdown & Feasibility Report

**Date:** March 3, 2026  
**Prepared for:** Client Meeting  
**Status:** Reconnaissance Complete

---

## Executive Summary

Tixr uses a **clean, consistent REST API** architecture. All event data is accessible via a single endpoint (`/api/events/{eventId}`) that returns **plain JSON** — no obfuscation, no hex encoding, no GraphQL complexity. This is significantly simpler than AdmitONE or PreKindle.

The main challenge is **DataDome bot protection** (CAPTCHA), which blocks automated clicks and secondary API calls. However, the initial page load always succeeds and contains all the data we need through intercepted network responses.

---

## Tixr API Architecture

### Core Endpoints (all return plain JSON)

| Endpoint | Method | Purpose | Auth Required |
|----------|--------|---------|---------------|
| `/api/page/requirements?url=...` | GET | Page metadata, tracking config, login options | No |
| `/api/events/{eventId}` | GET | **Complete event data** — venue, sales, tiers, prices, categories | No |
| `/api/events/{eventId}/seating-chart-holds` | POST | Seats.io hold token for reserved seating | No |
| `/api/events/{eventId}/listing-prices` | GET | Resale/listing prices (if applicable) | No |
| `/api/featured` | GET | Homepage featured events list | No |
| `/api/user/profile` | GET | User profile (requires auth, 403 without login) | Yes |
| `/ajax/wtii` | GET | Session initialization | No |

### External APIs (only for reserved seating events)

| Endpoint | Purpose |
|----------|---------|
| `cdn-na.seatsio.net/chart.js` | Seats.io chart rendering library |
| `cdn-na.seatsio.net/.../rendering-info?event_key={eventId}` | Seat map layout/chart data |
| `cdn-na.seatsio.net/.../object-statuses` | Individual seat availability status |

---

## Identified Scenarios

### Scenario 1: Reserved Seating with Seats.io (CAN DO ✅)

**Example:** [Earthquakes Season Tickets](https://www.tixr.com/groups/sjearthquakes/events/earthquakes-season-tickets-18-matches--178222)

**What we found:**
- Venue `seatingType: "RESERVED"` 
- Venue has `seatsIoConfiguration.workspace.key` (e.g., `46a7d3d0-4e04-4904-a7b4-3357211457e8`)
- 68 sales entries across categories: `GA`, `STANDARD`, `PARKING`
- Sales have `state`: `OPEN`, `LOCKED`, `SOLD_OUT`
- Seats.io chart.js loads on page, with `event_key` matching the Tixr `eventId`
- **Seating chart holds** API returns a `holdToken` with expiration

**Data available:**
- Event name, venue, dates, description
- All ticket categories with prices (via `tiers[].price`)
- Category types: GA, STANDARD (reserved), PARKING
- Sale state (OPEN/LOCKED/SOLD_OUT)
- Delivery type (PDF, ROTATING_DIGITAL_CODE, DIGITAL_CODE)
- Seats.io workspace key for seat map access
- Max tickets allowed per order

**Scraping approach:**
- Intercept `/api/events/{id}` response during page load → complete event + sales data
- For seat-level availability: use Seats.io `rendering-info` and `object-statuses` APIs
- **We already have the Seats.io decoder** from PreKindle/AdmitONE (hex-offset bruteforce)

**Complexity:** Medium — same Seats.io pattern we already handle

---

### Scenario 2: General Admission / Open Seating (CAN DO ✅)

**Example:** [FISHER at Navy Pier](https://www.tixr.com/groups/navypiereventscenter/events/fisher-168656)

**What we found:**
- Venue `seatingType: "OPEN"` — no seat map
- No `seatsIoConfiguration` on venue
- Simple sales: 2 entries (GA at $132.20, VIP at $171.13)
- Both currently `SOLD_OUT`
- `allInPricing: true` (price includes fees)
- Tiers contain price info directly

**Data available:**
- Event info, venue, dates
- Ticket categories (GA, VIP) with exact prices
- Tier descriptions (VIP perks in HTML)
- Availability state
- Pricing model (all-in vs. fees separate)

**Scraping approach:**
- Single intercept of `/api/events/{id}` → everything we need
- No seat map, no Seats.io — pure API data extraction
- Parse `tiers[].price`, `tiers[].name`, `tiers[].info` (HTML description)

**Complexity:** Low — simplest scenario

---

### Scenario 3: Festival / Multi-Category (CAN DO ✅)

**Example:** [Lightning in a Bottle 2026](https://www.tixr.com/groups/dolab/events/lightning-in-a-bottle-2026-145482)

**What we found:**
- Venue `seatingType: "OPEN"`
- 55 sales entries across 6 categories: `CAMPING`, `PARKING`, `LODGING`, `VIP`, `SHUTTLE`, `GA`
- Sales have 3 states: `OPEN`, `QUEUED`, `SOLD_OUT`
- Prices range from $0 (Hotel Packages redirect) to $10,305 (Waterfront Luxury RV)
- Some items have `price: null` with `minPrice`/`maxPrice` range (Kids Ticket: $0–$249)
- All `type: "FIXED"`, all `seatingType: "OPEN"`

**Data available:**
- Complete catalog: GA passes, VIP passes, camping options, lodging, parking, shuttles
- Exact pricing for each tier
- Availability state per item
- Category classification

**Scraping approach:**
- Same `/api/events/{id}` intercept
- Group sales by category for organized output
- Handle `QUEUED` state (waitlist items)

**Complexity:** Low — same API, just more data

---

### Scenario 4: Event Discovery / Homepage (CAN DO ✅)

**Example:** [tixr.com homepage](https://www.tixr.com/)

**What we found:**
- `/api/featured` returns array of featured events with name, URL, venue, type
- `/api/page/requirements` provides page metadata

**Data available:**
- Featured event names, URLs, venues
- Event types (`EVENT`, `GENERIC` for group/season pages)

**Scraping approach:**
- Direct API call to `/api/featured`
- Parse and follow event URLs for detailed scraping

**Complexity:** Very Low

---

### Scenario 5: Access Code / Locked Sales (CAN DO WITH LIMITATIONS ⚠️)

**What we found:**
- Some sales have `state: "LOCKED"` (e.g., Quakes College Pass)
- These require an access code to unlock
- The `at` field and `accessCodeRequired` flag indicate this
- The sale appears in the API response but price/availability may be hidden

**What we can scrape:**
- Sale existence and category
- That it requires a code
- Name and description

**What we CANNOT scrape:**
- Actual prices behind the access code (requires valid code)
- Availability for locked sales

**Complexity:** N/A — we report its existence but can't unlock it

---

### Scenario 6: External Redirect (DON'T NEED ❌)

**What we found:**
- Tixr does NOT redirect to third-party ticketing sites
- All ticketing happens within tixr.com domain
- The `optionalSaleRedirectUrl` field exists in the schema but was `null` on all tested events
- Some items like "Hotel Packages" show `price: 0` and `SOLD_OUT` — these may have been external links but are handled within Tixr's UI

**Conclusion:** No external redirect scenario to handle

---

## Anti-Bot Protection: DataDome

### What we discovered:
- Tixr uses **DataDome** (enterprise-grade bot protection)
- `DATADOME_JS_API_KEY: "3D11317A8F884C0DCE99FB28F60835"`
- DataDome posts to `api-js.datadome.co/js/` on every page load
- **CAPTCHA challenge triggers on**: any click interaction with the page after ~30s, or any secondary API fetch from page JS
- **Does NOT block**: initial page load, initial API responses during page load

### Impact on scraping:
- **Initial page load always works** — we get `/api/events/{id}` response during navigation
- **Clicking tickets triggers CAPTCHA** — cannot click through to seat map or purchase flow
- **Secondary fetch() calls get 403** — cannot re-fetch APIs from page context

### Mitigation strategy:
1. **Intercept responses during navigation** (Playwright `page.on('response')`) — this always works
2. **One page load per event** — don't try to interact after loading
3. For Seats.io data: call Seats.io APIs directly (they're on a different domain, not behind DataDome)
4. Anti-bot headers: custom user agent, `navigator.webdriver` spoofing (already implemented)

---

## Encoding & Decoding

### Tixr's own APIs: **No encoding** ✅
- All responses are **plain JSON**
- No hex encoding, no base64, no obfuscation
- Clean, well-structured data with clear field names
- Prices are plain numbers (not encoded)

### Seats.io APIs (only for reserved seating): **Existing decoder works** ✅
- Same hex-offset encoding as AdmitONE/PreKindle
- Our existing `decoder.py` (`_bruteforce_offset_decode`) handles this
- Applies to `rendering-info` and `object-statuses` responses

---

## Tixr Sale Categories (from API)

| Category | Description | Example |
|----------|-------------|---------|
| `GA` | General Admission | Standing/open floor |
| `VIP` | VIP Access | Premium viewing, private bars |
| `STANDARD` | Reserved Seating | Specific seat via Seats.io |
| `PARKING` | Parking Pass | Venue parking |
| `CAMPING` | Camping Options | Tent sites, RV spots, glamping |
| `LODGING` | Lodging/Accommodation | Airstreams, RVs, hotel packages |
| `SHUTTLE` | Transportation | Bus/shuttle to venue |

## Tixr Sale States

| State | Meaning |
|-------|---------|
| `OPEN` | Available for purchase |
| `SOLD_OUT` | No more inventory |
| `LOCKED` | Requires access code |
| `QUEUED` | Waitlist / queue |

## Tixr Sale Types

| Type | Meaning |
|------|---------|
| `FIXED` | Fixed-price ticket (most common) |

---

## Data Model Comparison

### What Tixr provides vs. what we need:

| Data Point | Available | Source |
|-----------|-----------|--------|
| Event name | ✅ | `/api/events/{id}` → `name` |
| Venue name & address | ✅ | `/api/events/{id}` → `venue` |
| Event dates | ✅ | `startDate`, `endDate` (Unix ms) |
| Ticket categories | ✅ | `sales[].category` |
| Ticket prices | ✅ | `sales[].tiers[].price` |
| Price ranges | ✅ | `tiers[].minPrice`, `tiers[].maxPrice` |
| Availability state | ✅ | `sales[].state` |
| VIP/tier descriptions | ✅ | `tiers[].info` (HTML) |
| Seat-level availability | ✅ (reserved only) | Seats.io APIs |
| Seat map layout | ✅ (reserved only) | Seats.io `rendering-info` |
| All-in pricing flag | ✅ | `allInPricing` |
| Max tickets per order | ✅ | `maxTicketsAllowed` |
| Delivery method | ✅ | `deliveryConfiguration` |
| Access code requirements | ✅ (flag only) | `state: "LOCKED"` |
| Resale listings | ✅ | `/listing-prices` endpoint |
| Group/organizer info | ✅ | `group` object |

---

## Effort Estimate

| Component | Effort | Notes |
|-----------|--------|-------|
| GA/Open events scraper | 2-3 hours | Simplest — one API call, parse JSON |
| Festival multi-category scraper | +1 hour | Same API, group by category |
| Reserved seating (Seats.io) | 3-4 hours | Reuse existing decoder, same pattern as AdmitONE |
| Event discovery (homepage) | 1-2 hours | `/api/featured` + pagination |
| CLI + exporters | Already done | Skeleton exists in `tixr_scraper/` |
| DataDome mitigation | Built-in | Response interception approach |
| **Total** | **~8-10 hours** | Leveraging existing code from other scrapers |

---

## Key Takeaways for Client

1. **Tixr is the cleanest site to scrape** — plain JSON APIs, well-structured data, no obfuscation
2. **All ticket data is available from a single API call** — `/api/events/{id}` contains everything
3. **Reserved seating uses the same Seats.io system** as AdmitONE — our decoder already handles it
4. **DataDome bot protection exists** but is bypassable via response interception during page navigation
5. **No external redirects** — everything stays on tixr.com
6. **6 ticket categories supported**: GA, VIP, STANDARD, PARKING, CAMPING, LODGING, SHUTTLE
7. **We can cover all scenarios** except unlocking access-code-protected sales (those require valid codes)
