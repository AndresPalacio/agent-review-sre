from __future__ import annotations

from typing import Any

import pytest

from fin_graph.scheduler.delivery import UncertainDelivery, send_digest_text


class Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send(self, destination: str, payload: str, key: str) -> str:
        self.calls.append((destination, payload, key))
        return "remote-1"


class Ledger:
    def __init__(self) -> None:
        self.status: dict[str, str] = {}

    async def claim_delivery(self, key: str, destination: str) -> str:
        if key in self.status:
            return self.status[key]
        self.status[key] = "pending"
        return "new"

    async def complete_delivery(self, key: str) -> None:
        self.status[key] = "completed"


async def test_digest_delivery_sends_only_explicit_channels_once() -> None:
    slack, telegram, ledger = Client(), Client(), Ledger()
    clients: dict[str, Any] = {"slack": slack, "telegram": telegram}
    await send_digest_text(clients, ledger, "CPU < 1", {"slack": "C1", "telegram": "-1001"}, "day")
    await send_digest_text(clients, ledger, "CPU < 1", {"slack": "C1", "telegram": "-1001"}, "day")
    assert len(slack.calls) == len(telegram.calls) == 1
    assert slack.calls[0][1] == "CPU < 1"
    assert telegram.calls[0][1] == "CPU &lt; 1"


async def test_digest_delivery_never_retries_uncertain_send() -> None:
    slack, ledger = Client(), Ledger()
    ledger.status["day:slack:C1"] = "pending"
    with pytest.raises(UncertainDelivery):
        await send_digest_text({"slack": slack}, ledger, "digest", {"slack": "C1"}, "day")
    assert slack.calls == []
