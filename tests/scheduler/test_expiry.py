"""Approval expiry must reject only still-pending requests under the thread lock."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.types import Command

from fin_graph.integrations.context import RunContext
from fin_graph.scheduler.expiry import expire_pending_approvals

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


@dataclass
class FakeStorage:
    touched: dict[tuple[str, str], datetime]
    records: list[tuple[str, str, str, str, str, str]] = field(default_factory=list)
    list_calls: int = 0

    @asynccontextmanager
    async def approval_lock(self, kind: str, thread_id: str) -> AsyncIterator[None]:
        yield

    async def list_approval_candidates(self, cutoff: datetime) -> list[tuple[str, str]]:
        self.list_calls += 1
        return [key for key, when in self.touched.items() if when < cutoff]

    async def touch_checkpoint_thread(self, thread_id: str, kind: str) -> None:
        self.touched[kind, thread_id] = NOW

    async def record_approval(
        self,
        thread_id: str,
        kind: str,
        actor: str,
        decision: str,
        action_name: str,
        args_digest: str,
    ) -> None:
        self.records.append((thread_id, kind, actor, decision, action_name, args_digest))


class FakeGraph:
    def __init__(self, requests: list[dict[str, Any]]) -> None:
        self.requests = requests
        self.resumptions: list[tuple[Command[Any], dict[str, Any], RunContext]] = []

    async def aget_state(self, config: dict[str, Any]) -> SimpleNamespace:
        del config
        interrupts = (
            (SimpleNamespace(value={"action_requests": self.requests}),) if self.requests else ()
        )
        return SimpleNamespace(tasks=(SimpleNamespace(interrupts=interrupts),))

    async def ainvoke(
        self, command: Command[Any], config: dict[str, Any], *, context: RunContext
    ) -> dict[str, Any]:
        self.resumptions.append((command, config, context))
        self.requests = []
        return {"outcome": "rejected"}


def _run(
    storage: FakeStorage,
    chat_graph: FakeGraph,
    order_graph: FakeGraph,
    *,
    chat_locks: dict[str, asyncio.Lock] | None = None,
    order_locks: dict[str, asyncio.Lock] | None = None,
) -> Any:
    return expire_pending_approvals(
        storage=storage,
        chat_graph=chat_graph,
        order_graph=order_graph,
        chat_locks=chat_locks if chat_locks is not None else {},
        order_locks=order_locks if order_locks is not None else {},
        context_factory=lambda: RunContext(clients={"read_only": object()}),
        ttl=timedelta(hours=24),
        now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_expired_paper_order_rejected_and_audited() -> None:
    storage = FakeStorage({("orders", "r1"): NOW - timedelta(hours=25)})
    graph = FakeGraph(
        [{"name": "place_paper_order", "arguments": {"ticker": "DEEP", "quantity": 1}}]
    )

    count = await _run(storage, FakeGraph([]), graph)

    assert count == 1
    assert graph.requests == []
    command, config, context = graph.resumptions[0]
    assert command.resume == {"decisions": [{"type": "reject"}]}
    assert config == {"configurable": {"thread_id": "orders:r1"}}
    assert context.actor == "scheduler:expiry"
    assert context.role == "internal_rejector"
    assert "read_only" in context.clients
    assert storage.records == [
        (
            "r1",
            "orders",
            "scheduler:expiry",
            "reject",
            "place_paper_order",
            "60eec458a4569598b582ccd89a686a7d7798c406c834e3faba3e340cfd1161e5",
        )
    ]


@pytest.mark.asyncio
async def test_fresh_approval_is_not_expired() -> None:
    storage = FakeStorage({("chat", "c1"): NOW - timedelta(hours=23)})
    graph = FakeGraph([{"name": "save_note", "args": {"text": "safe"}}])

    assert await _run(storage, graph, FakeGraph([])) == 0
    assert graph.resumptions == []
    assert storage.records == []


@pytest.mark.asyncio
async def test_approval_resolved_before_lock_is_not_resumed() -> None:
    storage = FakeStorage({("chat", "c1"): NOW - timedelta(hours=25)})
    graph = FakeGraph([{"name": "save_note", "args": {"text": "safe"}}])
    original_list = storage.list_approval_candidates

    async def resolve_after_discovery(cutoff: datetime) -> list[tuple[str, str]]:
        candidates = await original_list(cutoff)
        if storage.list_calls == 1:
            graph.requests = []
        return candidates

    storage.list_approval_candidates = resolve_after_discovery  # type: ignore[method-assign]

    assert await _run(storage, graph, FakeGraph([])) == 0
    assert storage.list_calls == 2
    assert graph.resumptions == []
    assert storage.records == []


@pytest.mark.asyncio
async def test_approval_refreshed_before_lock_is_not_expired() -> None:
    storage = FakeStorage({("chat", "c1"): NOW - timedelta(hours=25)})
    graph = FakeGraph([{"name": "save_note", "args": {"text": "safe"}}])
    original_list = storage.list_approval_candidates

    async def refresh_after_discovery(cutoff: datetime) -> list[tuple[str, str]]:
        candidates = await original_list(cutoff)
        if storage.list_calls == 1:
            storage.touched["chat", "c1"] = NOW
        return candidates

    storage.list_approval_candidates = refresh_after_discovery  # type: ignore[method-assign]

    assert await _run(storage, graph, FakeGraph([])) == 0
    assert graph.resumptions == []
    assert storage.records == []


@pytest.mark.asyncio
async def test_busy_thread_is_left_for_next_cycle() -> None:
    storage = FakeStorage({("chat", "c1"): NOW - timedelta(hours=25)})
    graph = FakeGraph([{"name": "save_note", "args": {"text": "safe"}}])
    lock = asyncio.Lock()
    await lock.acquire()
    try:
        assert await _run(storage, graph, FakeGraph([]), chat_locks={"c1": lock}) == 0
        assert graph.resumptions == []
        assert storage.records == []
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_chat_rejects_every_pending_action_without_executing_it() -> None:
    storage = FakeStorage({("chat", "c1"): NOW - timedelta(hours=25)})
    graph = FakeGraph(
        [
            {"name": "save_note", "args": {"text": "one"}},
            {"name": "save_note", "args": {"text": "two"}},
        ]
    )

    assert await _run(storage, graph, FakeGraph([])) == 1
    assert graph.resumptions[0][0].resume == {"decisions": [{"type": "reject"}, {"type": "reject"}]}
    assert len(storage.records) == 2
    assert {record[5] for record in storage.records} == {
        "0b7407e4bbf9276ed184b34e4b83628318662e45a970561325a447cbde2fae1d",
        "b5f7e4c8a8716aedb4fadf5094c9a111740110ce8038f58e47b54b562a549420",
    }
