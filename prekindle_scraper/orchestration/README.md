# PreKindle Scraper Orchestration

Multi-script workflow for discovering, bulk scraping, and refreshing inventory for PreKindle events.

## Overview

This orchestration layer provides three scripts for managing large-scale PreKindle event scraping:

- **Script 1 (`01_discover.py`)** - Discover all events from PreKindle search API
- **Script 2 (`02_bulk_scraper.py`)** - Scrape all discovered events with concurrency control
- **Script 3 (`03_refresh_inventory.py`)** - Fast inventory refresh for seatsio events (API-only)

## Quick Start

```bash
# Navigate to prekindle_scraper directory
cd prekindle_scraper

# Step 1: Discover all events
python orchestration/01_discover.py

# Step 2: Bulk scrape all events
python orchestration/02_bulk_scraper.py --max-events 10  # Test with 10 events first

# Step 3: Refresh inventory (API-only, much faster)
python orchestration/03_refresh_inventory.py
```

## Script 1: Event Discovery

Discovers all PreKindle events and initializes state tracking.

### Usage

```bash
# Discover all events (default)
python orchestration/01_discover.py

# Test with limited results
python orchestration/01_discover.py --max-pages 1

# Customize pagination
python orchestration/01_discover.py --limit 100 --max-pages 5

# Custom output path
python orchestration/01_discover.py --output my_events.json
```

### Output

- `orchestration/state/events_discovered.json` - All discovered events
- `orchestration/state/scraping.json` - State tracking for bulk scraping

### Options

- `--limit N` - Results per page (default: 50)
- `--max-pages N` - Maximum pages to fetch (default: all)
- `--delay N` - Seconds between paginated requests (default: 1.0)
- `--output PATH` - Custom output JSON path
- `--debug` - Enable debug logging

---

## Script 2: Bulk Scraper

Scrapes all discovered events with concurrency control, error handling, and progress tracking.

### Usage

```bash
# Scrape all pending events
python orchestration/02_bulk_scraper.py

# Test with limited events
python orchestration/02_bulk_scraper.py --max-events 10

# Customize concurrency
python orchestration/02_bulk_scraper.py --concurrency 3

# Retry failed events
python orchestration/02_bulk_scraper.py --retry-failed

# Filter by event type
python orchestration/02_bulk_scraper.py --only-purchasable --skip-sold-out

# Run in headless mode
python orchestration/02_bulk_scraper.py --headless
```

### Output

Results are saved to `output/bulk_scrape_{timestamp}/by_scenario/{scenario}/`:

```
output/
  bulk_scrape_20260220_143022/
    by_scenario/
      seatsio/
        Event_Name_20260220_143025.json
        Another_Event_20260220_143030.json
      general_admission/
        GA_Event_20260220_143035.json
      svg_seatmap/
        ...
```

### Options

- `--max-events N` - Maximum events to scrape (default: all)
- `--concurrency N` - Concurrent browser instances (default: 5)
- `--delay N` - Seconds between events (default: 3.0)
- `--retry-failed` - Retry previously failed events
- `--resume` - Continue from where you left off (default behavior)
- `--skip-sold-out` - Skip sold out events
- `--only-purchasable` - Skip free events
- `--headless` - Run browsers in headless mode
- `--debug` - Enable debug logging

### Performance

- **Time per event**: 10-30 seconds (with browser automation)
- **Concurrency 5**: ~50-150 events/hour
- **Full 4000+ events**: 27-81 hours (can run overnight/multi-day)

### State Tracking

Progress is saved in `orchestration/state/scraping.json`. Script automatically resumes from last state if interrupted.

---

## Script 3: Inventory Refresh

Fast inventory refresh for seatsio events using API-only (no browser needed).

### Usage

```bash
# Refresh latest bulk scrape results
python orchestration/03_refresh_inventory.py

# Specify input directory
python orchestration/03_refresh_inventory.py --input output/bulk_scrape_20260219/by_scenario/seatsio/

# Refresh specific event
python orchestration/03_refresh_inventory.py --event-id -2852852648567343600

# Customize concurrency (much higher than browser scraping)
python orchestration/03_refresh_inventory.py --concurrency 100
```

### Output

Results saved to `output/refresh_{timestamp}/`:

```
output/
  refresh_20260220_150000/
    Event_Name_20260220_150001.json
    Another_Event_20260220_150002.json
```

### Options

- `--input DIR` - Input directory with seatsio JSON files
- `--event-id ID` - Refresh specific event only
- `--concurrency N` - Concurrent API requests (default: 50)
- `--delay N` - Seconds between requests (default: 0.1)
- `--debug` - Enable debug logging

### Performance

- **Time per event**: 1-3 seconds (API only)
- **Concurrency 50**: ~1000-1800 events/hour
- **2000 seatsio events**: **1-2 hours** (100x faster than browser scraping)

### How It Works

1. Reads previous scrape results to extract API URLs and pricing
2. Fetches fresh `object-statuses` data from seats.io API
3. Rebuilds seat availability using saved chart structure and pricing
4. Exports refreshed results with `scrape_method: "api_refresh"`

### Limitations

- **Only works for seatsio scenario** (not general_admission or svg_seatmap)
- **Pricing NOT refreshed** (uses cached data from original scrape)
- **Cannot detect scenario changes** (if event switches to different ticketing system)

---

## Complete Workflow Example

### Initial Scrape (Full Dataset)

```bash
# 1. Discover all events (~4000 events)
python orchestration/01_discover.py

# 2. Bulk scrape (may take 27-81 hours for all events)
python orchestration/02_bulk_scraper.py --headless --only-purchasable

# If interrupted, resume with:
python orchestration/02_bulk_scraper.py --resume

# Retry failures:
python orchestration/02_bulk_scraper.py --retry-failed
```

### Daily Inventory Updates (Fast)

```bash
# Refresh all seatsio events (1-2 hours instead of 27-81 hours)
python orchestration/03_refresh_inventory.py --concurrency 50
```

### Test Run (Small Dataset)

```bash
# Test with first 20 events
python orchestration/01_discover.py --max-pages 1
python orchestration/02_bulk_scraper.py --max-events 20 --headless
python orchestration/03_refresh_inventory.py
```

---

## State Management

All state is tracked in `orchestration/state/scraping.json`:

```json
{
  "events": {
    "-2852852648567343600": {
      "event_id": "-2852852648567343600",
      "ticket_url": "https://www.prekindle.com/promo/id/-2852852648567343600",
      "title": "Event Name",
      "status": "completed",
      "scenario_type": "seatsio",
      "output_json_path": "output/bulk_scrape_20260220/seatsio/Event_20260220.json",
      "scraped_at": "2026-02-20T14:30:25Z"
    }
  }
}
```

### Event Status Values

- `pending` - Not yet scraped
- `in_progress` - Currently scraping
- `completed` - Successfully scraped
- `failed` - Scraping failed (will retry up to max_retries)

---

## Configuration

Edit `orchestration/config.py` to customize:

```python
@dataclass
class OrchestrationConfig:
    # Paths
    state_dir: Path = Path("orchestration/state")
    output_base_dir: Path = Path("output")

    # Bulk scraping
    max_concurrency: int = 5
    request_delay: float = 3.0
    max_retries_per_event: int = 3

    # Inventory refresh
    refresh_concurrency: int = 50
    refresh_delay: float = 0.1
```

---

## Troubleshooting

### No events found

```bash
# Check if discovery was run
ls orchestration/state/events_discovered.json

# If missing, run Script 1:
python orchestration/01_discover.py
```

### All events already scraped

```bash
# Check state file
cat orchestration/state/scraping.json | jq '.events | to_entries | map(select(.value.status == "pending")) | length'

# Or count manually
cat orchestration/state/scraping.json | grep -c '"status": "pending"'

# Retry failed events
python orchestration/02_bulk_scraper.py --retry-failed

# Or manually edit scraping.json to reset event status to "pending"
```

### Browser crashes / memory issues

```bash
# Reduce concurrency
python orchestration/02_bulk_scraper.py --concurrency 2

# Increase delay between events
python orchestration/02_bulk_scraper.py --delay 5.0
```

### Refresh fails (missing API URLs)

- Only works for seatsio scenario
- Requires previous scrape results with `api_urls` field
- Re-scrape events that are missing API URLs:
  ```bash
  python orchestration/02_bulk_scraper.py --retry-failed
  ```

---

## Performance Comparison

| Operation | Method | Time per Event | Total Time (4000 events) |
|-----------|--------|----------------|-------------------------|
| Initial Scrape | Browser (Script 2) | 10-30s | 27-81 hours |
| Inventory Refresh | API (Script 3) | 1-3s | 1-2 hours |
| **Speedup** | | | **~30-80x faster** |

---

## Architecture

```
Script 1 (Discover)
  ↓
  state/events_discovered.json (4066 events)
  ↓
  state/scraping.json (event state tracking)
  ↓
Script 2 (Bulk Scraper)
  ↓ (reads pending events)
  ↓ (calls main.scrape_event() for each)
  ↓
  output/bulk_scrape_TIMESTAMP/
    by_scenario/
      seatsio/*.json (includes api_urls + html_pricing)
      general_admission/*.json
  ↓
Script 3 (Inventory Refresh)
  ↓ (reads seatsio/*.json files)
  ↓ (extracts api_urls + html_pricing)
  ↓ (fetches fresh object-statuses via httpx)
  ↓ (rebuilds seats using seatsio_scraper logic)
  ↓
  output/refresh_TIMESTAMP/*.json (refreshed inventory)
```

---

## Dependencies

All dependencies from main `prekindle_scraper` module:

- `httpx` - API requests
- `playwright` - Browser automation
- `pydantic` - Data models
- `structlog` - Logging
- `tenacity` - Retries

No additional dependencies required.

---
