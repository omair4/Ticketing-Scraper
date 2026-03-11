#!/usr/bin/env python3
"""
GraphQL Schema Discovery Script

Uses Playwright to navigate AdmitONE pages and capture GraphQL request/response data.
Run: python discovery/discover_graphql.py

Saves to discovery/graphql_queries.json
"""

import asyncio
import json
import sys
from pathlib import Path

# Add parent for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

OUTPUT_FILE = Path(__file__).parent / "graphql_queries.json"


async def discover_graphql():
    """Navigate pages and capture GraphQL requests/responses."""
    captured = {
        "requests": [],  # operationName, query, variables
        "responses": [],  # parsed response data structure
        "urls_visited": [],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        async def handle_route(route):
            request = route.request
            if request.method == "POST" and "graphql.admitone.com" in request.url:
                try:
                    post_data = request.post_data
                    if post_data:
                        body = json.loads(post_data)
                        captured["requests"].append({
                            "operationName": body.get("operationName"),
                            "query": body.get("query", ""),  # Full query required for replay
                            "variables": body.get("variables"),
                            "url": request.url,
                        })
                except json.JSONDecodeError:
                    pass
            await route.continue_()

        async def handle_response(response):
            if "graphql.admitone.com" in response.url and response.ok:
                try:
                    body = await response.text()
                    data = json.loads(body)
                    if "data" in data:
                        # Store structure (keys only for schema discovery)
                        def summarize(obj, depth=0):
                            if depth > 3:
                                return "..."
                            if isinstance(obj, dict):
                                return {k: summarize(v, depth + 1) for k, v in obj.items()}
                            if isinstance(obj, list):
                                if obj and isinstance(obj[0], dict):
                                    return [summarize(obj[0], depth + 1)]
                                return f"[{len(obj)} items]"
                            return type(obj).__name__

                        captured["responses"].append({
                            "url": response.url,
                            "structure": summarize(data.get("data", {})),
                            "sample": _extract_sample_events(data),
                        })
                except (json.JSONDecodeError, Exception):
                    pass

        page = await context.new_page()
        # Intercept all requests, filter for GraphQL in handler (route pattern often fails for full URLs)
        await page.route("**/*", handle_route)
        page.on("response", handle_response)

        urls_to_visit = [
            "https://admitone.com/events/toronto",
            "https://admitone.com/search?search=concert",
            "https://admitone.com/events/calgary",
        ]

        for url in urls_to_visit:
            try:
                print(f"Visiting {url}...")
                captured["urls_visited"].append(url)
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"  Error: {e}")

        await browser.close()

    return captured


def _extract_sample_events(data: dict) -> list:
    """Extract sample event objects from response for schema inference."""
    samples = []
    data = data.get("data", data)

    def find_events(obj, path=""):
        if len(samples) >= 5:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("events", "proEvents", "communityEvents") and isinstance(v, list):
                    for item in v[:2]:
                        if isinstance(item, dict) and (item.get("id") or item.get("title")):
                            samples.append(item)
                            if len(samples) >= 5:
                                return
                else:
                    find_events(v, f"{path}.{k}")
        elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
            for item in obj[:1]:
                find_events(item, path)

    find_events(data)
    return samples


async def main():
    print("Discovering GraphQL schema from AdmitONE...")
    captured = await discover_graphql()

    # Deduplicate requests by operationName
    seen_ops = set()
    unique_requests = []
    for req in captured["requests"]:
        op = req.get("operationName") or "unknown"
        if op not in seen_ops:
            seen_ops.add(op)
            unique_requests.append(req)

    captured["requests"] = unique_requests

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, default=str)

    print(f"\nSaved to {OUTPUT_FILE}")
    print(f"  Requests captured: {len(captured['requests'])}")
    print(f"  Responses captured: {len(captured['responses'])}")
    for req in captured["requests"]:
        print(f"  - {req.get('operationName', 'unknown')}")


if __name__ == "__main__":
    asyncio.run(main())
