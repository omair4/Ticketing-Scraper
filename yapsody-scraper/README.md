# Yapsody Scraper

Scrapes seat inventory from `concerts.yapsody.com` venues. Uses seats.io (`cdn-na.seatsio.net`) with pricing from the Yapsody `pricing_info` REST API.

---

## Setup

```bash
cd yapsody-scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Local credentials

Edit `local_creds.py` with your DB and proxy info. Leave proxy fields as `None` to run without a proxy (works fine for local testing):

```python
def get_db_info():
    return "localhost", "root", "your_db_password"

def get_proxy_info():
    return None, None, None, None  # host, port, username, password
```

> `local_creds.py` is gitignored — never commit credentials.

---

## Running locally (`test_local.py`)

All commands print to stdout. No DB writes.

```bash
python test_local.py discover
python test_local.py determine <venue_code> <event_id>
python test_local.py inventory <workspace_key> <event_key> <chart_key> '<pricing_json>'
python test_local.py full <venue_code> <event_id>
```

### `discover` — scrape concerts.yapsody.com

```bash
python test_local.py discover
```

Fetches the concerts.yapsody.com homepage, parses all event cards, and prints a summary classified as `yapsody_seatsio` (has seatsio seat map) or `external_redirect` (links to holdmyticket.com etc).

---

### `determine` — extract keys + pricing from booking page

```bash
python test_local.py determine sycuancasino 852239
python test_local.py determine dakotamagiccasino 863499
```

1. Fetches the event index page (httpx) to find `performance_id`
2. Launches a headless Playwright browser, navigates to the booking page
3. Intercepts the `pricing_info` XHR response (AngularJS venues) or extracts from DOM (Next.js venues)
4. Intercepts seatsio CDN URLs to extract `workspace_key` and `chart_key`
5. Prints all extracted values

Expected output:
```
performance_id: 1234567
event_key:      Performance-1234567
workspace_key:  639668b2-6053-4001-9db0-4b820a4097f8
chart_key:      abc123...
pricing_list (3 categories):
[{'category': 1, 'ticketTypes': [{'label': 'General', 'price': 45.0, ...}]}, ...]
```

---

### `inventory` — fetch seat inventory from seatsio CDN

```bash
python test_local.py inventory \
  639668b2-6053-4001-9db0-4b820a4097f8 \
  Performance-1234567 \
  abc123chartkey \
  '[{"category": 1, "ticketTypes": [{"label": "General", "price": 45.0}]}]'
```

Hits the seatsio CDN directly (no browser needed): fetches `rendering-info`, `object-statuses`, and `charts/published/9`, decodes hex-encoded responses, and prints section counts + a sample seat DataFrame.

---

### `full` — determine + inventory in one step

```bash
python test_local.py full sycuancasino 852239
python test_local.py full dakotamagiccasino 863499
```

Runs `determine` then immediately feeds the extracted keys into `inventory`. The most convenient command for end-to-end testing.

---

## Lambda functions

Four Lambda functions run in sequence via Step Functions.

### `yapsody_show_discovery.py` — Lambda 1

**Trigger:** daily schedule
**Input:** `{}`

Scrapes `concerts.yapsody.com` (single HTML page) and populates `event_discovery`.

1. `GET https://concerts.yapsody.com/` with httpx
2. BeautifulSoup parses event card `<a>` tags
3. For each link: extract `event_id` from URL path, `venue_code` from subdomain, event name + venue + date from card HTML
4. Classifies URL as `yapsody_seatsio` or `external_redirect`
5. Upserts `yapsody_seatsio` events into `event_discovery` (`ON DUPLICATE KEY UPDATE`)

**Output:** `{ num_events: N }`

---

### `yapsody_determine_scraper.py` — Lambda 2

**Trigger:** per event, after discovery (Step Function Map)
**Input:** `{ event_id, venue_code, booking_url }`

Navigates the venue booking page with Playwright and extracts the seatsio config + pricing.

1. `GET {venue}.yapsody.com/event/index/{event_id}/` (httpx) — parse HTML to find `performance_id` from `Get Tickets` href
2. Construct booking URL: `{venue}.yapsody.com/event/book/{event_id}/{performance_id}`
3. Launch Playwright browser with `page.on("response")` interceptor:
   - Capture `*/pricing_info` XHR → `price.seats_io_categories` → `pricing_list`
   - Capture `cdn-na.seatsio.net` CDN URLs → parse `workspace_key` + `chart_key` from URL path
4. Fallback for Next.js venues (SSR pricing): extract `seats_io_categories` from DOM JSON
5. Writes `performance_id`, `event_key` (`Performance-{perf_id}`), `workspace_key`, `chart_key`, `pricing_list`, `ticket_url` back into `event_discovery`

**Output:** `{ event_id, scraper_type, workspace_key, event_key, chart_key, pricing_list, ticket_url }`

---

### `yapsody_provide_inventory_params_list.py` — Lambda 3

**Trigger:** before inventory run (Step Function input generator)
**Input:** `{ time_frame?: 0 }`

Reads all active, fully-configured `yapsody_seatsio` events from `event_discovery` (must have `workspace_key`, `event_key`, `chart_key`, and `pricing_list` set) and returns one payload per event for the inventory Map state.

**Output:** `{ events: [{ event_id, time_frame }, ...], total: N }`

---

### `event_inventory_main.py` — Lambda 4

**Trigger:** Step Function Map (one invocation per event)
**Input (inventory mode):** `{ event_id, time_frame?: 0 }`
**Input (existence check mode):** `{ mode: "existence_check", eId, wkspc_key, evt_key, chart_key, pricelist, e_name, v_name, date, scraper_type, listing_info }`

**Inventory mode:**
1. Reads full event record from `event_discovery` by `event_id`
2. Calls `event_inventory.seatsio.scrape()` → seat DataFrame
3. Deletes old rows for this event from `event_inventory`
4. Bulk-inserts new seat rows
5. Inserts a run summary into `event_inventory_logs`

**Existence check mode:**
Runs the same scrape but instead of uploading returns `{ exists: bool }` based on section / price (±$15) / row (within 5 rows toward stage) / consecutive-run quantity rules.

**Output (inventory):** `{ rows: N, scraper_type, event_id }`
**Output (existence check):** `{ mode: "existence_check", exists: bool, runs, all_matching_seats, ... }`

---

### `event_inventory.seatsio.py` — shared module (not a Lambda)

Called by `event_inventory_main.py`. Handles all seatsio CDN fetching and seat extraction:

| API call | Encoding | Purpose |
|---|---|---|
| `rendering-info?event_key=Performance-{id}` | Plain JSON | Category definitions, objectCategories map, forSaleConfigs |
| `events/object-statuses?event_key=Performance-{id}` | Hex-encoded | Sparse availability (missing = available) |
| `charts/{chart_key}/published/9` | Hex-encoded | Seat layout + GA area capacities |

Pricing comes from `pricing_list` (captured in Lambda 2), not from rendering-info. Category keys in `pricing_list` map directly to `objectCategories` integer keys.

---

## Step Function pipeline

```
yapsody_show_discovery              (daily — populates event_discovery)
        ↓  (Map over newly discovered events)
yapsody_determine_scraper           (one per event — fills workspace_key, chart_key, pricing_list)
        ↓
yapsody_provide_inventory_params_list
        ↓  (Map)
event_inventory_main                (one per event — populates event_inventory)
```

---

## Files

| File | Purpose |
|---|---|
| `yapsody_show_discovery.py` | Lambda 1: scrape event listings |
| `yapsody_determine_scraper.py` | Lambda 2: Playwright browser, intercept pricing + seatsio keys |
| `yapsody_provide_inventory_params_list.py` | Lambda 3: build event list for Step Function Map |
| `event_inventory_main.py` | Lambda 4: orchestrate scrape + upload |
| `event_inventory.seatsio.py` | Module: seatsio CDN fetcher + seat DataFrame builder |
| `event_inventory.browser.py` | Module: async Playwright browser helper |
| `schema.sql` | MySQL schema |
| `local_creds.py` | Local credential stub (gitignored) |
| `test_local.py` | Local runner — no DB, prints to stdout |
| `requirements.txt` | Python dependencies |

---

## Pricing (key difference vs PreKindle)

Pricing does **not** come from seatsio `rendering-info`. It is captured from the Yapsody `pricing_info` REST API during the browser navigation step (Lambda 2):

```
GET https://api.yapsody.com/online/events/{event_id}/performances/{perf_id}/pricing_info
→ { price: { seats_io_categories: [{category: KEY, ticketTypes: [{label, price}]}] } }
```

The integer `category` key in `seats_io_categories` maps directly to the category key in `rendering_info.objectCategories[seat_label]`, so no text-matching is needed.

---

## Known venues

| Venue code | Seatsio workspace key |
|---|---|
| `sycuancasino` | `639668b2-6053-4001-9db0-4b820a4097f8` |
| `dakotamagiccasino` | `bfc51b93-19d6-4385-9c2a-c302a9d60a34` |
