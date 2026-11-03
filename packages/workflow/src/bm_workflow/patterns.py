"""Small, bounded composition primitives using the common step runtime.

Branch and map results contain the final child state. Map bodies receive a copy
of the parent state with ``item`` and ``item_index`` set for the current item.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from .definition import StepNode, step, step_manifest
from .errors import ExecutionError

if TYPE_CHECKING:
    from .compiler import CompiledWorkflow


def sequence(*steps: StepNode) -> tuple[StepNode, ...]:
    """Group ordered steps for use as a branch."""
    if not steps:
        raise ValueError("sequence requires at least one step")
    if any(not isinstance(node, StepNode) for node in steps):
        raise TypeError("sequence requires StepNode values")
    return steps


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _key(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")


async def _run_sequence(nodes: Sequence[StepNode], state: dict[str, Any]) -> dict[str, Any]:
    from .runtime import execute_step

    result = deepcopy(state)
    for node in nodes:
        result = await execute_step(node, result)
    return result


async def _run_workers(worker: Callable[[], Awaitable[None]], count: int) -> None:
    async def guarded() -> None:
        try:
            await worker()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            # TaskGroup treats spontaneous child cancellation as success. A
            # cancelled provider is a cancelled workflow, never a partial map.
            raise ExecutionError("cancelled") from None

    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(count):
                group.create_task(guarded())
    except ExceptionGroup as errors:
        pending: list[BaseException] = list(errors.exceptions)
        while pending:
            cause = pending.pop(0)
            if isinstance(cause, ExecutionError):
                raise cause from None
            if isinstance(cause, BaseExceptionGroup):
                pending[0:0] = cause.exceptions
        raise


def parallel(
    id: str,
    branches: Mapping[str, StepNode | Sequence[StepNode]],
    *,
    output_key: str,
    max_concurrency: int = 4,
) -> StepNode:
    """Run isolated branches with bounded workers and fail-fast cancellation."""
    _positive_integer(max_concurrency, "max_concurrency")
    _key(output_key, "output_key")
    if not branches:
        raise ValueError("parallel requires at least one branch")
    normalized: dict[str, tuple[StepNode, ...]] = {}
    for name, branch in branches.items():
        _key(name, "branch name")
        nodes = (branch,) if isinstance(branch, StepNode) else tuple(branch)
        normalized[name] = sequence(*nodes)
    entries = tuple(normalized.items())

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        results: dict[str, dict[str, Any]] = {}
        work = iter(entries)

        async def worker() -> None:
            for name, nodes in work:
                results[name] = await _run_sequence(nodes, state)

        await _run_workers(worker, min(max_concurrency, len(entries)))
        return {output_key: {name: results[name] for name in normalized}}

    return step(
        id,
        handler,
        kind="composition",
        metadata={
            "pattern": "parallel",
            "output_key": output_key,
            "max_concurrency": max_concurrency,
            "branches": {
                name: [step_manifest(node) for node in nodes] for name, nodes in normalized.items()
            },
        },
    )


def join(id: str, handler: Callable[..., Any], **kwargs: Any) -> StepNode:
    """A named deterministic step which can consume composite results."""
    return step(id, handler, **kwargs)


def map_each(
    id: str,
    body: StepNode,
    *,
    items_key: str,
    output_key: str,
    max_concurrency: int = 4,
    max_items: int = 1000,
) -> StepNode:
    """Map a step over a list, retaining input order and bounding task count."""
    _positive_integer(max_concurrency, "max_concurrency")
    _positive_integer(max_items, "max_items")
    _key(items_key, "items_key")
    _key(output_key, "output_key")
    if not isinstance(body, StepNode):
        raise TypeError("body must be a StepNode")

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        from .runtime import execute_step

        items = state[items_key]
        if not isinstance(items, list):
            raise TypeError("map input must be a list")
        if len(items) > max_items:
            raise ValueError("map input exceeds max_items")
        results: list[dict[str, Any] | None] = [None] * len(items)
        work = iter(enumerate(items))

        async def worker() -> None:
            for index, item in work:
                child = deepcopy(state)
                child["item"] = deepcopy(item)
                child["item_index"] = index
                results[index] = await execute_step(body, child)

        await _run_workers(worker, min(max_concurrency, len(items)))
        return {output_key: results}

    return step(
        id,
        handler,
        kind="composition",
        metadata={
            "pattern": "map_each",
            "body": step_manifest(body),
            "items_key": items_key,
            "output_key": output_key,
            "max_concurrency": max_concurrency,
            "max_items": max_items,
        },
    )


def subflow(id: str, compiled: CompiledWorkflow, *, output_key: str) -> StepNode:
    """Invoke a compiled workflow while preserving the caller's run constraints."""
    _key(output_key, "output_key")

    async def handler(state: dict[str, Any]) -> dict[str, Any]:
        from .runtime import arun_workflow, current_context

        parent = current_context()
        child = parent.child(compiled) if parent is not None else None
        result = await arun_workflow(compiled, state, context=child)
        status = result.status.value
        if status != "complete" or result.output is None:
            cause = result.cause
            raise ExecutionError(
                cause.code if cause is not None else "subflow_failed",
                node_id=cause.node_id if cause is not None else id,
                error_type=cause.error_type if cause is not None else None,
            )
        return {output_key: result.output.model_dump(mode="python", round_trip=True)}

    return step(
        id,
        handler,
        kind="composition",
        metadata={
            "pattern": "subflow",
            "definition_digest": compiled.definition_digest,
            "output_key": output_key,
        },
    )
