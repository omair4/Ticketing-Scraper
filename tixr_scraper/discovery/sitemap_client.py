"""
Sitemap parser for Tixr group discovery.

Fetches sitemap.xml and group-sitemap-*.xml to extract all group slugs.
Uses Playwright browser to bypass DataDome protection on tixr.com.
"""

import asyncio
import re
from typing import List
from xml.etree import ElementTree
import structlog

from config import SITEMAP_URLS, SITEMAP_INDEX_URL

logger = structlog.get_logger()

# XML namespace for sitemaps
SITEMAP_NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


class SitemapClient:
    """Parses tixr.com sitemaps to discover group slugs."""

    def __init__(self, browser):
        self.browser = browser

    async def _fetch_xml(self, url: str, max_retries: int = 3) -> str:
        """Fetch XML content via Playwright page navigation with retry."""
        for attempt in range(max_retries):
            try:
                await self.browser.page.goto(url, wait_until='domcontentloaded', timeout=30000)
                await asyncio.sleep(3)  # DataDome challenge time

                # Check for DataDome block
                if await self.browser._is_datadome_blocked():
                    logger.warning("datadome_on_sitemap", url=url, attempt=attempt)
                    await asyncio.sleep(5)
                    continue

                # Browsers render XML differently — try multiple extraction methods
                content = await self.browser.page.content()

                # Method 1: Check if page content has XML directly
                if '<urlset' in content or '<sitemapindex' in content:
                    # Extract XML from the HTML wrapper browsers add
                    xml_text = await self.browser.page.evaluate(
                        '() => new XMLSerializer().serializeToString(document)'
                    )
                    if '<urlset' in xml_text or '<sitemapindex' in xml_text:
                        return xml_text

                # Method 2: Some browsers show XML in a <pre> tag
                pre_text = await self.browser.page.evaluate(
                    '() => { const pre = document.querySelector("pre"); return pre ? pre.textContent : null; }'
                )
                if pre_text and ('<urlset' in pre_text or '<sitemapindex' in pre_text):
                    return pre_text

                # Method 3: Get body innerText (loses tags but check anyway)
                inner_text = await self.browser.page.evaluate('() => document.body.innerText')
                if inner_text and ('<urlset' in inner_text or '<sitemapindex' in inner_text):
                    return inner_text

                # Method 4: Fall back to full page content
                if '<loc>' in content:
                    return content

                logger.warning("sitemap_no_xml_found", url=url, attempt=attempt,
                              content_length=len(content))
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)

            except Exception as e:
                logger.warning("sitemap_fetch_error", url=url, attempt=attempt, error=str(e))
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                else:
                    raise

        raise RuntimeError(f"Failed to fetch sitemap XML from {url} after {max_retries} attempts")

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
