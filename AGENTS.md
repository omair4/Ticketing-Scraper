# AGENTS.md — Ground Rules for Building New Website Scrapers

These rules apply whenever we start working on a new ticketing website scraper in this repo.

---

## Phase 1: Research — Thorough, Playwright-Driven, No Assumptions

Before writing ANY scraper code, perform a deep investigation of the target website using the **Playwright MCP** tools. Do NOT assume anything about how the site works. Every finding must come from actually browsing the site.

### 1.1 Research Checklist (must complete ALL)

1. **Navigate the site** using Playwright MCP (`browser_navigate`, `browser_snapshot`, `browser_click`):
   - Homepage / event listing pages
   - Individual event/show pages
   - Ticket purchase flow (click through to seat selection)
   - Any venue map / seat map pages

2. **Capture all network traffic** using `browser_network_requests`:
   - REST API endpoints (XHR/Fetch)
   - GraphQL queries (capture full query + variables)
   - CDN requests (seats.io `cdn-na.seatsio.net`, `cdn-eu.seatsio.net`, or other seat map providers)
   - WebSocket connections
   - Auth/token endpoints

3. **Determine the data architecture**:
   - How are shows/events listed? (HTML page, REST API, GraphQL, sitemap)
   - Single-date shows vs multi-date shows — how does the site handle schedules?
   - If multi-date: how do we get ALL dates/performances for a show? (pagination, calendar API, etc.)
   - For each performance/date: how do we get seat inventory? (API, seats.io, HTML table, SVG map)
   - What data is available? (section, row, seat number, price, availability status, ADA, VIP)
   - What encoding is used? (plain JSON, hex-encoded, base64, HTML)

4. **Identify ALL scraping scenarios** — do NOT miss any:
   - seats.io events (hex-encoded seat maps + CDN pricing)
   - Direct API events (REST/GraphQL with plain JSON)
   - General admission (no seat map, just ticket categories + quantities)
   - External redirects (Ticketmaster, holdmyticket.com, etc.)
   - Any other vendor integration on the site
   - **Test at least one event from each scenario type** using Playwright MCP

5. **Identify anti-bot / auth requirements**:
   - Cloudflare, Akamai, Queue-IT, CAPTCHA
   - Token/auth endpoints (anonymous JWT, API keys, session cookies)
   - Required headers (`User-Agent`, `Authorization`, custom headers like `x-mgm-source`)
   - Rate limiting behavior
   - Does headless browser work or is headed required?

6. **Determine the best scraping approach** for each scenario:
   - Can we use direct API calls (httpx) without a browser? (always prefer this)
   - Do we need Playwright only for initial key extraction (like yapsody)?
   - Do we need a full browser session throughout?
   - What's the minimum browser interaction needed?

### 1.2 Document in FINDINGS.md

Create a `FINDINGS.md` in the scraper directory covering:
- Tech stack of the target site (Next.js, Angular, React, etc.)
- All discovered API endpoints with request/response examples
- GraphQL queries with full query text + variables
- Authentication flow (step by step)
- All scraping scenarios with example URLs
- Anti-bot measures and how to handle them
- Recommended approach for each scenario
- Data model mapping (site fields → our schema fields)

### 1.3 Share Findings Before Coding

Present findings to the user. **Do NOT start coding until the user approves the approach.** If anything is unclear, ask — do not assume.

---

## Phase 2: Directory Structure — Follow Yapsody/MGM Pattern

Every new scraper MUST follow the flat Lambda-pipeline structure established in `yapsody-scraper/` and `mgmresorts-scraper/`:

```
<site>-scraper/
├── <site>_show_discovery.py              # Lambda 1: discover all shows/events from the site
├── <site>_determine_scraper.py           # Lambda 2: extract keys, pricing, scraper config per event (if needed)
├── <site>_provide_schedule_params_list.py # Lambda (if multi-date): build schedule scrape list from DB
├── <site>_event_schedule.py              # Lambda (if multi-date): fetch all dates for each show
├── <site>_provide_inventory_params_list.py# Lambda: build inventory scrape list from DB
├── event_inventory_main.py               # Lambda (final): scrape seat inventory + upload to DB
├── event_inventory.<method>.py           # Module: actual scraping logic (seatsio, direct_api, etc.)
├── event_inventory.browser.py            # Module: Playwright browser helper (if browser needed)
├── <site>_auth.py                        # Module: authentication helper (if auth needed)
├── run_all_events.py                     # Convenience: full pipeline in one script (no DB)
├── run_pipeline.py                       # Convenience: full pipeline runner
├── test_local.py                         # Local test runner — no DB, prints to stdout
├── schema.sql                            # MySQL schema for all tables
├── requirements.txt                      # Module-specific dependencies
├── README.md                             # Setup, usage, architecture, Lambda docs
├── local_creds.py                        # (gitignored) DB + proxy credentials
└── graphql_queries.json                  # (if GraphQL) captured queries for reference
```

### Key structural rules:
- **Flat file layout** — no nested `scrapers/`, `utils/`, `discovery/`, `orchestration/` subdirectories
- **One Lambda per pipeline step** — each file is a standalone Lambda function
- **Shared modules** use `event_inventory.<method>.py` naming (dot-separated, loaded via `importlib`)
- **`test_local.py`** is the primary local test interface — every command works without DB
- **`schema.sql`** defines all MySQL tables upfront
- **`local_creds.py`** pattern for DB/proxy credentials (gitignored, with stub fallback in test_local.py)

### Pipeline pattern:

**For sites with single-date events (like yapsody):**
```
show_discovery → determine_scraper → provide_inventory_params_list → event_inventory_main
```

**For sites with multi-date shows (like MGM):**
```
show_discovery → provide_schedule_params_list → event_schedule → provide_inventory_params_list → event_inventory_main
```

The pipeline depends on whether the site has shows with multiple performance dates. Research phase must determine this.

---

## Phase 3: Coding Standards — No Junk, No Broken Code

### Must Follow

- **Clean, production-quality code** — no placeholder logic, no TODO comments left in, no half-implementations
- **Every function must work end-to-end** before moving to the next one
- **httpx for direct HTTP calls** — async or sync depending on context
- **Playwright for browser automation** — only when direct API is not possible. Use async API
- **pandas DataFrames** for seat inventory output — consistent with yapsody/mgmresorts pattern
- **`requests` or `curl_cffi`** for Lambda functions that need TLS fingerprint impersonation (like MGM)
- **BeautifulSoup** for HTML parsing when needed
- **JSON for structured data** — `json.loads()` / `json.dumps()`
- **Error handling** — catch specific exceptions, log clearly, don't silently swallow errors
- **Proxy support** — all HTTP calls must accept optional proxy configuration
- **DB operations** — `ON DUPLICATE KEY UPDATE` for upserts, bulk insert for inventory

### Naming Conventions

- Directory: `<site>-scraper/` (hyphenated, like `yapsody-scraper`, `mgmresorts-scraper`)
- Branch: `<Site>-Scraper` (PascalCase site name, e.g. `Yapsody-Scraper`, `Mgmresorts-Scraper`)
- Lambda files: `<site>_<function>.py` (snake_case, e.g. `yapsody_show_discovery.py`)
- Module files: `event_inventory.<method>.py` (dot-separated)
- DB tables: `event_discovery`, `event_schedule`, `event_inventory`, `event_inventory_logs`

### Schema Pattern

Follow the same DB schema pattern as yapsody/mgmresorts:

- **`event_discovery`** — show/event catalog with scraper_type classification
- **`event_schedule`** — (if multi-date) individual performance dates per show
- **`event_inventory`** — seat-level rows with standard columns:
  - `eventId`, `event_name`, `venue_name`, `event_datetime`
  - `objectLabelOrUuid` (unique seat label like `"101-P-1"`)
  - `section_name`, `row`, `seat_number`
  - `total_price`, `seat_pattern` (Consecutive/Even/Odd), `status` (available/sold)
  - `isRestrictedView`, `category`, `ticket_type`
  - `ada`, `vip` (if available)
  - `time_frame`
- **`event_inventory_logs`** — one row per scrape run with status, total_stock, stock_by_section

### Anti-Patterns — Absolute NO

- No hardcoded event URLs or IDs in production code
- No `time.sleep()` — use Playwright waits or async patterns
- No secrets in code — use `local_creds.py` (gitignored) or environment variables
- No junk/dead code — every line must serve a purpose
- No broken functions — if it doesn't work, fix it before committing
- No missing error handling on network requests
- No assuming API responses will always have data — handle empty/null gracefully
- No skipping scenarios — if the site has 3 types of events, we handle all 3

---

## Phase 4: Development Workflow — Incremental, Tested, Verified

### Build order (strict):

1. **`schema.sql`** — define all DB tables first based on research findings
2. **Auth module** (if needed) — get authentication working and verified
3. **`<site>_show_discovery.py`** — discover all shows/events
   - **TEST**: run it, verify output matches what Playwright MCP showed on the site
4. **`<site>_determine_scraper.py`** (if needed) — extract per-event config
   - **TEST**: run on 2-3 events of different types, verify all keys extracted
5. **`<site>_event_schedule.py`** (if multi-date) — fetch all dates per show
   - **TEST**: run on a show with known dates, verify count matches site
6. **`event_inventory.<method>.py`** — core scraping logic per scenario
   - **TEST**: run on one event per scenario type, verify seat counts + prices match site
7. **`event_inventory_main.py`** — Lambda orchestrator with inventory + existence check modes
   - **TEST**: run inventory mode, verify DataFrame output
8. **`<site>_provide_*_params_list.py`** — DB query builders for Step Functions
9. **`test_local.py`** — wire up all CLI commands (discover, determine, schedule, inventory, full)
   - **TEST**: every command must produce clean output
10. **`run_all_events.py`** (or `run_pipeline.py`) — end-to-end pipeline runner
11. **`README.md`** — document everything after implementation is verified

### Testing Requirements

After each step, **run the code and verify results** by comparing with Playwright MCP browser output:

- **Use Playwright MCP to open the same event** on the site and visually verify seat counts, prices, sections
- **Output must match** what the site shows — if there's a discrepancy, investigate and fix before moving on

### Coverage Testing (test_local.py)

The `test_local.py` must support these commands at minimum:
- `discover` — list all events/shows
- `inventory <params>` — seat inventory for one event
- `full <params>` — end-to-end single event (most convenient)
- Additional commands matching the pipeline steps

### Scenario Coverage Testing

When testing, **randomly pick 2-3 events of each scenario type** but include ALL types:
- If site has seats.io events → test 2-3 of those
- If site has direct API events → test 2-3 of those
- If site has GA events → test 2-3 of those
- If site has external redirects → test 2-3 of those
- **Do not skip any scenario type** — every type must be verified working

---

## Phase 5: Output & Data Quality

### Seat inventory DataFrame columns (standard across all scrapers):

```
eventId | event_name | venue_name | event_datetime | objectLabelOrUuid | section_name | row | seat_number | total_price | status | seat_pattern | isRestrictedView | category | ticket_type | ada | vip | time_frame
```

### test_local.py output format (follow yapsody/mgmresorts pattern):

- Print section-level summary (count + starting price per section)
- Print price range (min–max)
- Print sample rows (first 10 as formatted DataFrame)
- Print scrape logs (status, total stock, stock by section)

### Data integrity:

- Every seat must have a price (no `None` or `0.00` unless truly free)
- `objectLabelOrUuid` must be unique per event (e.g. `"Section-Row-SeatNum"`)
- `seat_pattern` must be correctly detected (Consecutive/Even/Odd based on seat numbering)
- `status` must reflect actual availability (available vs sold)
- Section names must match what the venue actually calls them

---

## Phase 6: Step Function Pipeline & Lambda Structure

### Lambda function structure (follow yapsody/mgmresorts exactly):

Each Lambda function:
- Has a `lambda_handler(event, context)` entry point
- Imports `creds` module for DB access (mounted at `/mnt/access/creds.py` in Lambda)
- Uses `customrequests` wrapper for proxied HTTP (Lambda environment)
- `test_local.py` bypasses both by injecting `local_creds` as `creds` module
- Returns a dict with result summary

### Step Function pipeline:
```
Discovery (daily)
    ↓ Map
Determine/Schedule (per show)
    ↓
Params List Builder
    ↓ Map
Inventory (per event)
```

### Existence check mode:

`event_inventory_main.py` must support two modes:
1. **Inventory mode** (default): scrape + upload all seats to DB
2. **Existence check mode** (`mode: "existence_check"`): scrape + return `{ exists: bool }` based on matching rules (section/price/row/quantity)

---

## When to Ask, When Not to Assume

- If research reveals something unexpected → **ask the user**
- If a scenario doesn't fit the pattern → **ask the user**
- If pricing structure is unusual → **ask the user**
- If anti-bot blocks you → **ask the user**
- **Never assume** an API will behave like another site's API
- **Never skip** a scenario because it seems complex
- **Never ship** code that hasn't been tested against real data
