# tix4.com/shows — Phase 1 Research Findings (v3 FINAL — full revalidation)

Date: 2026-04-16
Scope: `https://tix4.com/shows` only. No other parts of the site.
Validated against: **all 129 shows** on /shows (119 probed successfully, 9 no showtimes, 1 server error).

---

## 1. Tech Profile

| Aspect | Value |
|---|---|
| Stack | Laravel/PHP (CSRF `_token`, Blade templates) |
| Rendering | **100% server-rendered**. All scraping = `httpx` + BeautifulSoup. No Playwright needed. |
| Anti-bot | None. Cloudflare CDN pass-through. No JS challenge, no TLS fingerprinting, no rate-limit at low QPS. |
| Auth | None. All pages public to anonymous users. |
| Date window | `/event/<slug>` exposes today + 15 days via `#event-calendar-model` JSON. Not extendable. |

---

## 2. Pipeline Map (AGENTS.md flat Lambda-pipeline)

```
discovery                       -> GET /shows (129 cards)
determine_scraper               -> GET /event/<slug> + GET <first_buy_url> -> classify scenario
provide_schedule_params_list    -> per show, yield {show_slug}
event_schedule                  -> GET /event/<slug> (parse #event-calendar-model JSON)
provide_inventory_params_list   -> per schedule row, yield {scraper_type, buy_url, ...}
event_inventory_main            -> GET <buy_url> -> dispatch to 1 of 6 inventory modules
```

---

## 3. Discovery — `/shows`

GET `https://tix4.com/shows`. Grid: `.row.w-100.c-container` -> `.col-lg-4.col-xl-3` cards.

Per-show: `slug` (from `a[href^="/event/"]`), `title` (`.event-title`), `category` (`.event-magazine-title`), `starting_price_usd` (`.magazine-discounted-price`).

129 unique shows, 7 categories. No pagination. Dedupe by `href` (carousel clones duplicates).

---

## 4. Schedule — `/event/<slug>`

Source: `<script type="application/json" id="event-calendar-model">`. JSON array, today + 15 days.

Per entry: `date_key`, `times[].label`, `times[].buy_url`, `times[].remaining_tickets` (usually null).

From `buy_url` parse: `event_id_token` (path after `/buy/<slug>/`), `showtime_id` (query param, absent on Native GA), `show_time` (URL-encoded datetime).

---

## 5. Six Buy-Page Scenarios

### 5.0 Classifier (DOM-shape based, validated against all 129 shows)

```python
def classify(soup):
    has_svg = soup.select_one('input[name="svg_seats_exists"][value="true"]') is not None

    # Native Seatmap: circles carry data-totalprice
    if has_svg and soup.select('svg circle[data-totalprice]'):
        return "native_seatmap"          # providers: 6, 11

    # External Seatmap: div.seat-item with data-total-price
    if has_svg and soup.select('div.seat-item[data-total-price]'):
        return "external_seatmap"        # provider: 12

    # Native GA: input[name="ticket_id"] radios
    if soup.select_one('input[name="ticket_id"]'):
        return "native_ga"              # provider: "" (empty)

    # External GA: div[data-ticket-type][data-name][data-price]
    if soup.select('[data-ticket-type][data-name][data-price]'):
        return "external_ga"            # provider: 12

    # Native GA Provider-6: is_ga hidden inputs + .platinum-price
    if soup.select('input[name="is_ga"][value="1"]'):
        return "native_ga_provider6"    # provider: 6

    # JS-only Seatmap: SVG exists but no pricing in raw HTML
    if has_svg:
        return "js_only_seatmap"        # provider: 17

    return "UNKNOWN"
```

### 5.0.1 Distribution (129 shows, 119 probed)

| Scenario | Count | Provider IDs |
|---|---|---|
| external_seatmap | 59 | 12 |
| native_ga | 33 | "" (empty) |
| native_seatmap | 16 | 6, 11 |
| external_ga | 7 | 12 |
| native_ga_provider6 | 2 | 6 |
| js_only_seatmap | 1 | 17 |
| Empty/sold-out (no inventory) | 1 | 6 |
| No showtimes in window | 9 | — |
| Server error (500) | 1 | — |

---

### 5.1 Native Seatmap (16 shows — providers 6, 11)

Shows: Blue Man Group, David Copperfield, O by Cirque, Mystere, Carrot Top, etc.

Per-seat data on `<svg circle>`:

| Field | Source |
|---|---|
| seat_id | `@id` (e.g. `102-B-1`) |
| section_name | first dash-token of id (e.g. `102`) |
| row | second dash-token |
| seat_number | third dash-token |
| category | `@data-description` (e.g. `CN03`, sometimes empty) |
| total_price | `@data-totalprice` (all-in dollars, matches legend) |
| status | `data-disabled="false"` = available; `"true"` = sold |

Sold circles: only `id`, `cx`, `cy`, `data-disabled="true"`. No pricing.
Available circles: full pricing attrs including `data-totalprice`, `data-oldtotalprice`, `data-fee`, `data-discount`, `data-promocode`.

Legend validation: `#price-map .price-legend-item[data-price]` gives color->price tiers that match circle `data-totalprice` values exactly.

---

### 5.2 External Seatmap (59 shows — provider 12)

Shows: Penn and Teller, most Cirque shows, many production/tribute/comedy shows.

Per-seat data on `<div class="seat-item">`:

| Field | Source |
|---|---|
| seat_key | `@data-seat-key` (e.g. `SEC6-L-3`, matches SVG circle `@id`) |
| section_name | `@data-section` (e.g. `SEC6`) |
| row | `@data-row` |
| seat_number | `@data-number` |
| total_price | `@data-total-price` (all-in dollars, matches legend) |
| net_price | `@data-price` |
| fee | `@data-process-fee` |
| ticket_type_code | `@data-ticket-type` |

Only available seats have `seat-item` divs. Trust `seat-item` count as inventory truth (more than `circle.is-available` due to filtered/ADA seats).

Legend validation: `#price-map .price-legend-item[data-price]` matches `data-total-price` values exactly.

---

### 5.3 Native GA (33 shows — provider empty)

Shows: LA Comedy Club, Nathan Burton, V Variety, House of Magic, Eddie Griffin, etc.

Per-category data on `<input name="ticket_id">`:

| Field | Source |
|---|---|
| ticket_id | `@value` |
| category | nearest `.ticket-type-title` text |
| total_price | `@data-fullprice` (= `data-price` + Fees, verified: 19.78 + 12.22 = 32.00 = fullprice) |
| net_price | `@data-price` |
| fee | parsed from `.price-details` text `"$19.78 + Fees: $12.22"` |
| qty_max | `@data-max` |

**Pricing note:** `data-price` is NET, NOT the customer total. `data-fullprice` = net + fee = what the customer pays.

---

### 5.4 External GA (7 shows — provider 12)

Shows: Comedy Cellar, X Rocks, etc.

Per-category data on `<div class="tickets-container" data-ticket-type data-name data-price>`:

| Field | Source |
|---|---|
| category | `@data-name` (e.g. `VIP`, `GA`) |
| section_name | `@data-section` |
| net_price | `@data-price` (NET only — NOT the customer total) |
| total_price | `span.platinum-price` text inside container (e.g. `$ 71`) — this is the all-in total |
| ticket_type_code | `@data-ticket-type` |

**Pricing note:** `data-price` is NET (e.g. $54.08). The displayed `span.platinum-price` text (e.g. `$71`) is the all-in total. Fee = display - net.

---

### 5.5 Native GA Provider-6 (2 shows — provider 6)

Shows: Brad Garrett's Comedy Club, Tape Face.

Page states "This event is using All-In Pricing". No `svg_seats_exists`, no `input[name="ticket_id"]`, no `[data-ticket-type][data-name]`.

Per-category data: `.tickets-container` + adjacent `form.form-horizontal` with hidden inputs:

| Field | Source |
|---|---|
| category | `.seat-section` text (e.g. `Adult`) + `.tickets-container` description text |
| total_price | `span.platinum-price` text (e.g. `$99.00`) — explicitly all-in |
| price_level_id | `input[name="price_level_id"]` in adjacent form |
| promo_code | `input[name="promo_code"]` in adjacent form |

Containers and forms alternate in document order. Pair by index.

---

### 5.6 JS-Only Seatmap (1 show — provider 17)

Show: Atomic Saloon.

SVG circles exist (214 total, 37 `is-available`) but ZERO pricing data in raw HTML. Only section-level "Starting at $X" text in `.tickets-container`.

**Decision: emit section-level rows with "Starting at" price.** Cannot get per-seat pricing without Playwright.

---

## 6. Pricing Strategy (all-in total, unified)

Every scenario uses the **customer-facing all-in total** as `total_price`:

| Scenario | total_price source | Verified? |
|---|---|---|
| Native Seatmap | `circle @data-totalprice` | Yes — matches legend |
| External Seatmap | `div.seat-item @data-total-price` | Yes — matches legend |
| Native GA | `input @data-fullprice` | Yes — equals net + parsed fee |
| External GA | `span.platinum-price` text | Yes — displayed total, net in data-price |
| Native GA Provider-6 | `span.platinum-price` text | Yes — page states "All-In Pricing" |
| JS-Only Seatmap | parsed from "Starting at $X" text | Section-level only |

---

## 7. Schema (yapsody/mgm convention)

`event_discovery`: show_slug PK, title, category, starting_price_usd, scraper_type ENUM, external_api_provider_id, is_active

`event_schedule`: show_slug FK, event_id_token, showtime_id, event_datetime, buy_url_path, scraped_at

`event_inventory`: section_name, row, seat_number, objectLabelOrUuid, total_price, status, seat_pattern, category, ticket_type, isRestrictedView, time_frame (matching yapsody/mgm columns)

`event_inventory_logs`: run_id, schedule_id, statuscode, total_stock, stock_by_section, error_message

---

## 8. Edge Cases

1. Empty `event-calendar-model` = no showtimes in window. Zero schedule rows, no error.
2. Native Seatmap: `class="na"` does NOT mean sold. Use `data-disabled="true"` + absence of `data-totalprice`.
3. External Seatmap: more `circle.is-available` than `div.seat-item`. Trust `seat-item` count.
4. Native GA: `data-price` is NET not total. Use `data-fullprice` for total.
5. External GA: `data-price` is NET not total. Parse `span.platinum-price` for total.
6. JS-Only Seatmap (Atomic Saloon): section-level pricing only.
7. Sold-out / add-on shows (Carrot Top M&G): no inventory elements. Log and skip.
8. Shows with no buy_url (9 shows): no upcoming performances. Skip.
9. Server errors (Marc Anthony 500): log and skip.
10. Dedupe discovery cards by href (carousel clones).
