"""An async Yahoo session: browser user agent, cookie and crumb, 429 backoff, bounded reads.

Ported from ai-hedge-fund ``src/data/providers/yahoo_provider.py`` (MIT). Each request opens a
short-lived ``httpx.AsyncClient`` that shares one cookie jar, so no connection outlives the run.
Only Yahoo hosts are reachable, redirects are refused and bodies are capped.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from fin_graph.integrations.base import HttpStatusError, IntegrationError
from fin_graph.integrations.http import MAX_RESPONSE_BYTES

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ALLOWED_HOSTS = frozenset(
    {
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com",
        "fc.yahoo.com",
        "feeds.finance.yahoo.com",
    }
)
COOKIE_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 5.0, 10.0)


class YahooSession:
    """Read-only GETs against Yahoo; refreshes cookie and crumb once on 401/403."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff: Sequence[float] = BACKOFF_SECONDS,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.backoff = tuple(backoff)
        self._cookies = httpx.Cookies()
        self._crumb: str | None = None
        self._crumb_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    def _active_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise RuntimeError("Yahoo session is closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"},
                cookies=self._cookies,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def aclose(self) -> None:
        """Close the run-owned connection pool once."""
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.aclose()

    def reset(self) -> None:
        self._cookies = httpx.Cookies()
        self._crumb = None
        if self._client is not None:
            self._client.cookies.clear()

    async def _send(self, url: str, params: Mapping[str, Any] | None) -> httpx.Response:
        host = urlsplit(url).hostname
        if host not in ALLOWED_HOSTS:
            raise IntegrationError(f"Yahoo request to an unexpected host was refused: {host}")
        if self._closed:
            raise RuntimeError("Yahoo session is closed")
        try:
            client = self._active_client()
            async with client.stream("GET", url, params=params) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise IntegrationError(f"Yahoo response too large: {urlsplit(url).path}")
            self._cookies.update(client.cookies)
            # The body is already decoded: keep only the charset, not content-encoding.
            content_type = response.headers.get("content-type", "")
            return httpx.Response(
                response.status_code,
                headers={"content-type": content_type} if content_type else None,
                content=bytes(body),
            )
        except httpx.RequestError as exc:
            raise IntegrationError(
                f"Yahoo request {urlsplit(url).path} failed: {type(exc).__name__}"
            ) from exc

    async def _with_backoff(self, url: str, params: Mapping[str, Any] | None) -> httpx.Response:
        response = await self._send(url, params)
        for delay in self.backoff:
            if response.status_code != 429:
                break
            await asyncio.sleep(delay)
            response = await self._send(url, params)
        return response

    async def crumb(self) -> str | None:
        """The crumb quoteSummary needs, fetched once per session (cookies first)."""
        async with self._crumb_lock:
            if self._crumb is None:
                # fc.yahoo.com answers with an error page; only its cookies matter.
                with contextlib.suppress(IntegrationError):
                    await self._send(COOKIE_URL, None)
                response = await self._with_backoff(CRUMB_URL, None)
                text = response.text.strip() if response.status_code == 200 else ""
                if text and "<" not in text and len(text) < 64:
                    self._crumb = text
            return self._crumb

    async def get(
        self, url: str, params: Mapping[str, Any] | None = None, *, needs_crumb: bool = False
    ) -> httpx.Response:
        """A successful response, or :class:`HttpStatusError` with the final status."""
        query = dict(params or {})
        if needs_crumb and (crumb := await self.crumb()):
            query["crumb"] = crumb
        response = await self._with_backoff(url, query)
        if response.status_code in (401, 403):
            self.reset()
            if needs_crumb and (crumb := await self.crumb()):
                query["crumb"] = crumb
            response = await self._with_backoff(url, query)
        if response.status_code != 200:
            raise HttpStatusError(
                f"Yahoo {urlsplit(url).path} returned {response.status_code}", response.status_code
            )
        return response

    async def get_json(
        self, url: str, params: Mapping[str, Any] | None = None, *, needs_crumb: bool = False
    ) -> Any:
        response = await self.get(url, params, needs_crumb=needs_crumb)
        try:
            return response.json()
        except ValueError as exc:
            raise IntegrationError(f"Yahoo {urlsplit(url).path} returned invalid JSON") from exc
