"""
Lambda 1: Provide discovery parameters for Tixr (sitemap-based).

This mirrors the role of `prekindle_provide_show_discovery_params_list.py`:
- It returns a list of "discovery jobs" that a Step Functions Map state can fan out over.

For Tixr, each job is a single `group-sitemap-*.xml` URL discovered from `sitemap.xml`.
Downstream, `tixr_event_discovery` will process one sitemap file per invocation.
"""

from __future__ import annotations

from typing import Any, Dict, List

import asyncio

from .shared.browser_setup import with_tixr_session
from .shared.tixr_api import fetch_sitemap_index


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Entry point for AWS Lambda.

    Input event is currently unused; this function discovers all
    `group-sitemap-*.xml` URLs and returns one discovery job per sitemap URL.
    """

    async def _run() -> List[Dict[str, Any]]:
        async def _inner(browser, page):
            sitemap_urls = await fetch_sitemap_index(page)
            return [
                {
                    "sitemap_url": url,
                    "description": "Tixr group sitemap batch",
                }
                for url in sitemap_urls
            ]

        jobs: List[Dict[str, Any]] = await with_tixr_session(_inner)
        return jobs

    jobs: List[Dict[str, Any]] = asyncio.run(_run())

    return {
        "batches": jobs,
        "total_sitemaps": len(jobs),
    }

