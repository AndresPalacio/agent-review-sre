"""Inbound bot adapters with server-owned persistence and analysis callbacks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class GatewayResult:
    thread_id: str
    report_text: str
    approval_kind: str | None = None
    report_blocks: list[dict[str, Any]] | None = None
    approval_id: str | None = None


class GatewayRunPending(Exception):
    """An existing worker still owns the run; replay it later without a failed attempt."""


class GatewayLeaseLost(Exception):
    """Another worker owns this event and may publish its result."""


class GatewayStore(Protocol):
    async def claim_gateway_event(
        self,
        platform: str,
        event_id: str,
        payload: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> bool: ...

    async def pending_gateway_events(
        self, platform: str, before: datetime, *, limit: int = 100
    ) -> list[tuple[str, dict[str, Any]]]: ...

    async def claim_stale_gateway_event(
        self, platform: str, event_id: str, before: datetime, owner: str | None = None
    ) -> bool: ...

    async def touch_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool: ...

    async def complete_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool: ...

    async def fail_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool: ...

    async def bind_gateway_thread(
        self,
        platform: str,
        destination: str,
        remote_id: str,
        thread_id: str,
        *,
        approval_id: str | None = None,
    ) -> None: ...

    async def gateway_thread(
        self, platform: str, destination: str, remote_id: str
    ) -> str | None: ...

    async def gateway_approval_id(
        self, platform: str, destination: str, remote_id: str
    ) -> str | None: ...


SubmitRequest = Callable[[str, str, str, str, str], Awaitable[GatewayResult]]
DecideApproval = Callable[[str, str, str, str, str], Awaitable[str]]
