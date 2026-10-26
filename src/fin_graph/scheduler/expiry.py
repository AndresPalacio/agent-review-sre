"""Expire pending chat and paper-order approvals through their graph checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from langgraph.types import Command

from fin_graph.integrations.context import RunContext

EXPIRY_ACTOR = "scheduler:expiry"
EXPIRY_ROLE = "internal_rejector"


class ApprovalStorage(Protocol):
    def approval_lock(self, kind: str, thread_id: str) -> AbstractAsyncContextManager[None]: ...

    async def list_approval_candidates(self, cutoff: datetime) -> Sequence[tuple[str, str]]: ...

    async def record_approval(
        self,
        thread_id: str,
        kind: str,
        actor: str,
        decision: str,
        action_name: str,
        args_digest: str,
    ) -> None: ...

    async def touch_checkpoint_thread(self, thread_id: str, kind: str) -> None: ...


class ApprovalGraph(Protocol):
    async def aget_state(self, config: dict[str, Any]) -> Any: ...

    async def ainvoke(
        self, input: Command[Any], config: dict[str, Any], *, context: RunContext
    ) -> Any: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _approval_records(state: Any) -> list[tuple[str, str]]:
    """Return audit metadata only for a well-formed approval interrupt."""
    records: list[tuple[str, str]] = []
    for task in state.tasks:
        for item in task.interrupts:
            value = item.value
            if not isinstance(value, dict):
                return []
            requests = value.get("action_requests")
            if not isinstance(requests, list) or not requests:
                return []
            for action in requests:
                if not isinstance(action, dict):
                    return []
                name = action.get("name")
                args = action.get("args", action.get("arguments"))
                if not isinstance(name, str) or not name or not isinstance(args, dict):
                    return []
                digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
                records.append((name, digest))
    return records


async def expire_pending_approvals(
    *,
    storage: ApprovalStorage,
    chat_graph: ApprovalGraph | None,
    order_graph: ApprovalGraph | None,
    chat_locks: dict[str, asyncio.Lock],
    order_locks: dict[str, asyncio.Lock],
    context_factory: Callable[[], RunContext],
    ttl: timedelta,
    now: Callable[[], datetime] = _utc_now,
) -> int:
    """Reject old pending approvals; return the number of threads resolved.

    The caller supplies the same lock maps used by the approval API. The graph
    must allow ``internal_rejector`` to submit only ``reject`` decisions.
    """
    if ttl <= timedelta(0):
        raise ValueError("approval TTL must be positive")
    cutoff = now() - ttl
    if cutoff.tzinfo is None:
        raise ValueError("approval clock must be timezone-aware")
    graphs = {"chat": chat_graph, "orders": order_graph}
    locks = {"chat": chat_locks, "orders": order_locks}
    expired = 0
    for kind, thread_id in await storage.list_approval_candidates(cutoff):
        if kind not in graphs or graphs[kind] is None:
            continue
        graph = graphs[kind]
        assert graph is not None
        lock = locks[kind].setdefault(thread_id, asyncio.Lock())
        if lock.locked():
            continue
        async with lock, storage.approval_lock(kind, thread_id):
            if (kind, thread_id) not in await storage.list_approval_candidates(cutoff):
                continue
            config = {"configurable": {"thread_id": f"{kind}:{thread_id}"}}
            state = await graph.aget_state(config)
            records = _approval_records(state)
            if not records:
                continue
            context = context_factory()
            if not isinstance(context, RunContext):
                raise TypeError("approval expiry requires RunContext")
            context = replace(context, actor=EXPIRY_ACTOR, role=EXPIRY_ROLE)
            decisions = [{"type": "reject"} for _ in records]
            for action_name, args_digest in records:
                await storage.record_approval(
                    thread_id, kind, EXPIRY_ACTOR, "reject", action_name, args_digest
                )
            await graph.ainvoke(Command(resume={"decisions": decisions}), config, context=context)
            await storage.touch_checkpoint_thread(thread_id, kind)
            expired += 1
    return expired
