"""Bounded Datadog logs, metrics, monitors, events, spans and incidents reads."""

from __future__ import annotations

from typing import Any

import httpx

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.http import HttpJsonClient, require_window


class DatadogClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        app_key: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = HttpJsonClient(
            url, {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}, timeout_seconds, transport
        )

    async def logs(
        self, query: str, start: int, end: int, limit: int = 100, cursor: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit(limit)
        page: dict[str, Any] = {"limit": limit}
        if cursor:
            page["cursor"] = cursor
        data = await self.http.request(
            "POST",
            "/api/v2/logs/events/search",
            json_body={
                "filter": {"query": query, "from": str(start * 1000), "to": str(end * 1000)},
                "page": page,
                "sort": "-timestamp",
            },
        )
        return _search_result(data, "logs")

    async def metrics(self, query: str, start: int, end: int) -> dict[str, Any]:
        require_window(start, end)
        data = await self.http.request(
            "GET", "/api/v1/query", params={"from": start, "to": end, "query": query}
        )
        if not isinstance(data, dict) or data.get("status") == "error":
            raise IntegrationError("Datadog metrics API returned an error envelope")
        return data

    async def monitors(self, limit: int = 100) -> Any:
        _limit(limit)
        return await self.http.request(
            "GET", "/api/v1/monitor", params={"page": 0, "page_size": limit}
        )

    async def mute_monitor(self, monitor_id: int, end: int) -> dict[str, Any]:
        """Mute one monitor until a Unix-seconds deadline."""
        if monitor_id <= 0 or end <= 0:
            raise ValueError("invalid monitor mute request")
        data = await self.http.write_request(
            "POST", f"/api/v1/monitor/{monitor_id}/mute", json_body={"end": end}
        )
        if not isinstance(data, dict) or str(data.get("id")) != str(monitor_id):
            raise IntegrationError("Datadog mute response has no monitor id")
        return data

    async def monitor_detail(self, monitor_id: int) -> dict[str, Any]:
        if monitor_id <= 0:
            raise ValueError("invalid monitor id")
        data = await self.http.request("GET", f"/api/v1/monitor/{monitor_id}")
        if not isinstance(data, dict) or str(data.get("id")) != str(monitor_id):
            raise IntegrationError("Datadog monitor response has no matching id")
        return data

    async def events(self, query: str, start: int, end: int, limit: int = 100) -> Any:
        require_window(start, end)
        _limit(limit)
        return await self.http.request(
            "GET",
            "/api/v2/events",
            params={
                "filter[query]": query,
                "filter[from]": str(start * 1000),
                "filter[to]": str(end * 1000),
                "page[limit]": limit,
            },
        )

    async def spans(
        self, query: str, start: int, end: int, limit: int = 100, cursor: str | None = None
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit(limit)
        page: dict[str, Any] = {"limit": limit}
        if cursor:
            page["cursor"] = cursor
        data = await self.http.request(
            "POST",
            "/api/v2/spans/events/search",
            json_body={
                "data": {
                    "type": "search_request",
                    "attributes": {
                        "filter": {
                            "query": query,
                            "from": str(start * 1000),
                            "to": str(end * 1000),
                        },
                        "page": page,
                        "sort": "-timestamp",
                    },
                }
            },
        )
        return _search_result(data, "spans")

    async def incidents(self, limit: int = 100) -> Any:
        _limit(limit)
        return await self.http.request("GET", "/api/v2/incidents", params={"page[size]": limit})


def _limit(value: int) -> None:
    if not 1 <= value <= 100:
        raise IntegrationError("Datadog page limit must be 1..100")


def _search_result(data: Any, kind: str) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise IntegrationError(f"Datadog {kind} response has no data list")
    return data
