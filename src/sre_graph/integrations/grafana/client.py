"""Read Grafana alert rules, annotations and dashboard index."""

from __future__ import annotations

from typing import Any, cast

import httpx

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient, require_window


class GrafanaClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = HttpJsonClient(url, headers, timeout_seconds, transport)

    async def alert_rules(self) -> list[dict[str, Any]]:
        data = await self.http.request("GET", "/api/v1/provisioning/alert-rules")
        if not isinstance(data, list):
            raise IntegrationError("Grafana alert rules response is not a list")
        return data

    async def annotations(self, start: int, end: int, limit: int = 100) -> list[dict[str, Any]]:
        require_window(start, end)
        if not 1 <= limit <= 100:
            raise IntegrationError("Grafana annotation limit must be 1..100")
        data = await self.http.request(
            "GET",
            "/api/annotations",
            params={"from": start * 1000, "to": end * 1000, "limit": limit},
        )
        if not isinstance(data, list):
            raise IntegrationError("Grafana annotations response is not a list")
        return data

    async def dashboards(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise IntegrationError("Grafana dashboard limit must be 1..100")
        data = await self.http.request(
            "GET", "/api/search", params={"query": query, "type": "dash-db", "limit": limit}
        )
        if not isinstance(data, list):
            raise IntegrationError("Grafana dashboard response is not a list")
        return data

    async def create_annotation(self, text: str, time_ms: int, tags: list[str]) -> int:
        """Create an organization annotation with a bounded text and timestamp."""
        if not text.strip() or len(text) > 1000 or time_ms < 0 or len(tags) > 20:
            raise ValueError("invalid annotation")
        data = await self.http.write_request(
            "POST", "/api/annotations", json_body={"text": text, "time": time_ms, "tags": tags}
        )
        if not isinstance(data, dict) or not isinstance(data.get("id"), int):
            raise IntegrationError("Grafana create annotation returned no id")
        return cast(int, data["id"])

    async def annotation(self, annotation_id: int) -> dict[str, Any]:
        if annotation_id <= 0:
            raise ValueError("invalid annotation id")
        data = await self.http.request("GET", f"/api/annotations/{annotation_id}")
        if not isinstance(data, dict):
            raise IntegrationError("Grafana annotation response is invalid")
        return data
