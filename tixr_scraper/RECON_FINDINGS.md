# Tixr Recon Findings — Category System

**Date:** March 10, 2026  
**Method:** Playwright MCP browser navigation + network interception  
**Sources:** This doc + merged key points from external TIXR_FINDINGS (2026-03-06)  
**Page:** https://www.tixr.com/

---

## Bot protection: DataDome (why Playwright is mandatory)

Tixr uses **DataDome** for bot protection. All relevant API calls return **403** when made with plain HTTP (curl, requests, httpx) and only succeed from a **real browser** after the DataDome JS challenge runs and sets the session cookie.

| Resource | From browser (in-page fetch) | From curl/requests |
|----------|------------------------------|---------------------|
| `/api/page/requirements` | **200 OK** | **403 Blocked** |
| `/api/featured` | **200 OK** | **403 Blocked** |
| `/api/events/{id}` | **200 OK** | **403 Blocked** |
| `/api/groups/{id}` | **200 OK** | **403 Blocked** |
| `/api/groups/{id}/events` | **200 OK** | **403 Blocked** |
| `/api/search`, `/api/discover`, `/api/groups` | 403 or 404 | **403 Blocked** |
| `/robots.txt`, `/sitemap.xml` | **Sitemap works** after session (see below) | **403 Blocked** |

**Takeaway:** Pure HTTP scraping is **not viable**. Discovery and event/inventory calls must run inside a browser context (Playwright). Rate limiting has been observed after many rapid fetches from the same browser — add delays and reuse the same context/cookie where possible.

---

## Discovery: How the Category System Works

### The Key Endpoint

**`GET /api/page/requirements?url=%2F&queryParams=`**

This single API call powers the **entire homepage category system**. The response contains:

```
{
  "logins": [...],
  "setup": {
    "saleId": null,
    "eventId": null,
    "groupId": null,
    "topics": [              <-- THIS IS THE CATEGORY SYSTEM
      {
        "name": "Sports Highlights",
        "carousel": {
          "items": [...]     <-- Events in this category
        }
      },
      ...
    ]
  },
  "tracking": {...},
  "localeInfo": {...}
}
```

### All 11 Categories Found (with event counts)

| # | Category Name | Events in Carousel |
|---|--------------|-------------------|
| 1 | Sports Highlights | 12 |
| 2 | Festivals | 25 |
| 3 | Music Venues | 25 |
| 4 | Comedy | 25 |
| 5 | National Women's Soccer League | 25 |
| 6 | Pop Culture | 25 |
| 7 | Sports | 25 |
| 8 | Motorcycles & Motorsports | 25 |
| 9 | Food & Beverage | 18 |
| 10 | Electronic | 25 |
| 11 | Esports | 25 |

**Total events across all categories: ~255**

### Event Item Structure (per carousel item)

Each event in `topics[].carousel.items[]` has:

```json
{
  "id": 178222,
  "name": "Earthquakes Season Tickets (18 Matches) at PayPal Park",
  "startDate": 1772323200000,
  "endDate": 1793489400000,
  "hoverMessage": "View Event",
  "defaultLocale": "en_US",
  "details": "",
  "imageUrl": "https://static.tixr.com/static/images/external/img/...",
  "link": "https://www.tixr.com/groups/sjearthquakes/events/earthquakes-season-tickets-18-matches--178222"
}
```

Key fields for scraping:
- `id` → eventId (used for `/api/events/{id}`)
- `name` → event name (includes venue name after "at")
- `startDate` / `endDate` → Unix milliseconds
- `link` → full event URL on tixr.com

### Other API Calls on Homepage

| Endpoint | Purpose | Relevant? |
|----------|---------|-----------|
| `GET /api/featured` | Hero carousel (10 items only) | Partially — overlaps with topics |
| `GET /ajax/wtii` | Session init, returns `{"time": ...}` | No |
| `POST api-js.datadome.co/js/` | DataDome bot protection | No (but important to know) |
| `GET juicer.io/api/feeds/tixr_` | Social media feed | No |

### Important Notes

1. **DataDome blocks secondary fetch() calls** from the page context (returns 403/HTML).  
   The `/api/page/requirements` response is only reliably captured **during the initial page navigation** via response interception.

2. **`/api/featured` is NOT the category system.** It only returns 10 hero carousel items. The real category data lives in `/api/page/requirements` → `setup.topics`.

3. Each category shows **up to 25 events** in the carousel. This may or may not be all events in that category on the platform. We need to investigate whether there are "view all" links or pagination for categories with more events.

4. The `link` field in each item is a **full URL** including the group slug and event slug with the numeric eventId at the end (e.g., `...-178222`).

---

## Open Question 1: Are 25 events per category ALL events, or a subset? (ANSWERED)

**Findings (Playwright MCP recon):**

- On the homepage there are **no "View all"**, **"See more"**, or **pagination** links next to any category section.
- Each topic in `/api/page/requirements` has only `name` and `carousel`; carousel has only `items` and `layout`. **No `totalCount`, `viewAllUrl`, or `nextPage`** in the API response.
- Adding `?page=2` to the homepage URL still returns the **same** 11 topics and **255 total events** — the API does not use query params for pagination on the homepage.
- **Conclusion:** For the **homepage**, the list is a **fixed curated set**: 11 categories, 255 events total (12–25 per category). There is no pagination or "view all" in the UI or in the `/api/page/requirements` response for `url=/`. To get more events we must rely on **group pages** (e.g. `/groups/sjearthquakes`) or other discovery paths (see Q3).

---

## Open Question 3: Group pages — do they have their own API with more events? (ANSWERED)

**Findings (Playwright MCP recon on `/groups/sjearthquakes`):**

- On group page load, two Tixr APIs fire:
  1. **`GET /api/page/requirements?url=%2Fgroups%2Fsjearthquakes&queryParams=`**  
     - Returns `setup.groupId: 2583` (numeric id). No `topics` on group page.
  2. **`GET /api/groups/2583`**  
     - Group metadata: id, name, subdomain, url, logoUrl, theme, etc.
  3. **`GET /api/groups/2583/events`**  
     - Returns a **full array of events** for that group (e.g. 20 events for San Jose Earthquakes).  
     - Each event has: id, name, startDate, endDate, url, venue, sales, state, etc. — same shape as needed for scraping.

So: **groups are the source of “all events per organizer”**. For each group we need the numeric `groupId`, then `GET /api/groups/{groupId}/events` gives the full event list for that group.

**How we get group IDs:**  
- The group **slug** (e.g. `sjearthquakes`) comes from the URL path.  
- The numeric **groupId** (e.g. `2583`) comes from `GET /api/page/requirements?url=%2Fgroups%2F{slug}` → `setup.groupId`.  
- So we need the browser (or a working server-side call to page/requirements) to resolve slug → id; then we can call `/api/groups/{id}/events`.

---

## Topics vs groups — are topics useless? (VERIFIED)

**Short answer: No. Topics are not useless. We need both.**

- **Topics (homepage `page/requirements?url=%2F`):**
  - Give us **11 category rails** and **255 event links** (curated carousel).
  - From those 255 links we can extract **109 unique group slugs** (e.g. `sjearthquakes`, `thestandnyc`, `dolab`).  
  - So topics are our **discovery source for groups**. Without them (or another “list of groups” endpoint) we have no way to know which groups exist.
- **Groups API (`/api/groups/{id}/events`):**
  - Once we have a **groupId** (e.g. 2583), this endpoint returns **all events for that group** (e.g. 20 for SJ Earthquakes), not just the ones on the homepage.
  - So groups API is how we get the **full event list per group**.

**Correct flow for “all events to scrape”:**
1. Use **topics** (homepage) to get **group slugs** (109 from current homepage).
2. For each slug: load group page (or call `page/requirements?url=%2Fgroups%2F{slug}`) to get **groupId**.
3. Call **`/api/groups/{groupId}/events`** to get **all events** for that group.
4. Optionally also use the 255 event links from topics directly (they’re valid event URLs).

So: **Topics are not useless** — they give us the list of groups. **Groups API is what gives us all events per group.** Both are needed.

**Verified:** External testing confirms `/api/groups/{id}/events` and `/api/events/{id}` return **403** from curl/requests. All discovery and inventory must use Playwright (browser context).

---

## Single source for all topics or all groups? (ANSWERED)

**Goal:** Avoid the flow "topics → extract group slugs → page/requirements per group → /api/groups/{id}/events" by finding either (a) all topics in one place other than the homepage, or (b) all groups in one place.

**Tests performed (from browser context on https://www.tixr.com/):**

| Endpoint / path | Result | Notes |
|-----------------|--------|--------|
| `GET /api/groups` | **404** | No "list all groups" endpoint. |
| `GET /api/topics` | **403** | Blocked (DataDome or auth). |
| `GET /api/categories` | **403** | Blocked. |
| `GET /api/events` (no id) | **200** | Returns **20 events** (array). Pagination params `page`, `limit`, `offset` do **not** change count; always 20. So this is a fixed "featured/recent" list, **not** full catalog. |
| `GET /api/discover` | **403** | Blocked. |
| `GET /api/browse` | **403** | Blocked. |
| `GET /api/page/requirements?url=/browse` | 200 | `setup` **empty** — no topics, no groups. |
| `GET /api/page/requirements?url=/explore` | 200 | `setup` **empty**. |
| `GET /api/page/requirements?url=/events` | 200 | `setup` **empty**. |
| `GET /api/page/requirements?url=/discover` | 200 | `setup` **empty**. |
| `GET /api/page/requirements?url=/groups` | 200 | `setup` **empty**. |
| `GET /api/page/requirements?url=/` | 200 | **Only** this URL returns `setup.topics` (11 topics). |

**Conclusion:**

- **Topics:** The **only** place that returns the category/topic list is **`GET /api/page/requirements?url=%2F`** (homepage). There is no other page or API that exposes "all topics" in one response.
- **Groups:** There is **no** "list all groups" API. `/api/groups` returns 404; `/api/topics`, `/api/categories`, `/api/discover`, `/api/browse` are 403 or empty. So we **must** discover group slugs from the topic carousel event links, then resolve slug → groupId via `page/requirements?url=%2Fgroups%2F{slug}` (or equivalent), then call `/api/groups/{groupId}/events` for full event lists.

**Discovery flow remains:**

1. **Homepage:** `GET /api/page/requirements?url=%2F` → `setup.topics` (11 topics, ~255 event links).
2. **Extract** unique group slugs from event `link` URLs.
3. **Per group:** `GET /api/page/requirements?url=%2Fgroups%2F{slug}` → `setup.groupId`; then `GET /api/groups/{groupId}/events` for all events.

No shortcut to "all topics elsewhere" or "all groups in one place" was found.

---

## Sitemap vs topics: which to use for group discovery? (ANSWERED)

**Test (Playwright MCP, March 2026):** With browser session established (navigate to https://www.tixr.com/ first), then navigate to `https://www.tixr.com/sitemap.xml` — **sitemap loads**. Root sitemap is a **sitemap index** listing many child sitemaps, including **58 group sitemaps**: `https://www.tixr.com/catalog/group-sitemap-1.xml` … `group-sitemap-58.xml`. Each group sitemap contains `<loc>` URLs for group pages (`/groups/{slug}`).

### Counts (same session, same day)

| Source | What we get | Count |
|--------|-------------|--------|
| **Sitemap** (all 58 group-sitemap XMLs) | One URL per group page | **2,654 unique group slugs** |
| **Topics** (homepage `page/requirements?url=/`) | Unique group slugs from event links in 11 topic carousels | **109 unique group slugs** |

### Accuracy of topics

- **Topics cover ~4% of groups:** 109 / 2,654 ≈ **4.1%**. So **~96% of groups are not discoverable from homepage topics alone**.
- All 109 topic-derived slugs appear in the sitemap (topics are a subset of sitemap).
- For **full catalog** discovery we must use the **sitemap** when we have a browser session.

### Recommended discovery strategy

1. **Primary (full catalog):** In Playwright, after loading the homepage (to satisfy DataDome), **fetch `https://www.tixr.com/sitemap.xml`**, parse the index, fetch each `catalog/group-sitemap-*.xml`, and extract all `/groups/{slug}` URLs. Deduplicate slugs → **full list of 2,654+ groups**.
2. **Per group (unchanged):** For each slug, `GET /api/page/requirements?url=%2Fgroups%2F{slug}` → `setup.groupId`; then `GET /api/groups/{groupId}/events` for all events.
3. **Optional:** Use topics as a **quick/small subset** (e.g. for smoke tests or when only featured groups are needed); do **not** rely on topics for full catalog.

**Conclusion:** Use **sitemap for group discovery** whenever we need the full list. Use **topics** only as a curated subset (~109 groups). Document and implement sitemap-based discovery in the next step.

---

## Event-level API and data (inventory)

Once we have event IDs (from discovery), event detail comes from **`GET /api/events/{eventId}`** (must be called from browser context).

### Event API response (relevant fields)

- `id`, `name`, `shortName`, `status`, `startDate`, `endDate`, `doorsOpenDate`
- `venue`: id, name, address, latitude, longitude, timezone, **seatingType**, capacity
- `group`: id, name, subdomain, description, logoUrl
- `eventCategoryName` (Festival, Concert, etc.)
- **`sales[]`**: sale objects with state (OPEN/QUEUED/CLOSED), type (FIXED), **priceLevels**, maxTicketsAllowed, deliveryConfiguration
- **`seatsIoConfiguration`** for reserved-seating events (same pattern as AdmitONE/PreKindle)
- `media[]`, `lineups[]`, `description`, `resaleEnabled`, `allInPricing`, etc.

### JSON-LD fallback (no JS)

Event pages embed `application/ld+json` in the raw HTML with `@type: Event`, name, startDate, location, **offers** (ticket names/prices/categories), performers. Useful for **known event URLs** if we only need basic data or as a validation source; full pricing/availability still requires the API.

### URL patterns

- Event: `https://www.tixr.com/groups/{subdomain}/events/{slug}-{eventId}`
- Group: `https://www.tixr.com/groups/{subdomain}`
- Event IDs and group IDs are numeric (e.g. eventId 178222, groupId 2583).

---

## Architecture and next steps (conclusion)

### Agreed discovery flow

1. **Establish session (Playwright):** Navigate to `https://www.tixr.com/` so DataDome cookie is set.
2. **Full catalog (preferred):** Fetch **`https://www.tixr.com/sitemap.xml`** → parse index → fetch each **`catalog/group-sitemap-*.xml`** (58 files) → extract all `/groups/{slug}` URLs → deduplicate → **~2,654 group slugs**.
3. **Alternative (small subset):** Use **topics**: `GET /api/page/requirements?url=%2F` → `setup.topics` → extract group slugs from event links → **~109 groups** (only ~4% of catalog).
4. **Per group (unchanged):** For each slug: `GET /api/page/requirements?url=%2Fgroups%2F{slug}` → `setup.groupId`; then **`GET /api/groups/{groupId}/events`** → full event list for that group.
5. **Deduplicate** event IDs for the full catalog.

### Agreed event inventory flow

- For each event ID: use Playwright to establish context (or reuse existing), then **`GET /api/events/{eventId}`** for full detail (sales, priceLevels, venue, seatsIoConfiguration).
- Reserved seating: use **seats.io** integration (same approach as AdmitONE/PreKindle) when `seatsIoConfiguration` is present.

### How we proceed

- **Playwright is mandatory** for both discovery and event/inventory calls; no requests-only path exists.
- Implement as **Lambda(s)** (per client requirement): discovery Lambda(s) run browser to get topics → groups → event IDs; inventory Lambda(s) run browser to fetch `/api/events/{id}` (and seats.io where needed).
- **Rate limiting:** add delays between requests; reuse browser/cookie where possible; consider stealth (e.g. playwright-extra + stealth) for headless.
- **Captcha (slider) behavior:** DataDome can occasionally present a **slider captcha** (hard challenge), especially on first runs or when behavior looks very bot-like. We will:
  - Design scrapers to be **gentle** (limited concurrency, pauses, reused context) to avoid triggering it.
  - Treat any captcha encounter as a **failed run** to be retried later, not something we solve automatically in code.
  - Log these cases explicitly so we can tune rate limits / behavior if Tixr tightens protection.
- **No global search or “list all groups”** — discovery is strictly: homepage topics → group slugs → groupId → `/api/groups/{id}/events`.
- Optional: merge or cross-check with JSON-LD from raw HTML when we have event URLs.

This document justifies the above and serves as the single recon reference for the next implementation steps (Lambda discovery + inventory).
