# PreKindle Ticketing System - Technical Findings

## Technology Stack

| Component        | Technology                                      |
|------------------|-------------------------------------------------|
| Backend          | Apache Wicket (Java web framework)              |
| Session mgmt     | Server-side via `jsessionid` embedded in URLs   |
| Bot protection   | Queue-IT (`q.prekindle.com`)                    |
| Rendering        | 100% server-side (no client-side JSON/REST API) |
| AJAX framework   | Wicket AJAX (returns XML with HTML fragments)   |
| JS dependencies  | jQuery 1.12.4, Wicket AJAX library              |

---

## URL Structure

| Purpose          | URL Pattern                                                                  |
|------------------|------------------------------------------------------------------------------|
| Promo/event page | `https://www.prekindle.com/promo/id/{eventId}`                               |
| Event page (alt) | `https://www.prekindle.com/event/{numericId}-{slug}`                         |
| Checkout entry   | `https://www.prekindle.com/checkout/id/{eventId}`                            |
| Ticket selection | `https://www.prekindle.com/find-tickets/id/{eventId};jsessionid={session}?0` |

The `eventId` is a signed 64-bit integer (e.g., `-2852852648630450083`).

---

## Page Flow

```
Promo Page (/promo/id/{eventId})
    |
    | "GET TICKETS" click
    v
Queue-IT (q.prekindle.com)  <-- bot protection / queue
    |
    | redirect with jsessionid
    v
Seatmap Page (/find-tickets/id/{eventId};jsessionid={session}?0)
    |
    | Click on available section (Wicket AJAX)
    v
Seat Selection (same page, AJAX updates DOM)
    |
    | Click individual seat(s)
    v
Delivery Method + Continue
```

---

## Seatmap Architecture

### Background Image
- The visual seating chart is a **base64-encoded PNG** embedded in an SVG `<image>` element.
- Seat labels (A1, A2, B1, B2, etc.) are **baked into this PNG image only**.
- They do NOT appear in the HTML/DOM.

### SVG Overlay (Section-Level)
- Transparent shapes (rect/path/polygon) overlaid on the PNG.
- Each shape has a `wicketpath` attribute and unique `id`.
- Shapes represent clickable sections of the venue.

### Section Availability (from SVG overlay colors)

| Fill Color  | Opacity | Meaning         |
|-------------|---------|-----------------|
| `#1e96ff`   | `0`     | **Available**   |
| `#bbbbbb`   | `.5`    | **Sold Out**    |

### Seat Availability (from AJAX response after clicking section)

| Background Color | Meaning               |
|------------------|-----------------------|
| `#39E604`        | **Available** (green) |
| `#cccccc`        | **Unavailable** (grey)|
| `white`          | **Spacer** (no seat)  |

---

## Wicket AJAX Interaction

### Section Click AJAX URL Pattern
```
GET ./{eventId};jsessionid={session}?0-1.IBehaviorListener.1-howManyPanel-seatmapContainer-seatmap-wrapper-svg-{shapeType}-{index}
```

Where:
- `{shapeType}` = `rectangles`, `paths`, or `polygons`
- `{index}` = 0-based index within that shape type
- `IBehaviorListener.1` = click event (`.0` = touchend)

### Response Format
- Content-Type: `text/xml;charset=UTF-8`
- Body: `<ajax-response><component id="..."><![CDATA[...HTML...]]></component>...</ajax-response>`

### Available Section Response
Returns HTML fragment for `howManyPanel` containing:
- Section name (e.g., `<div class="seatingsectionname">Balcony 700</div>`)
- Price levels (e.g., `$65.99 Total Price` with color codes)
- Seat grid: rows of `<a class="seat">` elements with background-color indicating availability
- Wicket AJAX bindings for individual seat click events

### Unavailable Section Response
Triggers a **JavaScript alert**: `"{Section Name} is SOLD OUT."`
- The section name IS revealed in the alert message.
- **No seat-level data is returned** for sold out sections.
- After alert dismissal, the seatmap stays on the same view.

---

## Sold Out Sections - What We Can/Cannot Get

### CAN get:
- Section name (from the alert: `"Balcony 600 is SOLD OUT."`)
- The fact that it's completely sold out

### CANNOT get:
- Individual seat breakdown for sold-out sections
- Seat names/labels (only in background PNG for all sections)
- Price that WAS charged for sold-out sections

---

## Available Section Data Structure

When clicking an available section, the response contains:

```
Section: "Balcony 700"
Price Levels:
  - $65.99 Total Price (color: #39E604)
  - Unavailable (color: #cccccc)

Rows:
  Row 0: [unavail, unavail, unavail, unavail, spacer, spacer]
  Row 1: [AVAIL,   unavail, spacer,  spacer,  unavail, AVAIL]
  Row 2: [AVAIL,   unavail, unavail, unavail, unavail, AVAIL]

Summary: 4 available, 10 unavailable, 4 spacers
```

---

## Key Technical Observations

1. **No REST API exists** - all data transfer uses Wicket AJAX (XML + HTML fragments).
2. **Session is mandatory** - `jsessionid` is required for all Wicket AJAX calls.
3. **Component IDs change per session** - IDs like `id1`, `id2` are session-specific.
   However, `wicketpath` attributes are stable and predictable.
4. **Stateful server** - the server tracks which section you've selected.
   Must click BACK before selecting a different section.
5. **Queue-IT** must be passed before reaching the ticket page.
6. **Seat names are NOT in HTML** - the `<span>` inside each `<a class="seat">` is empty.
   Labels only exist in the PNG background image.
7. **Wicket uses jQuery 1.12.4** for AJAX transport.

---

## Scraping Strategy (Playwright/Browser)

### Recommended approach:

1. **Navigate** to promo page `/promo/id/{eventId}`.
2. **Click "GET TICKETS"** - browser handles Queue-IT automatically.
3. **Parse SVG overlay** - extract all rect/path/polygon elements.
   - Map each shape's fill/opacity to available vs. sold out.
4. **For each section** (iterate through SVG shapes):
   - **If available** (`fill=#1e96ff`): click it, parse seat grid from DOM,
     extract section name + price + available/unavailable seat counts.
     Click BACK to return to seatmap.
   - **If sold out** (`fill=#bbbbbb`): click it, capture the alert dialog
     message to get section name. Dismiss alert.
5. **Aggregate** all sections into a structured result.

### Data we can collect per event:
- Event name, date, venue, age limit
- Per section: name, price, total seats, available seats, sold-out status
- Overall: total available, total sold out, percentage sold

### Limitations:
- Cannot get individual seat names (A1, B2, etc.) - only counts
- Cannot get seat details for sold-out sections
- Must handle Queue-IT (browser-based approach required)
- One event at a time (session-bound, sequential section clicks)

---

## Sample Event Tested

- **Event**: Fabio Porchat - Historias do Porchat
- **Venue**: Granada Theater, Dallas TX
- **Date**: Friday, February 6, 2026
- **Event ID**: `-2852852648630450083`
- **Promo URL**: `https://www.prekindle.com/promo/id/-2852852648630450083`
- **Sections observed**: 25 total (mix of rect, path, polygon shapes)
- **Section tested**: Balcony 700 - $65.99, 4 available out of 14 non-spacer seats
- **Sold out tested**: Balcony 600, Floor 401

---

## Cookie/Session Requirements

| Cookie/Header                        | Purpose                     |
|--------------------------------------|-----------------------------|
| `jsessionid` (in URL)                | Wicket server session       |
| `QueueITAccepted-SDFrts345E-V3_...`  | Queue-IT bypass token       |
| `q_i_t_u_i`                          | Queue-IT tracking           |
| `_ga`, `_gcl_au`, `_fbp`            | Analytics (not required)    |
