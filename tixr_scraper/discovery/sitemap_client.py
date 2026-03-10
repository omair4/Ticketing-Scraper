"""
Sitemap parser for Tixr group discovery.

Fetches sitemap.xml and group-sitemap-*.xml to extract all group slugs.
No browser needed — sitemaps are accessible via plain HTTP.
"""

import re
from typing import List
from xml.etree import ElementTree
import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import SITEMAP_URLS, SITEMAP_INDEX_URL

logger = structlog.get_logger()

# XML namespace for sitemaps
SITEMAP_NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


class SitemapClient:
    """Parses tixr.com sitemaps to discover group slugs."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _fetch_xml(self, url: str) -> str:
        """Fetch XML content from URL with retry."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def discover_sitemap_urls(self) -> List[str]:
        """
        Discover group sitemap URLs from the sitemap index.
        Falls back to hardcoded URLs if index parsing fails.
        """
        try:
            xml_text = await self._fetch_xml(SITEMAP_INDEX_URL)
            root = ElementTree.fromstring(xml_text)

            sitemap_urls = []
            for sitemap in root.findall('.//sm:sitemap/sm:loc', SITEMAP_NS):
                url = sitemap.text.strip()
                if 'group-sitemap' in url:
                    sitemap_urls.append(url)

            if sitemap_urls:
                logger.info("sitemap_index_parsed", urls=len(sitemap_urls))
                return sitemap_urls

        except Exception as e:
            logger.warning("sitemap_index_failed", error=str(e))

        # Fallback to hardcoded URLs
        logger.info("using_hardcoded_sitemap_urls", count=len(SITEMAP_URLS))
        return SITEMAP_URLS

    async def fetch_group_slugs(self) -> List[str]:
        """
        Fetch all group slugs from tixr.com sitemaps.

        Returns:
            List of group slugs (e.g., ['greenriverfestival', 'someclub', ...])
        """
        sitemap_urls = await self.discover_sitemap_urls()
        all_slugs = []

        for sitemap_url in sitemap_urls:
            try:
                xml_text = await self._fetch_xml(sitemap_url)
                slugs = self._parse_group_sitemap(xml_text)
                all_slugs.extend(slugs)
                logger.info("sitemap_parsed", url=sitemap_url, slugs=len(slugs))
            except Exception as e:
                logger.warning("sitemap_fetch_failed", url=sitemap_url, error=str(e))

        # Deduplicate
        unique_slugs = list(dict.fromkeys(all_slugs))
        logger.info("total_group_slugs_discovered", count=len(unique_slugs))
        return unique_slugs

    def _parse_group_sitemap(self, xml_text: str) -> List[str]:
        """
        Parse a group-sitemap XML to extract group slugs.

        URLs follow pattern: https://www.tixr.com/groups/{slug}
        """
        slugs = []
        try:
            root = ElementTree.fromstring(xml_text)
            for url_elem in root.findall('.//sm:url/sm:loc', SITEMAP_NS):
                url = url_elem.text.strip()
                match = re.search(r'tixr\.com/groups/([^/\s]+)', url)
                if match:
                    slug = match.group(1)
                    if slug and slug not in ('', 'undefined'):
                        slugs.append(slug)
        except ElementTree.ParseError as e:
            logger.warning("xml_parse_error", error=str(e))

        return slugs
