"""Read Loki LogQL results and label names."""

from __future__ import annotations

from typing import Any

import httpx

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient, prometheus_data, require_window


class LokiClient:
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

    async def query_range(self, query: str, start: int, end: int, limit: int = 100) -> Any:
        require_window(start, end)
        if not 1 <= limit <= 1000:
            raise IntegrationError("Loki limit must be 1..1000")
        result = await self.http.request(
            "GET",
            "/loki/api/v1/query_range",
            params={
                "query": query,
                "start": start * 1_000_000_000,
                "end": end * 1_000_000_000,
                "limit": limit,
            },
        )
        return prometheus_data(result, "Loki")

    async def labels(self, start: int, end: int) -> Any:
        require_window(start, end)
        result = await self.http.request(
            "GET",
            "/loki/api/v1/labels",
            params={"start": start * 1_000_000_000, "end": end * 1_000_000_000},
        )
        return prometheus_data(result, "Loki")
