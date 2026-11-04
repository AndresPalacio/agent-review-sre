"""Small outbound API clients; verify methods never send messages."""

from __future__ import annotations

import re
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import httpx

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.http import HttpJsonClient

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

    async def aclose(self) -> None:
        await self.http.aclose()

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

    async def aclose(self) -> None:
        await self.http.aclose()

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
