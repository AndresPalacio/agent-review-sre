"""Small outbound API clients; verify methods never send messages."""

from __future__ import annotations

import re
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import httpx

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient

_PD_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_TG_TOKEN = re.compile(r"[A-Za-z0-9:_-]+")


class SlackClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = HttpJsonClient(
            "https://slack.com", {"Authorization": f"Bearer {token}"}, timeout_seconds, transport
        )

    async def verify(self) -> None:
        data = await self.http.request("GET", "/api/auth.test")
        if not isinstance(data, dict) or data.get("ok") is not True or not data.get("user_id"):
            raise IntegrationError("Slack authentication check failed")

    async def send(
        self, destination: str, payload: dict[str, Any] | str, idempotency_key: str
    ) -> str:
        message = {"text": payload} if isinstance(payload, str) else dict(payload)
        message.update(
            {
                "channel": destination,
                "client_msg_id": str(
                    uuid5(NAMESPACE_URL, f"{idempotency_key}:slack:{destination}")
                ),
            }
        )
        data = await self.http.request("POST", "/api/chat.postMessage", json_body=message)
        if (
            not isinstance(data, dict)
            or data.get("ok") is not True
            or not isinstance(data.get("ts"), str)
        ):
            raise IntegrationError("Slack message send failed")
        return cast(str, data["ts"])


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not _TG_TOKEN.fullmatch(token):
            raise ValueError("Telegram token contains unsupported characters")
        self.http = HttpJsonClient(
            f"https://api.telegram.org/bot{token}",
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    async def verify(self) -> None:
        data = await self.http.request("GET", "/getMe")
        if (
            not isinstance(data, dict)
            or data.get("ok") is not True
            or not isinstance(data.get("result"), dict)
        ):
            raise IntegrationError("Telegram authentication check failed")

    async def send(self, destination: str, payload: str, idempotency_key: str) -> str:
        data = await self.http.request(
            "POST",
            "/sendMessage",
            json_body={
                "chat_id": destination,
                "text": payload,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        result = data.get("result") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("ok") is not True
            or not isinstance(result, dict)
            or not isinstance(result.get("message_id"), int)
        ):
            raise IntegrationError("Telegram message send failed")
        return str(result["message_id"])


class PagerDutyClient:
    def __init__(
        self,
        token: str,
        from_email: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.http = HttpJsonClient(
            "https://api.pagerduty.com",
            {
                "Authorization": f"Token token={token}",
                "Accept": "application/vnd.pagerduty+json;version=2",
                "From": from_email,
            },
            timeout_seconds,
            transport,
        )

    async def verify(self) -> None:
        data = await self.http.request("GET", "/users/me")
        if not isinstance(data, dict) or not isinstance(data.get("user"), dict):
            raise IntegrationError("PagerDuty authentication check failed")

    async def send(self, destination: str, payload: str, idempotency_key: str) -> str:
        if not _PD_ID.fullmatch(destination):
            raise IntegrationError("PagerDuty incident id has unsupported characters")
        data = await self.http.request(
            "POST", f"/incidents/{destination}/notes", json_body={"note": {"content": payload}}
        )
        note = data.get("note") if isinstance(data, dict) else None
        if not isinstance(note, dict) or not isinstance(note.get("id"), str):
            raise IntegrationError("PagerDuty note send failed")
        return cast(str, note["id"])

    async def update_incident_status(self, incident_id: str, status: str) -> str:
        """Acknowledge or resolve one PagerDuty incident after approval."""
        if not _PD_ID.fullmatch(incident_id) or status not in {"acknowledged", "resolved"}:
            raise ValueError("invalid PagerDuty status change")
        data = await self.http.write_request(
            "PUT",
            f"/incidents/{incident_id}",
            json_body={"incident": {"type": "incident_reference", "status": status}},
        )
        incident = data.get("incident") if isinstance(data, dict) else None
        if not isinstance(incident, dict) or incident.get("id") != incident_id:
            raise IntegrationError("PagerDuty update returned no matching incident")
        return str(incident.get("status", ""))

    async def incident_detail(self, incident_id: str) -> dict[str, Any]:
        if not _PD_ID.fullmatch(incident_id):
            raise ValueError("invalid PagerDuty incident id")
        data = await self.http.request("GET", f"/incidents/{incident_id}")
        incident = data.get("incident") if isinstance(data, dict) else None
        if not isinstance(incident, dict) or incident.get("id") != incident_id:
            raise IntegrationError("PagerDuty incident response has no matching id")
        return incident
