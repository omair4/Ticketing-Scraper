"""
Shared auth for MGM Resorts API. Uses curl_cffi (Chrome TLS impersonation) when available;
falls back to Playwright browser fetch on timeout for local runs (e.g. run_pipeline.py).
"""
import asyncio

AUTH_URL = "https://identityapi.mgmresorts.com/identity/authorization/v2/anon/user/token"

try:
    from curl_cffi import requests as _requests
    _IMPERSONATE = "chrome120"
except ImportError:
    import requests as _requests  # type: ignore[no-redef]
    _IMPERSONATE = None

_AUTH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://mgmgrand.mgmresorts.com",
    "Referer": "https://mgmgrand.mgmresorts.com/",
}


async def _get_token_via_browser():
    """Fetch token via headed browser (bypasses Akamai when curl_cffi still fails)."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://www.mgmresorts.com/", wait_until="commit", timeout=60000)
            body = await page.evaluate(f"""async () => {{
                const resp = await fetch('{AUTH_URL}');
                if (!resp.ok) throw new Error('Auth endpoint HTTP ' + resp.status);
                return await resp.json();
            }}""")
            await context.close()
        finally:
            await browser.close()
    token = body.get("access_token") or body.get("token") or body.get("id_token")
    if not token and isinstance(body, dict):
        for v in body.values():
            if isinstance(v, str) and len(v) > 50:
                token = v
                break
    if not token:
        raise ValueError(f"Playwright: no token in auth response. Keys: {list(body.keys())}")
    return token


def get_anon_token(proxy=None):
    """Fetch anonymous bearer token. Uses curl_cffi when available; Playwright fallback on timeout."""
    timeout = 25
    extra = {"impersonate": _IMPERSONATE} if _IMPERSONATE else {}
    try:
        resp = _requests.get(
            AUTH_URL,
            headers=_AUTH_HEADERS,
            proxies=proxy,
            timeout=timeout,
            **extra,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("access_token") or data.get("id_token")
        if not token and isinstance(data, dict):
            for v in data.values():
                if isinstance(v, str) and len(v) > 50:
                    token = v
                    break
        if not token:
            raise ValueError(f"Could not find token in auth response: {list(data.keys())}")
        return token
    except (
        _requests.exceptions.ReadTimeout,
        _requests.exceptions.ConnectionError,
        _requests.exceptions.SSLError,
        _requests.exceptions.HTTPError,
    ) as e:
        try:
            return asyncio.run(_get_token_via_browser())
        except Exception as e2:
            raise e2 from e
