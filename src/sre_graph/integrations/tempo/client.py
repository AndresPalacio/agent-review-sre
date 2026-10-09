"""Read Tempo TraceQL search results and traces by ID."""

from __future__ import annotations

import re
from typing import Any

import httpx

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient, require_window

_TRACE_ID = re.compile(r"[0-9a-fA-F]{16}(?:[0-9a-fA-F]{16})?")


class TempoClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        tenant: str | None = None,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if tenant:
            headers["X-Scope-OrgID"] = tenant
        self.http = HttpJsonClient(url, headers, timeout_seconds, transport)

    async def search(self, query: str, start: int, end: int, limit: int = 20) -> Any:
        require_window(start, end)
        if not 1 <= limit <= 100:
            raise IntegrationError("Tempo search limit must be 1..100")
        return await self.http.request(
            "GET", "/api/search", params={"q": query, "start": start, "end": end, "limit": limit}
        )

    async def trace(self, trace_id: str) -> Any:
        if not _TRACE_ID.fullmatch(trace_id):
            raise IntegrationError("Tempo trace id must be 16 or 32 hex characters")
        return await self.http.request(
            "GET", f"/api/v2/traces/{trace_id}", headers={"Accept": "application/json"}
        )
