"""Sentry issue, event and release reads with cursor preservation."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.http import HttpJsonClient, require_window
from fin_graph.integrations.pagination import next_link_value

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}")


def _segment(value: str) -> str:
    if not _SEGMENT.fullmatch(value):
        raise IntegrationError("Sentry identifier has unsupported characters")
    return value


def _limit(value: int) -> None:
    if not 1 <= value <= 100:
        raise IntegrationError("Sentry page limit must be 1..100")


def _time(value: int) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


class SentryClient:
    def __init__(
        self,
        url: str,
        organization: str,
        token: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.organization = _segment(organization)
        self.http = HttpJsonClient(
            url, {"Authorization": f"Bearer {token}"}, timeout_seconds, transport
        )

    @property
    def _root(self) -> str:
        return f"/api/0/organizations/{self.organization}"

    async def _page(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        data, headers = await self.http.request_with_headers("GET", path, params=params)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise IntegrationError("Sentry API returned no list")
        return {"items": data, "nextCursor": next_link_value(headers.get("link"), "cursor")}

    async def issues(
        self,
        query: str,
        start: int,
        end: int,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit(limit)
        params: dict[str, Any] = {
            "query": query,
            "start": _time(start),
            "end": _time(end),
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return await self._page(f"{self._root}/issues/", params)

    async def issue_detail(self, issue_id: str) -> dict[str, Any]:
        data = await self.http.request("GET", f"{self._root}/issues/{_segment(issue_id)}/")
        if not isinstance(data, dict) or "id" not in data:
            raise IntegrationError("Sentry issue response has no id")
        return data

    async def update_issue_status(self, issue_id: str, status: str) -> dict[str, Any]:
        """Resolve or ignore one issue after approval."""
        if status not in {"resolved", "ignored"}:
            raise ValueError("status must be resolved or ignored")
        data = await self.http.write_request(
            "PUT", f"{self._root}/issues/{_segment(issue_id)}/", json_body={"status": status}
        )
        if not isinstance(data, dict) or str(data.get("id")) != issue_id:
            raise IntegrationError("Sentry update issue returned no matching id")
        return data

    async def events(
        self,
        issue_id: str,
        start: int,
        end: int,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit(limit)
        params: dict[str, Any] = {
            "start": _time(start),
            "end": _time(end),
            "per_page": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return await self._page(f"{self._root}/issues/{_segment(issue_id)}/events/", params)

    async def event_detail(self, issue_id: str, event_id: str) -> dict[str, Any]:
        data = await self.http.request(
            "GET", f"{self._root}/issues/{_segment(issue_id)}/events/{_segment(event_id)}/"
        )
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise IntegrationError("Sentry event response has no entries")
        return data

    async def releases(self, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        _limit(limit)
        params: dict[str, Any] = {"per_page": limit}
        if cursor:
            params["cursor"] = cursor
        return await self._page(f"{self._root}/releases/", params)

    async def organization_read(self) -> dict[str, Any]:
        data = await self.http.request("GET", f"{self._root}/")
        if not isinstance(data, dict) or data.get("slug") != self.organization:
            raise IntegrationError("Sentry organization response is unavailable")
        return data
