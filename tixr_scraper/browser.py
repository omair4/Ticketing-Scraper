"""
Playwright browser automation for Tixr.

This mirrors the structure of the other browser modules and is the main
entrypoint for automated network reconnaissance on Tixr + Seats.io flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import structlog
from playwright.async_api import async_playwright, Browser, Page

from .config import ScraperConfig, BUTTON_SELECTORS, is_tixr_domain


logger = structlog.get_logger()


def _parse_proxy(proxy_url: Optional[str]) -> Optional[dict]:
    """
    Parse proxy URL into Playwright proxy dict.
    Supports http://host:port and http://username:password@host:port
    """
    if not proxy_url or not proxy_url.strip():
        return None
    try:
        parsed = urlparse(proxy_url.strip())
        if not parsed.hostname:
            return None
        server = f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or 80}"
        proxy_dict: dict = {"server": server}
        if parsed.username:
            proxy_dict["username"] = parsed.username
        if parsed.password:
            proxy_dict["password"] = parsed.password
        return proxy_dict
    except Exception as e:
        logger.warning(
            "proxy_parse_failed",
            proxy_url=proxy_url[:80] if proxy_url else "",
            error=str(e),
        )
        return None


@dataclass
class CapturedResponse:
    """Captured network response data."""

    url: str
    status: int
    content_type: str
    body: Optional[str] = None

    def is_json(self) -> bool:
        ct = (self.content_type or "").lower()
        return "application/json" in ct or "text/json" in ct


@dataclass
class NavigationResult:
    """Result of navigating to a Tixr event."""

    success: bool
    current_url: str
    captured_responses: List[CapturedResponse]
    page_html: str
    scenario_detected: Optional[str] = None
    error: Optional[str] = None


class BrowserAutomation:
    """
    Async context manager encapsulating the Playwright browser lifecycle
    and capturing relevant Tixr / Seats.io network traffic.
    """

    def __init__(self, config: ScraperConfig):
        self.config = config
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._captured_responses: List[CapturedResponse] = []

    async def __aenter__(self) -> "BrowserAutomation":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context_options: dict = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": self.config.user_agent,
        }
        proxy_dict = _parse_proxy(self.config.proxy)
        if proxy_dict:
            context_options["proxy"] = proxy_dict
            logger.info("browser_using_proxy", server=proxy_dict.get("server"))

        context = await self._browser.new_context(**context_options)
        self._page = await context.new_page()

        # Basic anti-bot hardening used in other scrapers
        await self._page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            """
        )

        # Attach network listener
        self._page.on("response", self._on_response)

        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._playwright:
                await self._playwright.stop()

    async def _on_response(self, response):
        """
        Capture Tixr + Seats.io responses for later parsing.

        This is our automated reconnaissance layer: it focuses on JSON/text
        APIs and critical endpoints even if the content-type is unusual.
        """
        try:
            url = response.url
            # We care about:
            # - Tixr APIs on tixr.com
            # - Seats.io APIs used for reserved/season tickets
            relevant_patterns = [
                "seatsio.net",
                "rendering-info",
                "/api/events/",
                "/api/page/requirements",
                "/graphql",
                "/api/",
            ]
            if not (is_tixr_domain(url) or any(p in url for p in relevant_patterns)):
                return

            content_type = response.headers.get("content-type", "")
            body_text: Optional[str] = None

            # Important endpoints where we always want bodies if possible
            critical_patterns = [
                "cdn-na.seatsio.net",
                "cdn-eu.seatsio.net",
                "rendering-info",
                "object-statuses",
                "/api/events/",
                "/api/page/requirements",
            ]

            try:
                if response.ok:
                    must_capture_body = any(p in url for p in critical_patterns)
                    if must_capture_body or any(
                        token in content_type.lower()
                        for token in ("json", "text", "application/")
                    ):
                        try:
                            body_text = await response.text()
                        except Exception:
                            # Fallback: capture raw bytes and best-effort decode
                            try:
                                body_bytes = await response.body()
                            except Exception:
                                body_bytes = b""
                            if body_bytes:
                                try:
                                    body_text = body_bytes.decode("utf-8")
                                except Exception:
                                    import base64

                                    body_text = base64.b64encode(body_bytes).decode(
                                        "utf-8"
                                    )
            except Exception:
                # Some responses have no body or streaming content; metadata is still useful
                body_text = None

            self._captured_responses.append(
                CapturedResponse(
                    url=url,
                    status=response.status,
                    content_type=content_type,
                    body=body_text,
                )
            )
            logger.debug(
                "captured_response",
                url=url[:160],
                status=response.status,
                has_body=body_text is not None,
            )
        except Exception as e:
            logger.debug("capture_response_failed", error=str(e))

    async def navigate_to_tickets(self, event_url: str) -> NavigationResult:
        """
        Navigate to a Tixr event page, click through to the ticketing interface,
        and capture relevant network responses.

        The exact clicking strategy and scenario detection will be filled in
        after we finalize the Tixr DOM/flow analysis.
        """
        page = self._page
        assert page is not None

        try:
            logger.info("navigate_start", url=event_url)
            await page.goto(event_url, timeout=self.config.navigation_timeout)
            await page.wait_for_timeout(self.config.page_load_delay)

            # TODO: click the appropriate buy button(s) based on BUTTON_SELECTORS
            # For now we just capture the initial page state.

            html = await page.content()
            scenario = self._detect_scenario()

            # Best-effort debug dump of captured responses so we can
            # inspect real Tixr API endpoints without attaching devtools.
            try:
                self._dump_captured_responses_debug()
            except Exception as e:
                logger.debug("debug_dump_failed", error=str(e))

            return NavigationResult(
                success=True,
                current_url=page.url,
                captured_responses=list(self._captured_responses),
                page_html=html,
                scenario_detected=scenario,
            )
        except Exception as e:
            logger.error("navigation_failed", error=str(e))
            return NavigationResult(
                success=False,
                current_url=page.url if page else event_url,
                captured_responses=list(self._captured_responses),
                page_html="",
                scenario_detected=None,
                error=str(e),
            )

    def _dump_captured_responses_debug(self) -> None:
        """
        Dump basic info about captured responses to a JSON file in the
        configured output directory. This is purely for development /
        reconnaissance and safe to leave enabled.
        """
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = output_dir / f"tixr_network_{ts}.json"

        import json

        # Only log lightweight metadata + short body snippets to avoid huge files.
        def classify(url: str, content_type: str) -> str:
            if "seatsio.net" in url and "rendering-info" in url:
                return "seatsio_rendering_info"
            if "seatsio.net" in url and "object-statuses" in url:
                return "seatsio_object_statuses"
            if "seatsio.net" in url:
                return "seatsio_other"
            if "/api/events/" in url:
                return "tixr_event_api"
            if "/api/page/requirements" in url:
                return "tixr_page_requirements"
            if "juicer.io/api/posts" in url:
                return "juicer_social_feed"
            if "/api/" in url:
                return "other_api"
            return "other"

        serializable = []
        for r in self._captured_responses:
            entry = {
                "url": r.url,
                "status": r.status,
                "content_type": r.content_type,
                "kind": classify(r.url, r.content_type or ""),
            }
            if r.body:
                # Short preview of body for quick inspection
                entry["body_preview"] = r.body[:500]
            serializable.append(entry)

        filename.write_text(json.dumps(serializable, indent=2), encoding="utf-8")

    def _detect_scenario(self) -> Optional[str]:
        """
        Inspect captured responses and infer which scraper scenario should be used.

        This is a placeholder; once we know the real API shapes we can implement
        concrete rules (e.g., GA JSON API vs. seatmap API).
        """
        # Example of future heuristics:
        # - if "/inventory" in url → "ga"
        # - if "/seatmap" in url or response contains coordinates → "seatmap_api"
        return None

