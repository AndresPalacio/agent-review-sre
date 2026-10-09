"""Fail-closed digest delivery to explicitly configured destinations."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Protocol


class TextClient(Protocol):
    async def send(self, destination: str, payload: str, idempotency_key: str) -> str: ...


class DeliveryLedger(Protocol):
    async def claim_delivery(self, key: str, destination: str) -> str: ...

    async def complete_delivery(self, key: str) -> None: ...


class UncertainDelivery(RuntimeError):
    """A prior send may have reached the destination; never replay automatically."""


async def send_digest_text(
    clients: Mapping[str, TextClient | None],
    ledger: DeliveryLedger,
    text: str,
    destinations: dict[str, str],
    key: str,
) -> None:
    if not key or not text:
        raise ValueError("digest key and text are required")
    if set(destinations) - {"slack", "telegram"}:
        raise ValueError("unsupported digest destination")
    for channel, destination in destinations.items():
        client = clients.get(channel)
        if client is None:
            raise ValueError(f"{channel} delivery is not configured")
        claim_key = f"{key}:{channel}:{destination}"
        status = await ledger.claim_delivery(claim_key, destination)
        if status == "completed":
            continue
        if status != "new":
            raise UncertainDelivery("digest delivery outcome is uncertain")
        payload = escape(text) if channel == "telegram" else text
        await client.send(destination, payload, claim_key)
        await ledger.complete_delivery(claim_key)
