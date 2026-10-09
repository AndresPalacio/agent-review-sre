"""Bounded JSON HTTP reads shared by observability integrations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from sre_graph.integrations.base import IntegrationError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WINDOW_SECONDS = 7 * 24 * 60 * 60


class HttpJsonClient:
    """Make one read request at a time without leaking credentials on failures."""

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) URL without credentials or query")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Return JSON for GET/POST, rejecting redirects, large bodies and unsafe paths."""
        payload, _ = await self.request_with_headers(
            method, path, params=params, json_body=json_body, headers=headers
        )
        return payload

    async def request_with_headers(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        _allow_write: bool = False,
    ) -> tuple[Any, Mapping[str, str]]:
        """Return JSON and only the Link header needed for safe pagination."""
        verb = method.upper()
        if _allow_write:
            if verb not in ("POST", "PUT", "PATCH", "DELETE"):
                raise ValueError("write request requires POST, PUT, PATCH or DELETE")
        elif verb not in ("GET", "POST"):
            raise ValueError("only read GET and POST requests are supported")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(part == ".." for part in path.split("/"))
            or "?" in path
            or "#" in path
        ):
            raise ValueError("path must be an internal absolute path without a query")
        merged = {**self.headers, **(headers or {})}
        try:
            async with (
                httpx.AsyncClient(
                    headers=merged,
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    verb, self.base_url + path, params=params, json=json_body
                ) as response,
            ):
                if not 200 <= response.status_code < 300:
                    raise IntegrationError(f"HTTP {verb} {path} returned {response.status_code}")
                size = response.headers.get("content-length")
                if size and size.isdecimal() and int(size) > MAX_RESPONSE_BYTES:
                    raise IntegrationError(f"HTTP {verb} {path} response too large")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise IntegrationError(f"HTTP {verb} {path} response too large")
                link = response.headers.get("link")
        except (httpx.RequestError, UnicodeError, ValueError) as exc:
            raise IntegrationError(f"HTTP {verb} {path} failed: {type(exc).__name__}") from exc
        if _allow_write and not body:
            return {}, ({"link": link} if link else {})
        try:
            return json.loads(body), ({"link": link} if link else {})
        except (ValueError, UnicodeDecodeError) as exc:
            raise IntegrationError(f"HTTP {verb} {path} returned invalid JSON") from exc

    async def write_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Perform an explicit mutation with the same URL, size and secret safeguards."""
        payload, _ = await self.request_with_headers(
            method, path, json_body=json_body, headers=headers, _allow_write=True
        )
        return payload


def prometheus_data(payload: Any, vendor: str) -> Any:
    """Extract a successful Prometheus-compatible response without echoing server errors."""
    if not isinstance(payload, dict) or payload.get("status") != "success" or "data" not in payload:
        raise IntegrationError(f"{vendor} API returned an error envelope")
    return payload["data"]


def require_window(start: int, end: int, max_seconds: int = MAX_WINDOW_SECONDS) -> None:
    """Bound a Unix-seconds query window before asking a provider to scan it."""
    if start < 0 or end <= start or end - start > max_seconds:
        raise IntegrationError(f"invalid query window: use 0 < start < end within {max_seconds}s")
