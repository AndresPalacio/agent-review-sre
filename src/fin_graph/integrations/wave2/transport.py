"""Private bounded form/NDJSON transport for fixed vendor endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode

import httpx

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.http import MAX_RESPONSE_BYTES, HttpJsonClient


class Wave2Transport(HttpJsonClient):
    async def stream_read(
        self, path: str, *, form: dict[str, Any] | None = None, body: Any = None
    ) -> list[dict[str, Any]]:
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise IntegrationError("invalid internal read path")
        headers = dict(self.headers)
        content = None
        if form is not None:
            content = urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with (
                    httpx.AsyncClient(
                        headers=headers,
                        transport=self.transport,
                        timeout=self.timeout_seconds,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client,
                    client.stream(
                        "POST", self.base_url + path, content=content, json=body
                    ) as response,
                ):
                    if not 200 <= response.status_code < 300:
                        raise IntegrationError(f"read returned HTTP {response.status_code}")
                    size = response.headers.get("content-length", "")
                    if size.isdecimal() and int(size) > MAX_RESPONSE_BYTES:
                        raise IntegrationError("read response too large")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise IntegrationError("read response too large")
            rows: list[dict[str, Any]] = []
            for line in raw.decode("utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("invalid envelope")
                rows.append(item)
            return rows
        except (httpx.RequestError, TimeoutError, ValueError, UnicodeError) as exc:
            raise IntegrationError(f"stream read failed: {type(exc).__name__}") from None

    async def json_read(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self.request(method, path, **kwargs)
        except TimeoutError:
            raise IntegrationError("read deadline exceeded") from None
