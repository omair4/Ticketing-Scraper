Tixr Scraper
============

This module scrapes ticket availability and pricing data from `tixr.com` for a single event URL.

It is intentionally modelled after the existing `event_scraper` (AdmitONE) and `prekindle_scraper` modules:

- Async Playwright browser automation (`browser.py`)
- Scenario-based scrapers selected via a factory in `scrapers/__init__.py`
- Pydantic v2 models for normalized output (`models.py`)
- A CLI entry point (`main.py`) that exports JSON/CSV into an `output/` directory

Status
------

This module is currently under active development. The initial structure is in place, but
the concrete Tixr scenarios, DOM selectors, and API parsing logic still need to be filled in
once we finish reconnaissance of real Tixr event pages.

