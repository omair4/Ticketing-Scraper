# Jade Eagleson Event - Ticket Extraction Analysis

## Event URL
```
https://admitone.com/events/calgary/pro/concerts/the-palace-theatre/jade-eagleson/jade-eagleson/689e0f85857db55262f707c3
```

## Flow Analysis

### Step 1: Initial Page Load
- Event page loads normally
- Contains "Buy Now" button

### Step 2: Click "Buy Now"
- Opens a modal with an **iframe**
- Iframe source: `https://embed.admitonelive.com/event/jade-eagleson-calgary-10125856`
- Event ID in URL: `10125856`

### Step 3: Iframe Content
The iframe loads from `embed.admitonelive.com` which is different from:
- **Scenario 1 (SeatsIO)**: Uses `embed.admitonelive.com/api/events/seated/`
- **Scenario 2 (Direct API)**: Uses `tickets.admitone.com`
- **Scenario 3 (GraphQL)**: Uses `checkout.admitone.com` → `graphql.admitone.com`

## Key APIs Detected

### 1. GraphQL Request
```
POST https://graphql.admitone.com/
```
- Found on initial page load
- May contain event metadata but needs investigation for ticket data

### 2. Embed Widget
```
GET https://embed.admitonelive.com/event/jade-eagleson-calgary-10125856
```
- This is the main ticketing widget
- Loads JavaScript files including:
  - `box_office/poop.js` - Likely contains ticket selection logic
  - Various jQuery plugins and UI components

## Potential API Endpoints to Monitor

Based on the iframe loading, we should monitor for:

1. **Embed API calls** (similar to Scenario 1):
   - `embed.admitonelive.com/api/events/...`
   - May contain ticket categories and pricing

2. **GraphQL queries** from within the iframe:
   - Additional GraphQL requests may be made after iframe loads
   - Need to capture these with event-specific queries

3. **Direct ticket API**:
   - `tickets.admitonelive.com/...` (note: different from `tickets.admitone.com`)
   - May contain ticket availability

## Recommendations for Playwright MCP Implementation

### 1. Update Browser Automation (`browser.py`)

Add detection for `embed.admitonelive.com` iframe pattern:

```python
# In _wait_for_modal_iframe method, add:
iframe_selectors = [
    'iframe[src*="embed.admitonelive.com"]',  # NEW: For Jade Eagleson scenario
    'iframe[src*="embed.admitonelive.com/api/events/seated"]',  # Existing SeatsIO
    'iframe[src*="tickets.admitone.com"]',  # Existing Direct API
]
```

### 2. Capture Network Requests from Iframe

The iframe may make additional API calls that need to be captured:

```python
# After iframe loads, wait for additional API calls
await iframe.wait_for_load_state('networkidle', timeout=15000)
await asyncio.sleep(5)  # Give time for ticket API calls
```

### 3. Monitor for Ticket APIs

Add to `_on_response` method in `browser.py`:

```python
relevant_patterns = [
    'embed.admitonelive.com',  # Already exists
    'tickets.admitonelive.com',  # NEW: Different from tickets.admitone.com
    'graphql.admitone.com',  # Already exists
    '/api/events/',  # Already exists
    'box_office',  # NEW: May contain ticket endpoints
]
```

### 4. Create New Scraper (if needed)

If this is a distinct scenario, create `scrapers/embed_widget_scraper.py`:

```python
class EmbedWidgetScraper(BaseScraper):
    """
    Scraper for events using embed.admitonelive.com widget.
    
    This handles events like Jade Eagleson that load a ticketing widget
    in an iframe from embed.admitonelive.com/event/{slug}
    """
    
    async def extract(self, captured_responses, page_html, event_url):
        # Look for API responses from embed.admitonelive.com
        # Extract ticket categories and pricing
        pass
```

## Next Steps

1. **Intercept iframe network requests**: Use Playwright's `page.on('response')` to capture all requests from the iframe context
2. **Wait for ticket API calls**: After iframe loads, wait longer (5-10 seconds) for ticket data APIs to be called
3. **Inspect GraphQL queries**: Check if the GraphQL request contains ticket data or if additional queries are needed
4. **Test with actual Playwright**: Run the scraper and capture all network requests to identify the exact ticket/pricing API endpoints

## Event ID Extraction

From the iframe URL: `embed.admitonelive.com/event/jade-eagleson-calgary-10125856`
- Event ID: `10125856`
- Slug: `jade-eagleson-calgary-10125856`

This event ID can be used to construct API endpoints if needed.
