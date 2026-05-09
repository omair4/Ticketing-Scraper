"""Method 1: curl_cffi with Chrome TLS impersonation. No browser."""
from __future__ import annotations

import sys

from poc.shared import (
    URL,
    MethodResult,
    Timer,
    emit,
    is_blocked,
    parse_events,
    proxy_url,
    save_html,
)


def main() -> int:
    result = MethodResult(method="curl_cffi")
    try:
        from curl_cffi import requests as cffi

        proxy = proxy_url()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        with Timer() as t:
            r = cffi.get(
                URL,
                impersonate="chrome131",
                timeout=30,
                proxies=proxies,
                verify=True,
            )
        result.elapsed_s = round(t.elapsed, 3)
        result.status = r.status_code
        body = r.text or ""
        result.html_path = str(save_html("curl_cffi", body))
        blocked, reason = is_blocked(r.status_code, body)
        result.blocked = blocked
        result.block_reason = reason
        if not blocked:
            result.event_count = len(parse_events(body))
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    emit(result)
    return 0 if (not result.error and not result.blocked) else 1


if __name__ == "__main__":
    sys.exit(main())
