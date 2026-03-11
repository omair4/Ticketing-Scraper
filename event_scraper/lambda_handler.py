"""
AWS Lambda handler for AdmitONE inventory scraper.

Each Lambda invocation scrapes a single event. Invoke concurrently for multiple events.

Event payload:
  - eventId (str): Event identifier
  - event_name (str): Event display name
  - venue_name (str): Venue name
  - event_datetime (str): Event date/time (e.g. "Sat. Feb 14, 2026 at 7:00pm")
  - ticket_url (str): Full URL to the AdmitONE event page
  - proxy (str, optional): Proxy URL, e.g. http://host:port or http://user:pass@host:port

Environment (optional):
  - PROXY: Default proxy URL when not provided in event
"""

import asyncio
import json
import os
import structlog
from typing import Any

from config import ScraperConfig
from main import scrape_event
from models import EventInfo, ScrapeResult

# Configure logging for Lambda (structured JSON to CloudWatch)
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def _result_to_response(result: ScrapeResult) -> dict:
    """Serialize ScrapeResult to JSON-serializable dict for Lambda response."""
    data = result.model_dump(mode="json")

    def convert_for_json(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: convert_for_json(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, list):
            return [convert_for_json(item) for item in obj]
        if isinstance(obj, bool):
            return obj
        if hasattr(obj, "__float__") and not isinstance(obj, bool):
            return float(obj)
        return obj

    return convert_for_json(data)


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda entry point. Scrapes one event per invocation.

    Args:
        event: Dict with eventId, event_name, venue_name, event_datetime, ticket_url; optional proxy
        context: Lambda context (unused)

    Returns:
        API Gateway-style response: { statusCode, body } or { statusCode, errorMessage }
    """
    try:
        event_id = event.get("eventId") or event.get("event_id") or "unknown"
        event_name = event.get("event_name") or "Unknown"
        venue_name = event.get("venue_name") or "Unknown"
        event_datetime = event.get("event_datetime") or event.get("event_date")
        ticket_url = event.get("ticket_url")
        proxy = event.get("proxy") or os.environ.get("PROXY")

        if not ticket_url:
            logger.warning("lambda_missing_ticket_url", event_keys=list(event.keys()))
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "ticket_url is required"}),
            }

        event_info_override = EventInfo(
            event_id=str(event_id),
            event_name=event_name,
            venue_name=venue_name,
            event_date=event_datetime,
            event_url=ticket_url,
        )

        config = ScraperConfig(headless=True, proxy=proxy)

        result = asyncio.run(
            scrape_event(
                event_url=ticket_url,
                config=config,
                event_info_override=event_info_override,
            )
        )

        body = _result_to_response(result)
        return {
            "statusCode": 200,
            "body": json.dumps(body, default=str, ensure_ascii=False),
        }

    except Exception as e:
        logger.exception("lambda_scrape_failed", error=str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
