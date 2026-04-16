"""
HTTP client with TLS fingerprint impersonation.

Uses curl_cffi to mimic real browser TLS signatures (JA3/JA4),
bypassing bot detection on sites like Alibaba, Amazon, etc.
Falls back to httpx if curl_cffi is not available.
"""

import random
from typing import Optional

# Try curl_cffi first (best anti-detection), fall back to httpx
try:
    from curl_cffi.requests import AsyncSession, BrowserType
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

import httpx

BROWSER_IMPERSONATIONS = [
    "chrome120",
    "chrome119",
    "chrome116",
    "safari17_0",
    "safari15_5",
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}


class StealthClient:
    """
    HTTP client that impersonates real browsers at the TLS level.

    Usage:
        async with StealthClient() as client:
            resp = await client.get("https://example.com")
            print(resp.text)
    """

    def __init__(self, proxy: str = "", timeout: int = 30):
        self.proxy = proxy or None
        self.timeout = timeout
        self._session = None

    async def __aenter__(self):
        if HAS_CURL_CFFI:
            impersonate = random.choice(BROWSER_IMPERSONATIONS)
            self._session = AsyncSession(
                impersonate=impersonate,
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                proxy=self.proxy,
            )
        else:
            self._session = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    **DEFAULT_HEADERS,
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *args):
        if self._session:
            if HAS_CURL_CFFI:
                await self._session.close()
            else:
                await self._session.aclose()

    async def get(self, url: str, **kwargs) -> "Response":
        if HAS_CURL_CFFI:
            resp = await self._session.get(url, **kwargs)
            return Response(
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                url=str(resp.url),
            )
        else:
            resp = await self._session.get(url, **kwargs)
            return Response(
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                url=str(resp.url),
            )


class Response:
    """Unified response object for both backends."""

    def __init__(self, status_code: int, text: str, content: bytes,
                 headers: dict, url: str):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers
        self.url = url

    def json(self):
        import json
        return json.loads(self.text)
