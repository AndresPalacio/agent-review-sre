"""GitHub REST change readers with bounded pages and time windows."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

import httpx

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient, require_window
from sre_graph.integrations.pagination import next_link_value

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,200}")


def _segment(value: str) -> str:
    if not _SEGMENT.fullmatch(value) or value in (".", ".."):
        raise IntegrationError("GitHub identifier has unsupported characters")
    return value


def _ref(value: str) -> str:
    if not _REF.fullmatch(value) or ".." in value or "//" in value:
        raise IntegrationError("GitHub ref has unsupported characters")
    return quote(value, safe="")


def _limit_page(limit: int, page: int) -> None:
    if not 1 <= limit <= 100 or not 1 <= page <= 100:
        raise IntegrationError("GitHub limit and page must be 1..100")


def _time(value: int) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


class GitHubClient:
    def __init__(
        self,
        url: str,
        owner: str,
        repo: str,
        token: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.owner = _segment(owner)
        self.repo = _segment(repo)
        self.http = HttpJsonClient(
            url,
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            timeout_seconds,
            transport,
        )

    @property
    def _root(self) -> str:
        return f"/repos/{self.owner}/{self.repo}"

    async def create_issue(self, title: str, body: str) -> int:
        """Create a bounded follow-up issue after approval."""
        if not title.strip() or len(title) > 256 or len(body) > 10000:
            raise ValueError("invalid GitHub issue")
        data = await self.http.write_request(
            "POST", f"{self._root}/issues", json_body={"title": title, "body": body}
        )
        if not isinstance(data, dict) or not isinstance(data.get("number"), int):
            raise IntegrationError("GitHub create issue returned no number")
        return cast(int, data["number"])

    async def issue_detail(self, number: int) -> dict[str, Any]:
        if number <= 0:
            raise ValueError("invalid issue number")
        data = await self.http.request("GET", f"{self._root}/issues/{number}")
        if not isinstance(data, dict) or data.get("number") != number:
            raise IntegrationError("GitHub issue response has no matching number")
        return data

    async def repository(self) -> dict[str, Any]:
        data = await self.http.request("GET", self._root)
        if not isinstance(data, dict) or "full_name" not in data:
            raise IntegrationError("GitHub repository response is invalid")
        return data

    async def _page(
        self,
        path: str,
        params: dict[str, Any],
        *,
        key: str | None = None,
    ) -> dict[str, Any]:
        data, headers = await self.http.request_with_headers("GET", path, params=params)
        items = data.get(key) if key and isinstance(data, dict) else data
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise IntegrationError("GitHub API returned no item list")
        raw_next = next_link_value(headers.get("link"), "page")
        next_page = int(raw_next) if raw_next and raw_next.isdecimal() else None
        return {"items": items, "nextPage": next_page}

    async def deployments(
        self, start: int, end: int, limit: int = 50, page: int = 1
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit_page(limit, page)
        result = await self._page(f"{self._root}/deployments", {"per_page": limit, "page": page})
        result["items"] = [
            item
            for item in result["items"]
            if isinstance(item.get("created_at"), str)
            and _time(start) <= item["created_at"].replace("Z", "+00:00") <= _time(end)
        ]
        return result

    async def commits(
        self,
        start: int,
        end: int,
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit_page(limit, page)
        return await self._page(
            f"{self._root}/commits",
            {"since": _time(start), "until": _time(end), "per_page": limit, "page": page},
        )

    async def pulls(
        self,
        start: int,
        end: int,
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit_page(limit, page)
        result = await self._page(
            f"{self._root}/pulls",
            {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": limit,
                "page": page,
            },
        )
        result["items"] = [
            item
            for item in result["items"]
            if (
                not isinstance(item.get("updated_at"), str)
                or _time(start) <= item["updated_at"].replace("Z", "+00:00") <= _time(end)
            )
        ]
        return result

    async def workflow_runs(
        self,
        start: int,
        end: int,
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        require_window(start, end)
        _limit_page(limit, page)
        return await self._page(
            f"{self._root}/actions/runs",
            {"created": f"{_time(start)}..{_time(end)}", "per_page": limit, "page": page},
            key="workflow_runs",
        )

    async def compare(self, base: str, head: str) -> dict[str, Any]:
        data = await self.http.request("GET", f"{self._root}/compare/{_ref(base)}...{_ref(head)}")
        if not isinstance(data, dict) or "status" not in data:
            raise IntegrationError("GitHub compare response has no status")
        return data

    async def repository_read(self) -> dict[str, Any]:
        data = await self.http.request("GET", self._root)
        if not isinstance(data, dict) or data.get("full_name") != f"{self.owner}/{self.repo}":
            raise IntegrationError("GitHub repository response is unavailable")
        return data
