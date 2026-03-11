# Tixr.com Scraping Research Findings

**Date:** 2026-03-06
**URL:** https://www.tixr.com/

---

## 1. Website Architecture: CSR (Client-Side Rendered)

- **Rendering:** Fully **CSR** (Client-Side Rendered) using a **custom vanilla JS framework** (no React, Vue, Next.js, or Angular detected)
- **Initial HTML:** Empty shell with `<div id="loading">` and `<div id="site">`, all content injected via JavaScript
- **JS Framework:** Custom — loads via `App.js`, `Events.js`, `Orders.js`, `PriceLevels.js`, `ReservedSeating.js`, etc. from `static.tixr.com`
- **SSR for SEO only:** Event pages have server-rendered `<title>`, `<meta>` tags, Open Graph tags, and **JSON-LD structured data** — but the visible page content is still rendered client-side
- **No SPA framework detected** — it's a custom MVC-style JS app

---

## 2. Bot Protection: DataDome (Aggressive)

### What's Protected
| Resource | From Browser (in-page fetch) | From curl/requests | Notes |
|----------|-------|------|-------|
| `/api/featured` | **200 OK** | **403 Blocked** | Homepage featured carousel |
| `/api/page/requirements` | **200 OK** | **403 Blocked** | Page bootstrap data |
| `/api/events/{id}` | **200 OK** | **403 Blocked** | Individual event detail |
| `/api/groups/{id}` | **200 OK** | **403 Blocked** | Group/organizer detail |
| `/api/groups/{id}/events` | **200 OK** | **403 Blocked** | Group's event listing |
| `/api/search` | **403 Blocked** | **403 Blocked** | Blocked even in browser! |
| `/api/discover` | **403 Blocked** | **403 Blocked** | Blocked even in browser! |
| `/api/groups` | **403 Blocked** | **403 Blocked** | No public group listing |
| `/api/user/profile` | **403 Blocked** | **403 Blocked** | Requires auth |
| `/robots.txt` | **403 Blocked** | **403 Blocked** | Even robots.txt is blocked |
| `/sitemap.xml` | **403 Blocked** | **403 Blocked** | No sitemap accessible |

### DataDome Details
- Provider: **DataDome** (`api-js.datadome.co`, `js.datadome.co/tags.js`)
- Captcha delivery: `geo.captcha-delivery.com`, `ct.captcha-delivery.com`
- Blocked responses return HTML with a DataDome JS challenge (not a visual captcha in most cases)
- The browser gets a DataDome cookie after passing the JS challenge, which is used for subsequent API requests
- **Rate limiting:** After too many API calls from within the browser, DataDome may start blocking those too (observed after ~10 rapid fetch calls)

### Key Takeaway
> **Pure HTTP requests (curl, httpx, requests) will NOT work.** Every single API endpoint returns 403 without a valid DataDome cookie, which requires executing JavaScript in a real browser environment.

---

## 3. Can We Scrape with Requests Only?

**NO.** Requests-only scraping is not viable because:

1. **All API endpoints return 403** without DataDome JS challenge completion
2. **No sitemap or robots.txt** accessible for URL discovery
3. **No public search/discover API** — even blocked from within the browser
4. **DataDome JS challenge** requires full browser JS execution to obtain the session cookie

---

## 4. Scraping Strategy: Playwright Required

### 4.1 Event Catalog Discovery

There is **no public API to list all events**. Catalog discovery must use these approaches:

#### Approach A: Homepage Topics (Partial Catalog, ~232 events)
- **Endpoint:** `/api/page/requirements?url=%2F&queryParams=`
- **Data:** Returns `setup.topics[]` array with 10 categories:
  - Sports Highlights, Festivals, Music Venues, Comedy, Electronic, Pop Culture, Sports, Motorcycles & Motorsports, Food & Beverage, Esports
- Each topic has a `carousel.items[]` array with events (id, name, link, startDate, imageUrl)
- **Total:** ~232 events across all topics (not deduplicated)
- **Limitation:** Only curated/featured events, NOT the full catalog

#### Approach B: Featured Events
- **Endpoint:** `/api/featured`
- Returns ~10 hero carousel items (events + groups)
- Very limited subset

#### Approach C: Group → Events Enumeration (Most Complete)
1. Get known group subdomains from homepage topics/featured
2. Navigate to group page → `/api/page/requirements` gives `groupId`
3. Fetch `/api/groups/{groupId}/events` → full event list for that group
4. **Challenge:** No API to list all groups — must discover group subdomains organically

#### Approach D: Google/Search Engine Indexing
- Event pages have good SEO (structured data, meta tags)
- Could use `site:tixr.com/groups/` Google search to discover groups
- Or scrape Google results for `site:tixr.com inurl:/events/`

### 4.2 Individual Event Scraping

Rich data available from the `/api/events/{eventId}` endpoint (from within Playwright):

#### Event API Response Fields
```
id, name, shortName, status, startDate, endDate, doorsOpenDate
venue: { id, name, address, latitude, longitude, timezone, seatingType, capacity }
group: { id, name, subdomain, description, logoUrl }
eventCategoryName (Festival, Concert, etc.)
sales[]: array of sale objects (55 for LIB)
  - id, eventId, state (OPEN/QUEUED/CLOSED), type (FIXED)
  - maxTicketsAllowed, deliveryConfiguration
  - priceLevels (embedded in each sale)
seatsIoConfiguration (for reserved seating events)
media[]: gallery images
lineups[]: performer list
description, legal, keywords
resaleEnabled, allInPricing
queue_settings
```

#### Structured Data (JSON-LD) — Available WITHOUT JS
Event pages embed `application/ld+json` in the raw HTML source:
```json
{
  "@type": "Event",
  "name": "...",
  "startDate": "...",
  "location": { "@type": "EventVenue", "name": "...", "address": {...} },
  "offers": [
    { "name": "5-Day GA Pass (Tier 5)", "price": "425.73", "category": "PRIMARY > GA" },
    { "name": "3-Day VIP Pass (Tier 5)", "price": "563.23", "category": "PRIMARY > VIP" }
  ],
  "performer": [
    { "@type": "MusicGroup", "name": "Empire of the Sun" }
  ]
}
```
**This is the one piece of data available without browser execution — IF you know the event URL.**

---

## 5. API Endpoints Summary

### Working Endpoints (from within Playwright browser context)

| Endpoint | Method | Data Returned |
|----------|--------|---------------|
| `/api/featured` | GET | Homepage hero carousel (10 items) |
| `/api/page/requirements?url={path}` | GET | Page bootstrap: topics, groupId, eventId |
| `/api/events/{eventId}` | GET | Full event detail + sales + pricing |
| `/api/groups/{groupId}` | GET | Group detail (name, description, logo, layout) |
| `/api/groups/{groupId}/events` | GET | All events for a group |
| `/ajax/wtii` | GET | Unknown (session/tracking) |

### URL Patterns
- Event pages: `https://www.tixr.com/groups/{subdomain}/events/{slug}-{eventId}`
- Group pages: `https://www.tixr.com/groups/{subdomain}`
- Event IDs are numeric integers (e.g., 145482)
- Group IDs are numeric integers (e.g., 798, 2686)

---

## 6. Captcha Encounter

- **No visual CAPTCHA was encountered** during normal Playwright browsing
- DataDome uses an invisible JS challenge (not a visual CAPTCHA like reCAPTCHA)
- The challenge runs automatically via `tags.js` and `api-js.datadome.co/js/`
- If detected as a bot, it returns an HTML page with a JS redirect to `captcha-delivery.com`
- Playwright with default settings passed the challenge without issues
- **Risk:** High-frequency scraping or headless detection may trigger a visual CAPTCHA

---

## 7. Recommended Scraping Architecture

```
Playwright Browser (headful or stealth headless)
  │
  ├── Phase 1: Catalog Discovery
  │   ├── Navigate to homepage → /api/page/requirements → extract all topic events
  │   ├── Extract unique group subdomains from event URLs
  │   ├── For each group: navigate → /api/groups/{id}/events → get all events
  │   └── Deduplicate event IDs
  │
  ├── Phase 2: Event Detail Scraping
  │   ├── Navigate to event page (to establish DataDome cookie context)
  │   ├── Fetch /api/events/{eventId} → full event JSON
  │   ├── Parse sales[], priceLevels, venue, lineup
  │   └── Optional: extract JSON-LD from raw HTML for validation
  │
  └── Phase 3: Monitoring/Refresh
      ├── Re-fetch /api/events/{eventId} for price/availability changes
      └── Re-check /api/groups/{groupId}/events for new events
```

### Key Considerations
1. **Playwright is mandatory** — no requests-only path exists
2. **Rate limiting:** Add delays between requests to avoid DataDome escalation
3. **Cookie reuse:** Once DataDome cookie is established, subsequent API fetches within the same browser context work
4. **No global search:** Must discover events through groups, not via a search API
5. **Stealth plugins** (`playwright-extra` + `stealth`) recommended for headless mode
6. **seats.io integration:** Some events use `seatsIoConfiguration` for reserved seating (same as AdmitONE/PreKindle)

---

## 8. Data Available Per Event

| Field | Source | Notes |
|-------|--------|-------|
| Event name, dates, status | API + JSON-LD | |
| Venue name, address, coords | API + JSON-LD | |
| Ticket types & prices | API (sales[]) + JSON-LD (offers[]) | JSON-LD has basic price, API has full detail |
| Availability/sold-out status | API (sale.state) | OPEN, QUEUED, CLOSED |
| Performer/lineup | API (lineups[]) + JSON-LD (performer[]) | |
| Event description | API | HTML formatted |
| Images/media | API (media[]) | Gallery array |
| Group/organizer info | API (group{}) | |
| Seating configuration | API (seatsIoConfiguration) | For reserved seating venues |
| Age restriction | API | |
| Resale enabled | API | Boolean |
| Delivery methods | API (deliveryConfiguration) | Shipping, digital, etc. |

---

## 9. Comparison with AdmitONE/PreKindle

| Feature | Tixr | AdmitONE | PreKindle |
|---------|------|----------|-----------|
| Bot Protection | DataDome (aggressive) | None/minimal | Queue-IT |
| API Access | Browser-only | Direct HTTP works | Browser-only |
| Search API | None public | GraphQL available | N/A |
| Event Discovery | Group-based only | City/category crawl | Single event |
| Seat Maps | seats.io (some events) | seats.io, Direct API, GraphQL | seats.io |
| Pricing Source | API + JSON-LD | API responses | HTML + seats.io |
| SSR | SEO-only (meta/JSON-LD) | Minimal | Minimal |
| Framework | Custom vanilla JS | Custom | Custom |
