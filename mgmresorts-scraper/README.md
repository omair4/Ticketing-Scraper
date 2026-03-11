# MGM Resorts Scraper — Findings & Implementation Plan

## Overview

MGM Resorts uses a **GraphQL API** at `https://api.mgmresorts.com/graphql-next` for all
show/event catalog and ticket inventory. Access requires an **anonymous bearer token**
obtained from a public endpoint (no login needed).

---

## Architecture Discovery

### Tech Stack
- Frontend: Next.js (Module Federation, multiple micro-frontends)
- API: Apollo GraphQL at `https://api.mgmresorts.com/graphql-next`
- CMS: ContentStack GraphQL at `https://graphql.contentstack.com/stacks/blt4d71e9534fa5b5bf/?environment=production` (static content / descriptions only)
- Auth: JWT anonymous token from `https://identityapi.mgmresorts.com/identity/authorization/v2/anon/user/token`

### Properties (Sub-domains)
Each MGM property is a subdomain, e.g.:
- `mgmgrand.mgmresorts.com`
- `bellagio.mgmresorts.com`
- `parkmgm.mgmresorts.com`
- `mandalaybay.mgmresorts.com`
- `luxor.mgmresorts.com`
- `newyorknewyork.mgmresorts.com`
- `excalibur.mgmresorts.com`
- etc.

Each has a **propertyId** UUID used as a GraphQL variable (e.g. MGM Grand = `66964e2b-2550-4476-84c3-1a4c0c5c067f`).

---

## Scenarios

### Scenario 1: Internal MGM Booking (`mgm_internal`)
**Trigger**: Show detail page links to `{property}.mgmresorts.com/book-show/{show-slug}`

**Inventory API**: Fully available via GraphQL — seat-level data with section/row/seat, price codes, ADA flags.

**Examples**:
- `https://mgmgrand.mgmresorts.com/book-show/ka-by-cirque-du-soleil`
- `https://bellagio.mgmresorts.com/book-show/o-by-cirque-du-soleil`
- `https://luxor.mgmresorts.com/book-show/fantasy`
- `https://mandalaybay.mgmresorts.com/book-show/michael-jackson-one`

### Scenario 2: External / TicketMaster (`external_redirect`)
**Trigger**: Show detail page links to `ticketmaster.com` or other external vendor.

**Examples**:
- Sammy Hagar residency → `https://www.ticketmaster.com/artist/1126196?venueId=376930`
- Some concerts/residencies

**Inventory**: Not available via MGM API — requires separate TicketMaster scraper or is out of scope.

---

## Authentication Flow

```
GET https://identityapi.mgmresorts.com/identity/authorization/v2/anon/user/token
→ Returns: { token: "Bearer eyJ..." }

All subsequent GraphQL calls:
POST https://api.mgmresorts.com/graphql-next
Headers:
  Authorization: Bearer <token>
  Content-Type: application/json
```

Token is short-lived (~15 min). Must be refreshed. The browser auto-refreshes it.

---

## GraphQL Query Catalog

### Show Catalog (Discovery)

#### `GetShowProperties` — All shows across all properties
```graphql
query GetShowProperties {
  getShows {
    id          # show slug, e.g. "ka-by-cirque-du-soleil"
    propertyId  # UUID
  }
}
```
→ Returns all internal shows. **This is the show catalog entry point.**

#### `GetShow` — Show details
```graphql
query GetShow($showId: String, $eventId: String) {
  show(id: $showId, eventId: $eventId) {
    id
    name
    darkDates
    descriptions { short long overview appCard }
    images { tile { url } overview { url } }
    property {
      id name
      location { address latitude longitude }
    }
    seasons { id displayName }
    productCategory
    ageRequirements
    allDayEvent
    arenaTheater { name }
    # ... more fields
  }
}
```
Variables: `{ "showId": "ka-by-cirque-du-soleil" }`

---

### Event Schedule (Show → List of Events with Dates)

#### `GetShowEventsView` — Paginated event list with lowest prices
```graphql
query GetShowEventsView(
  $endDate: String, $limit: Int!, $offset: Int!,
  $propertyId: String!, $showId: String!, $startDate: String!
) {
  showBooking {
    eventsViewForShow(queryParams: {
      endDate: $endDate, limit: $limit, offset: $offset,
      propertyId: $propertyId, showId: $showId, startDate: $startDate
    }) {
      events {
        eventDate    # "2026-03-14"
        eventTime    # "04:30 PM"
        eventId      # "Event_4801605"
        eventCode    # "KAG0314M"
        seasonId     # "Season_4730022"
        offerAvailable
        lowestPrice  # 68 (includes service fee)
      }
      lastUpdated
    }
  }
}
```
Variables: `{ "showId": "ka-by-cirque-du-soleil", "propertyId": "<uuid>", "startDate": "2026-03-11", "limit": 7, "offset": 0 }`

Paginate by incrementing `offset` by `limit` until empty result.

#### `GetCalendarView` — Monthly availability calendar
```graphql
query GetCalendarView(
  $showId: String, $propertyId: String, $seasonId: String,
  $programId: String, $startDate: String!, $endDate: String!,
  $redemptionCode: String
) {
  showBooking {
    calendarView(queryParams: { ... }) {
      date          # "2026-03-14"
      available     # true/false
      offerAvailable
      lowestPrice
    }
  }
}
```

---

### Inventory (Seat-Level)

#### `GetShowEvent` — Event metadata
```graphql
query GetShowEvent($showEventId: String!) {
  getShowEvent(id: $showEventId) {
    id
    eventDate
    eventTime
    seasonId
  }
}
```
Variables: `{ "showEventId": "Event_4801603" }`

#### `GetSeatsAvailability` — Full seat-level inventory ★ KEY QUERY
```graphql
query GetSeatsAvailability(
  $showEventId: String!, $programId: String,
  $numTicket: Int, $sections: String, $priceCodes: String,
  $redemptionCode: String, $numAdaTickets: Int, $numCompanionTickets: Int,
  $offerSeatsOnly: Boolean, $propertyId: String
) {
  showBooking {
    seatsAvailability(queryParams: { showEventId: $showEventId, ... }) {
      limitedAvailability
      comp
      hdePackage
      seats {           # array of sections
        count           # available seat count in section
        startingPrice
        name            # section name e.g. "101"
        seatRows {
          name          # row letter e.g. "P", "R", "C"
          seats {
            firstSeat       # e.g. 1
            lastSeat        # e.g. 4
            numSeats        # 4
            seatIncrement   # 1
            priceCode       # "DN05"
            basePrice       # 107.70
            discountedPrice # 107.70
            priceLevel      # 4
            holdClass       # "DIST-OPEN", "ADA", "ADA-COMPANION"
            ada             # bool
            adaCompanion    # bool
            vip             # bool
          }
        }
      }
      prices {          # price code definitions
        code            # "DN05"
        description
        price {
          basePrice
          discountedPrice
          comp
          basePriceWithServiceFee
          discountedPriceWithServiceFee
          serviceFee
        }
        minTickets
        maxTickets
        totalAvailableSeats
        ticketTypeCode
        ticketTypeDescription
        offerApplicable
      }
      seatRowsSortedByPrice {
        name
        sectionName
        startingPrice
        numSeats
      }
    }
  }
}
```
Variables: `{ "showEventId": "Event_4801603", "propertyId": "<uuid>", "offerSeatsOnly": false }`

---

### Offers / Programs

#### `GetOffers` — Available discount programs for a show
```graphql
query GetOffers($propertyId: String, $showId: String) {
  showBooking {
    offers(queryParams: { propertyId: $propertyId, showId: $showId }) {
      programDetails {
        id
        name
        active
        minTickets
        maxTickets
        startingPrice
        isComp
        type
        myvegas
        events
      }
    }
  }
}
```

---

## Data Model (Seat Record)

Each row in `GetSeatsAvailability.seats[].seatRows[].seats[]` represents a **range** of contiguous seats:

```
section_name  = seats[].name            # "101"
row           = seatRows[].name         # "P"
first_seat    = seats[].firstSeat       # 1
last_seat     = seats[].lastSeat        # 4
num_seats     = seats[].numSeats        # 4
price_code    = seats[].priceCode       # "DN05"
base_price    = seats[].basePrice       # 107.70
discounted    = seats[].discountedPrice # 107.70
ada           = seats[].ada             # false
ada_companion = seats[].adaCompanion    # false
vip           = seats[].vip             # false
hold_class    = seats[].holdClass       # "DIST-OPEN"
```

**Expand ranges**: seat numbers from `firstSeat` to `lastSeat` step `seatIncrement` = individual seats.

**Total price** = `discountedPrice` (already includes service fee based on `basePriceWithServiceFee` in prices[]).
Note: `lowestPrice` shown on event list already includes service fee.

---

## Implementation Plan

### Phase 1: Show Discovery (`mgmresorts_show_discovery.py`)

```
GET /identity/authorization/v2/anon/user/token  → bearer token

POST /graphql-next  GetShowProperties
  → list of { id (show_slug), propertyId }

For each show_slug:
  POST /graphql-next  GetShow(showId=slug)
  → name, propertyId, venue details, darkDates, images

Filter:
  - productCategory == "show" (exclude non-ticketed experiences)
  - Has internal booking URL pattern: book-show/{slug}

Output: event_discovery table rows with:
  show_id, property_id, show_name, venue_name, venue_address,
  booking_url, scraper_type="mgm_internal"
  OR scraper_type="external_redirect" for TicketMaster shows
```

### Phase 2: Event Schedule (`mgmresorts_event_schedule.py`)

```
For each show in event_discovery:
  POST /graphql-next  GetShowEventsView(showId, propertyId, startDate=today, limit=50, offset=0)
  Paginate until empty
  → list of { eventDate, eventTime, eventId, eventCode, lowestPrice }

Output: event_schedule table rows with:
  show_id, event_id, event_date, event_time, event_code, lowest_price
```

### Phase 3: Seat Inventory (`event_inventory.direct_api.py`)

```
Input: { show_id, event_id, property_id, event_name, venue_name, event_datetime }

POST /graphql-next  GetSeatsAvailability(showEventId=event_id, propertyId)

For each section in seatsAvailability.seats[]:
  For each seatRow in section.seatRows[]:
    For each seatRange in seatRow.seats[]:
      Expand: seat_number = firstSeat..lastSeat step seatIncrement
      Emit row per seat:
        eventId, event_name, venue_name, event_datetime,
        objectLabelOrUuid = "{section}-{row}-{seat_number}",
        section_name, row, seat_number,
        total_price = discountedPrice,
        seat_pattern = detect_pattern(firstSeat, lastSeat, seatIncrement),
        isRestrictedView = False,
        category = priceCode,
        ticket_type = ticketTypeDescription (from prices[]),
        ada = ada or adaCompanion
```

**seat_pattern detection**:
- `seatIncrement == 1` → `"Consecutive"`
- `seatIncrement == 2 and firstSeat % 2 == 0` → `"Even"`
- `seatIncrement == 2 and firstSeat % 2 == 1` → `"Odd"`

### Phase 4: Lambda Entry Points

```
mgmresorts_show_discovery.py        → Lambda: discovery (runs daily)
mgmresorts_event_schedule.py        → Lambda: schedule refresh (runs daily per show)
event_inventory_main.py             → Lambda: inventory snapshot (runs on demand)
```

---

## Key Differences vs PreKindle

| | PreKindle | MGM Resorts |
|---|---|---|
| Auth | None (public) | Anonymous JWT token |
| Show catalog | REST search API | GraphQL `GetShowProperties` |
| Event schedule | REST event/search | GraphQL `GetShowEventsView` (paginated) |
| Inventory | seats.io (hex-encoded) | GraphQL `GetSeatsAvailability` (plain JSON) |
| Seat format | Expand from chart | Seat ranges (firstSeat→lastSeat) |
| Pricing | HTML + seats.io | Inline in seat ranges + prices[] lookup |
| External shows | Queue-IT protected | TicketMaster redirect |
| Bot protection | Queue-IT (browser needed) | None detected (direct API works) |

---

## Anti-Bot / Rate Limiting

- No CAPTCHA or Queue-IT detected for the GraphQL API
- Anonymous token endpoint is public
- Headers to send: standard browser headers + `Authorization: Bearer <token>`
- Recommend adding proxy rotation for production scale

---

## Database Schema (follows prekindle pattern)

### `event_discovery`
```sql
show_id         VARCHAR(255)  -- "ka-by-cirque-du-soleil"
property_id     VARCHAR(36)   -- UUID
show_name       VARCHAR(255)
venue_name      VARCHAR(255)
venue_address   VARCHAR(500)
booking_url     VARCHAR(500)  -- "{property}.mgmresorts.com/book-show/{slug}"
scraper_type    ENUM('mgm_internal', 'external_redirect')
is_active       BOOL
```

### `event_schedule`
```sql
show_id         VARCHAR(255)
event_id        VARCHAR(50)   -- "Event_4801603"
event_code      VARCHAR(20)   -- "KAG0314M"
season_id       VARCHAR(50)   -- "Season_4730022"
event_date      DATE
event_time      VARCHAR(20)
lowest_price    DECIMAL(8,2)
offer_available BOOL
```

### `event_inventory` (same as prekindle)
```sql
eventId, event_name, venue_name, event_datetime,
objectLabelOrUuid, section_name, row, seat_number,
total_price, seat_pattern, isRestrictedView,
category, ticket_type, time_frame
```

### `event_inventory_logs` (same as prekindle)
```sql
eventId, event_name, venue_name, event_datetime,
ticket_url, scraper_type, time_frame,
statuscode, total_stock, stock_by_section, error_message
```
