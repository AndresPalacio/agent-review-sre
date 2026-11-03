"""Persistent flow CRUD and revision-pinned execution service."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fin_graph.compat.contracts import JsonObject, JsonValue
from fin_graph.compat.storage import (
    CompatibilityStore,
    FlowRecord,
    FlowRevision,
    FlowRunRecord,
    RecordNotFoundError,
    RunCycleRecord,
    RunStatus,
)
from fin_graph.flows.compiler import compile_flow
from fin_graph.flows.models import FlowGraph, FlowNode

FlowExecutor = Callable[
    [FlowNode, dict[str, JsonValue], JsonObject],
    Awaitable[JsonValue] | JsonValue,
]


@dataclass(frozen=True, slots=True)
class FlowServices:
    execute: FlowExecutor | None = None


def public_id(value: str) -> int:
    """Map opaque store ids to the positive integer surface used by the old app."""

    compact = value.replace("-", "")
    if len(compact) >= 15:
        try:
            return int(compact[:15], 16)
        except ValueError:
            pass
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:7], "big")


class FlowService:
    def __init__(
        self,
        store: CompatibilityStore,
        services: FlowServices | None = None,
        *,
        schedule_runs: bool = True,
    ) -> None:
        self.store = store
        self.services = services or FlowServices()
        self.schedule_runs = schedule_runs
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def _flow(self, flow_id: int) -> FlowRecord:
        record = next(
            (item for item in await self.store.list_flows() if public_id(item.id) == flow_id),
            None,
        )
        if record is None:
            raise RecordNotFoundError(f"flow not found: {flow_id}")
        return record

    async def _run(self, run_id: int) -> FlowRunRecord:
        record = next(
            (item for item in await self.store.list_flow_runs() if public_id(item.id) == run_id),
            None,
        )
        if record is None:
            raise RecordNotFoundError(f"flow run not found: {run_id}")
        return record

    async def create(
        self, name: str, definition: JsonObject, *, description: str = ""
    ) -> tuple[FlowRecord, FlowRevision]:
        return await self.store.create_flow(name, definition, description=description)

    async def get(self, flow_id: int) -> tuple[FlowRecord, FlowRevision]:
        flow = await self._flow(flow_id)
        revision = await self.store.get_flow_revision(flow.id, flow.active_revision)
        if revision is None:
            raise RecordNotFoundError(f"flow revision not found: {flow_id}")
        return flow, revision

    async def list(self) -> list[tuple[FlowRecord, FlowRevision]]:
        values: list[tuple[FlowRecord, FlowRevision]] = []
        for flow in await self.store.list_flows():
            revision = await self.store.get_flow_revision(flow.id, flow.active_revision)
            if revision is not None:
                values.append((flow, revision))
        return values

    async def update(
        self,
        flow_id: int,
        definition: JsonObject,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> tuple[FlowRecord, FlowRevision]:
        current = await self._flow(flow_id)
        return await self.store.update_flow(
            current.id,
            expected_version=current.version,
            definition=definition,
            name=name,
            description=description,
        )

    async def delete(self, flow_id: int) -> bool:
        current = await self._flow(flow_id)
        return await self.store.delete_flow(current.id, expected_version=current.version)

    async def duplicate(
        self, flow_id: int, new_name: str | None = None
    ) -> tuple[FlowRecord, FlowRevision]:
        current, revision = await self.get(flow_id)
        return await self.create(
            new_name or f"{current.name} (Copy)",
            revision.definition,
            description=current.description,
        )

    async def start_run(self, flow_id: int, request: JsonObject) -> FlowRunRecord:
        flow, revision = await self.get(flow_id)
        compile_flow(FlowGraph.model_validate(revision.definition), self.services)
        record = await self.store.create_flow_run(flow.id, revision.revision, request)
        if self.schedule_runs:
            task = asyncio.create_task(self._execute(record.id), name=f"flow-run:{record.id}")
            self._tasks[record.id] = task

            def discard(_task: asyncio.Task[None]) -> None:
                self._tasks.pop(record.id, None)

            task.add_done_callback(discard)
        return record

    async def _execute(self, run_id: str) -> None:
        current = await self.store.get_flow_run(run_id)
        if current is None:
            return
        running = await self.store.transition_flow_run(
            run_id, expected_version=current.version, status=RunStatus.RUNNING
        )
        if running is None:
            return
        await self.store.append_run_cycle(run_id, "run.started", {})
        try:
            revision = await self.store.get_flow_revision(running.flow_id, running.flow_revision)
            if revision is None:
                raise RecordNotFoundError("pinned flow revision not found")
            compiled = compile_flow(FlowGraph.model_validate(revision.definition), self.services)
            result = await compiled.ainvoke({"request": running.request})
            current = await self.store.get_flow_run(run_id)
            if current is not None and current.status is RunStatus.RUNNING:
                await self.store.transition_flow_run(
                    run_id,
                    expected_version=current.version,
                    status=RunStatus.SUCCEEDED,
                    result=result.get("result", {}),
                )
                await self.store.append_run_cycle(run_id, "run.completed", {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = await self.store.get_flow_run(run_id)
            if current is not None and current.status is RunStatus.RUNNING:
                await self.store.transition_flow_run(
                    run_id,
                    expected_version=current.version,
                    status=RunStatus.FAILED,
                    error=f"{type(exc).__name__}: flow execution failed",
                )
                await self.store.append_run_cycle(run_id, "run.failed", {})

    async def get_run(self, flow_id: int, run_id: int) -> FlowRunRecord:
        flow = await self._flow(flow_id)
        run = await self._run(run_id)
        if run.flow_id != flow.id:
            raise RecordNotFoundError(f"flow run not found: {run_id}")
        return run

    async def list_runs(self, flow_id: int) -> tuple[FlowRunRecord, ...]:
        flow = await self._flow(flow_id)
        return await self.store.list_flow_runs(flow.id)

    async def cancel(self, flow_id: int, run_id: int) -> FlowRunRecord:
        current = await self.get_run(flow_id, run_id)
        if current.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return current
        task = self._tasks.get(current.id)
        if task is not None:
            task.cancel()
        updated = await self.store.transition_flow_run(
            current.id,
            expected_version=current.version,
            status=RunStatus.CANCELLED,
            error="Cancelled",
        )
        return updated or await self._run(run_id)

    async def update_run(
        self,
        flow_id: int,
        run_id: int,
        *,
        status: RunStatus | None = None,
        result: JsonValue = None,
        error: str | None = None,
    ) -> FlowRunRecord:
        current = await self.get_run(flow_id, run_id)
        updated = await self.store.transition_flow_run(
            current.id,
            expected_version=current.version,
            status=status or current.status,
            result=current.result if result is None else result,
            error=current.error if error is None else error,
        )
        return updated or await self._run(run_id)

    async def delete_run(self, flow_id: int, run_id: int) -> bool:
        current = await self.get_run(flow_id, run_id)
        return await self.store.delete_flow_run(current.id)

    async def delete_runs(self, flow_id: int) -> int:
        flow = await self._flow(flow_id)
        return await self.store.delete_flow_runs(flow.id)

    async def append_cycle(self, run_id: int, event: str, payload: JsonObject) -> RunCycleRecord:
        run = await self._run(run_id)
        return await self.store.append_run_cycle(run.id, event, payload)

    async def list_cycles(self, flow_id: int, run_id: int) -> tuple[RunCycleRecord, ...]:
        run = await self.get_run(flow_id, run_id)
        return await self.store.list_run_cycles(run.id)
