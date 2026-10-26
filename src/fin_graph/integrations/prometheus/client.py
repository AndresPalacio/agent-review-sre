"""Prometheus HTTP API, also used by Mimir."""

from __future__ import annotations

from typing import Any

import httpx

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.http import HttpJsonClient, prometheus_data, require_window


class PrometheusClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        tenant: str | None = None,
        timeout_seconds: float = 15.0,
        vendor: str = "Prometheus",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if tenant:
            headers["X-Scope-OrgID"] = tenant
        self.http = HttpJsonClient(url, headers, timeout_seconds, transport)
        self.vendor = vendor

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return prometheus_data(await self.http.request("GET", path, params=params), self.vendor)

    async def query(self, expression: str, at: int | None = None) -> Any:
        params: dict[str, Any] = {"query": expression}
        if at is not None:
            params["time"] = at
        return await self._get("/api/v1/query", params)

    async def query_range(self, expression: str, start: int, end: int, step: int) -> Any:
        require_window(start, end)
        if step <= 0:
            raise IntegrationError("query step must be positive")
        return await self._get(
            "/api/v1/query_range", {"query": expression, "start": start, "end": end, "step": step}
        )

    async def labels(self, start: int, end: int) -> Any:
        require_window(start, end)
        return await self._get("/api/v1/labels", {"start": start, "end": end})

    async def series(self, selector: str, start: int, end: int) -> Any:
        require_window(start, end)
        return await self._get("/api/v1/series", {"match[]": selector, "start": start, "end": end})

    async def alerts(self) -> Any:
        return await self._get("/api/v1/alerts")

    async def rules(self) -> Any:
        return await self._get("/api/v1/rules")

    async def targets(self) -> Any:
        return await self._get("/api/v1/targets", {"state": "active"})
