#!/usr/bin/env python3
"""Quick GraphQL API test: city vs location, pagination, Canada radius."""
import asyncio
import json
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRAPHQL_URL = "https://graphql.admitone.com/"
QUERIES_FILE = Path(__file__).parent / "discovery/graphql_queries.json"

def run(op_name: str, query: str, variables: dict):
    async def _():
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                GRAPHQL_URL,
                json={"operationName": op_name, "query": query, "variables": variables},
                headers={"Content-Type": "application/json"},
            )
            return r.json()
    return asyncio.run(_())

def main():
    data = json.loads(QUERIES_FILE.read_text())
    pro = next(r for r in data["requests"] if r["operationName"] == "PublicProEvents")
    ev = next(r for r in data["requests"] if r["operationName"] == "Events")

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=365)
    dr = {"from": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    base = {"excludeStatus": ["Cancelled"], "startDateRange": dr}

    tests = [
        ("PublicProEvents city=Toronto", pro, {**base, "city": "Toronto", "take": 50, "skip": 0, "distance": 75}),
        ("PublicProEvents city=toronto", pro, {**base, "city": "toronto", "take": 50, "skip": 0, "distance": 75}),
        ("PublicProEvents location+dist=75", pro, {**base, "location": {"latitude": 43.6532, "longitude": -79.3832}, "distance": 75, "take": 50, "skip": 0}),
        ("PublicProEvents dist=5000 Canada", pro, {**base, "location": {"latitude": 56.13, "longitude": -106.35}, "distance": 5000, "take": 100, "skip": 0}),
        ("PublicProEvents take=500", pro, {**base, "location": {"latitude": 43.6532, "longitude": -79.3832}, "distance": 75, "take": 500, "skip": 0}),
        ("Events city=Toronto", ev, {"take": 50, "skip": 0, "city": "Toronto", "orderBy": {"field": "start_date", "sort": 1}, "startDateRange": dr}),
        ("Events city=toronto", ev, {"take": 50, "skip": 0, "city": "toronto", "orderBy": {"field": "start_date", "sort": 1}, "startDateRange": dr}),
        ("Events dist=5000 Canada", ev, {"take": 100, "skip": 0, "location": {"latitude": 56.13, "longitude": -106.35}, "distance": 5000, "orderBy": {"field": "start_date", "sort": 1}, "startDateRange": dr}),
    ]

    for name, q, vars_ in tests:
        r = run(q["operationName"], q["query"], vars_)
        errs = r.get("errors", [])
        d = r.get("data") or {}
        key = "publicProEvents" if "ProEvents" in name else "events"
        blob = d.get(key) or {}
        items, total = blob.get("items", []), blob.get("total", 0)
        print(f"{name}: ok={not errs}, items={len(items)}, total={total}")
        if errs:
            print(f"  errors: {[e.get('message') for e in errs[:2]]}")

if __name__ == "__main__":
    main()