# PreKindle Scraper — Overview

## What Is PreKindle?

PreKindle is a ticketing platform used primarily by mid-size venues and promoters across the US. Events range from concerts and comedy shows to festivals and private events. The platform handles ticket sales, seat selection, and event promotion through `prekindle.com` and `web.prekindle.com`.

## What This Module Does

The PreKindle scraper has two capabilities:

1. **Event Discovery** — Fetches all upcoming events from PreKindle's search API, returning structured data (title, date, venue, ticket URL, sold-out status) for every listed event.

2. **Event Ticket Scraping** — For a single event, navigates the purchase flow via browser automation to extract seat-level or ticket-category data including prices, availability, and venue details.

## How PreKindle Works (Technical Background)

### Server-Side Architecture

Unlike modern SPAs, PreKindle is built on **Apache Wicket** (Java), a server-side web framework. Pages are rendered entirely on the server. There are no client-side REST APIs or GraphQL endpoints for ticket data — everything comes as HTML.

The one exception is the **event search API** (`web.prekindle.com/api/event/search`), which returns JSON and powers the public event listing page.

### Ticket Scenarios

PreKindle events fall into three categories based on how seats/tickets are presented:

| Scenario | How It Works | Scraper Support |
|----------|-------------|-----------------|
| **seats.io** | Venue uses seats.io for interactive seat maps. Pricing is embedded in the page HTML. Seat availability comes from seats.io's CDN API (`cdn-na.seatsio.net`), hex-encoded. | Fully supported |
| **General Admission** | Simple HTML table with ticket categories, prices, and quantity dropdowns. No external APIs. | Fully supported |
| **SVG Seatmap** | PreKindle's own seatmap system: a base64-encoded PNG background image with transparent SVG overlays for clickable sections. Requires clicking each section individually via Wicket AJAX to get seat data. | Detected but **not supported** |

### Bot Protection

PreKindle uses **Queue-IT** (`q.prekindle.com`) as a bot protection / virtual waiting room between the promo page and the checkout page. The browser automation handles this automatically — Queue-IT passes through when using a real browser context.

### Event IDs

PreKindle event IDs are **signed 64-bit integers** (e.g., `-2852852648567343600`), unlike AdmitONE's MongoDB ObjectIds. These appear in URLs like `/promo/id/{eventId}`.

## Data Sources Per Scenario

### seats.io Events

| Data | Source | Method |
|------|--------|--------|
| Event info (title, date, venue, age limit) | Promo page HTML | BeautifulSoup parsing |
| Pricing (per category) | Checkout page HTML (`pricing` param in `seatsio.SeatingChart()` init) | Regex + JSON parsing |
| Seat chart structure (sections, rows, seats) | `cdn-na.seatsio.net/charts/published/...` | Hex-decoded JSON (captured via network listener) |
| Seat availability (per seat UUID) | `cdn-na.seatsio.net/object-statuses?event_key=...` | Hex-decoded JSON (captured via network listener) |
| seats.io workspace/event keys | Checkout page HTML | Regex extraction |

The hex encoding on seats.io responses uses a byte-offset cipher (same as AdmitONE). The `decoder.py` utility brute-forces all 256 offsets to find valid JSON.

### General Admission Events

| Data | Source | Method |
|------|--------|--------|
| Event info | Promo page HTML | BeautifulSoup parsing |
| Ticket categories + prices | Checkout page HTML table | BeautifulSoup parsing |
| Available quantity | `<select>` dropdown max value | BeautifulSoup parsing |
| Delivery methods | Checkout page labels | BeautifulSoup parsing |

No network APIs are involved — everything is server-rendered HTML.

### Event Discovery

| Data | Source | Method |
|------|--------|--------|
| All events (paginated) | `web.prekindle.com/api/event/search` | httpx GET with pagination |
| Event ID | Extracted from `ctaButtonProps.href` in response | URL parsing |

The search API is the only REST endpoint PreKindle exposes publicly.

## Output

### Single Event Output

Each scrape produces JSON and/or CSV files in the `output/` directory.

**JSON** includes:
- Full event info (name, venue, address, date, doors/show time, age limit, organizer)
- Seats array (for seats.io) or ticket categories array (for GA)
- Price range summary
- `api_urls` — the discovered seats.io API endpoints, enabling future **direct API queries** without browser automation
- `scrape_method` — whether data came from browser capture or HTML parsing
- `delivery_methods` — available delivery options

**CSV** uses a unified format with a `type` column to distinguish between `seat` and `ticket_category` rows.

### Discovery Output

JSON and CSV files listing all discovered events with metadata (title, date, venue, ticket URL, performers, sold-out status).

## Relationship to Other Modules

This project contains three independent scraper modules:

| Module | Target | Purpose |
|--------|--------|---------|
| `event_scraper/` | admitone.com | Scrape ticket/seat data for a single AdmitONE event |
| `website_scraper/` | admitone.com | Discover all events listed on AdmitONE |
| `prekindle_scraper/` | prekindle.com | Scrape ticket/seat data + discover events on PreKindle |

Each module has its own virtual environment, dependencies, and entry points. They share architectural patterns (Strategy/Factory, async Playwright, Pydantic models, structlog) but no code imports cross module boundaries — shared logic (like `decoder.py`) is copied to maintain independence.

## Limitations

- **SVG seatmap events** are detected but not scraped. These require clicking each section individually and parsing Wicket AJAX XML responses — significantly more complex than the other scenarios.
- **Seat labels** in SVG seatmap events are baked into background PNG images and cannot be extracted from HTML/DOM.
- **Sold-out section details** (for SVG seatmap) only reveal the section name via a JavaScript alert; no seat-level data is returned.
- **Queue-IT** adds latency (a few seconds) to each scrape. In high-traffic scenarios, it may impose a wait.
- **Session-bound state** — PreKindle's Wicket backend tracks server-side session state, so concurrent scraping of the same event from the same session is not possible.
