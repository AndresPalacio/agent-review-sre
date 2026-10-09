"""Read active alerts and existing silences from Alertmanager v2."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, cast

import httpx

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient


class AlertmanagerClient:
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

    async def alerts(self) -> list[dict[str, Any]]:
        data = await self.http.request("GET", "/api/v2/alerts", params={"active": "true"})
        if not isinstance(data, list):
            raise IntegrationError("Alertmanager alerts response is not a list")
        return data

    async def silences(self) -> list[dict[str, Any]]:
        data = await self.http.request("GET", "/api/v2/silences")
        if not isinstance(data, list):
            raise IntegrationError("Alertmanager silences response is not a list")
        return data

    async def create_silence(
        self,
        matchers: list[dict[str, Any]],
        starts_at: str,
        ends_at: str,
        created_by: str,
        comment: str,
    ) -> str:
        """Create one bounded silence after a separate human approval."""
        if (
            not matchers
            or len(matchers) > 20
            or any(
                not isinstance(item.get("name"), str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["name"])
                or not isinstance(item.get("value"), str)
                or not isinstance(item.get("isRegex"), bool)
                for item in matchers
            )
        ):
            raise ValueError("invalid silence matchers")
        start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or not start < end:
            raise ValueError("silence end must follow start")
        if not created_by.strip() or not comment.strip():
            raise ValueError("silence needs creator and comment")
        data = await self.http.write_request(
            "POST",
            "/api/v2/silences",
            json_body={
                "matchers": matchers,
                "startsAt": starts_at,
                "endsAt": ends_at,
                "createdBy": created_by[:120],
                "comment": comment[:1000],
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("silenceID"), str):
            raise IntegrationError("Alertmanager create silence returned no id")
        return cast(str, data["silenceID"])

    async def expire_silence(self, silence_id: str) -> None:
        """Expire one exact silence id."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", silence_id):
            raise ValueError("invalid silence id")
        await self.http.write_request("DELETE", f"/api/v2/silence/{silence_id}")
