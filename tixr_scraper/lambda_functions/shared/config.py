from __future__ import annotations

from dataclasses import dataclass


TIXR_BASE_URL = "https://www.tixr.com"
SITEMAP_URL = f"{TIXR_BASE_URL}/sitemap.xml"
SEATSIO_CDN_BASE = "https://cdn-na.seatsio.net"


RATE_LIMIT_DELAY_MS = 300
MAX_RETRIES = 3
SITEMAP_BATCH_SIZE = 50


@dataclass(frozen=True)
class TixrApiPaths:
    """Centralised path patterns for Tixr browser-only APIs."""

    PAGE_REQUIREMENTS: str = "/api/page/requirements"
    GROUPS: str = "/api/groups"
    EVENTS: str = "/api/events"


PATHS = TixrApiPaths()

