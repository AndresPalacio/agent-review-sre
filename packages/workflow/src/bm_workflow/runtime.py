"""Per-invocation state, safe outcomes and cooperative execution controls."""

from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
from collections.abc import Callable, Coroutine, Mapping
from contextlib import suppress
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from bm_workflow.definition import Handler, StepNode, validate_model
from bm_workflow.errors import ExecutionError, InputValidationError, TransientError

if TYPE_CHECKING:
    from bm_workflow.compiler import CompiledWorkflow


class RunStatus(StrEnum):
    COMPLETE = "complete"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SafeCause:
    code: str
    node_id: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class RunContext:
    name: str
    version: str
    definition_digest: str
    run_id: str = field(default_factory=lambda: str(uuid4()))
    deadline: float | None = None
    cancellation: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.deadline is not None and not math.isfinite(self.deadline):
            raise ValueError("deadline must be a finite monotonic timestamp")

    def check(self) -> None:
        if self.cancellation.is_set():
            raise ExecutionError("cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ExecutionError("deadline_exceeded")

    def child(self, compiled: CompiledWorkflow) -> RunContext:
        return replace(
            self,
            name=compiled.definition.name,
            version=compiled.definition.version,
            definition_digest=compiled.definition_digest,
        )


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    context: RunContext
    output: BaseModel | None = None
    cause: SafeCause | None = None


@dataclass(frozen=True)
class RuntimeEvent:
    node_id: str
    status: str
    attempt: int
    duration_seconds: float
    simulated: bool = False


@dataclass
class _Scope:
    context: RunContext
    fixtures: Mapping[str, Any] | None = None
    events: list[RuntimeEvent] = field(default_factory=list)
    observer: Callable[[RuntimeEvent], None] | None = None


_scope: ContextVar[_Scope | None] = ContextVar("bm_workflow_scope", default=None)


def current_context() -> RunContext | None:
    scope = _scope.get()
    return scope.context if scope else None


async def call_handler(handler: Handler, value: Any) -> Any:
    """Isolate input; sync callables run off-loop with contextvars propagated."""
    isolated = deepcopy(value)
    if inspect.iscoroutinefunction(handler):
        return await handler(isolated)
    result = await asyncio.to_thread(handler, isolated)
    if inspect.isawaitable(result):
        return await result
    return result


async def _controlled[T](
    operation: Coroutine[Any, Any, T], context: RunContext, limit_seconds: float | None = None
) -> T:
    task = asyncio.create_task(operation)
    cutoff = time.monotonic() + limit_seconds if limit_seconds is not None else None
    try:
        while True:
            context.check()
            if cutoff is not None and time.monotonic() >= cutoff:
                raise ExecutionError("deadline_exceeded")
            remaining = [0.05]
            if cutoff is not None:
                remaining.append(max(0, cutoff - time.monotonic()))
            if context.deadline is not None:
                remaining.append(max(0, context.deadline - time.monotonic()))
            done, _ = await asyncio.wait({task}, timeout=min(remaining))
            if done:
                context.check()
                if cutoff is not None and time.monotonic() >= cutoff:
                    raise ExecutionError("deadline_exceeded")
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        # Drain task exceptions, including when a deadline and task completion race.
        await asyncio.gather(task, return_exceptions=True)


def _emit(event: RuntimeEvent) -> None:
    scope = _scope.get()
    if scope is None:
        return
    if len(scope.events) < 10000:
        scope.events.append(event)
    if scope.observer:
        with suppress(Exception):  # Telemetry must not change business results.
            scope.observer(event)


async def execute_step(node: StepNode, state: dict[str, Any]) -> dict[str, Any]:
    scope = _scope.get()
    context = scope.context if scope else None
    for attempt in range(1, node.policy.max_attempts + 1):
        started = time.monotonic()
        simulated = scope is not None and scope.fixtures is not None and node.id in scope.fixtures
        try:
            if context:
                context.check()
            value: Any = deepcopy(state)
            if node.input_schema is not None:
                validated = validate_model(node.input_schema, value)
                value = (
                    validated
                    if node.typed
                    else validated.model_dump(mode="python", round_trip=True)
                )
            if simulated:
                assert scope is not None and scope.fixtures is not None
                updates = deepcopy(scope.fixtures[node.id])
            else:
                if scope and scope.fixtures is not None and node.kind == "external":
                    raise ExecutionError("missing_fixture", node.id)
                operation = call_handler(node.handler, value)
                if context:
                    updates = await _controlled(operation, context, node.policy.timeout_seconds)
                else:
                    updates = await operation
            if node.output_schema is not None:
                updates = validate_model(node.output_schema, updates).model_dump(
                    mode="python", round_trip=True
                )
            if not isinstance(updates, dict) or not all(isinstance(key, str) for key in updates):
                raise ExecutionError("invalid_node_output", node.id)
            if context:
                context.check()
            _emit(RuntimeEvent(node.id, "complete", attempt, time.monotonic() - started, simulated))
            return {**state, **deepcopy(updates)}
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # Caller cancellation must remain observable by its TaskGroup.
            _emit(RuntimeEvent(node.id, "cancelled", attempt, time.monotonic() - started))
            raise ExecutionError("cancelled", node.id) from None
        except TransientError as error:
            _emit(RuntimeEvent(node.id, "failed", attempt, time.monotonic() - started, simulated))
            if attempt == node.policy.max_attempts:
                raise ExecutionError("node_failed", node.id, type(error).__name__) from None
            if context:
                await _controlled(asyncio.sleep(node.policy.retry_delay), context)
            else:
                await asyncio.sleep(node.policy.retry_delay)
        except ExecutionError as error:
            _emit(RuntimeEvent(node.id, error.code, attempt, time.monotonic() - started, simulated))
            if error.node_id is None:
                raise ExecutionError(error.code, node.id, error.error_type) from None
            raise
        except Exception as error:
            _emit(RuntimeEvent(node.id, "failed", attempt, time.monotonic() - started, simulated))
            code = "invalid_node_output" if isinstance(error, ValidationError) else "node_failed"
            raise ExecutionError(code, node.id, type(error).__name__) from None
    raise AssertionError("Unreachable retry policy")


def _sync[T](operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(operation())
    raise RuntimeError("Use the async API (arun_workflow/arun) inside a running event loop")


async def arun_workflow(
    compiled: CompiledWorkflow,
    input_data: Any,
    *,
    context: RunContext | None = None,
    observer: Callable[[RuntimeEvent], None] | None = None,
) -> RunResult:
    try:
        validated = validate_model(compiled.definition.input_schema, input_data)
    except (ValidationError, TypeError, ValueError):
        raise InputValidationError("Input does not satisfy the workflow contract") from None
    context = context or RunContext(
        compiled.definition.name, compiled.definition.version, compiled.definition_digest
    )
    if (context.name, context.version, context.definition_digest) != (
        compiled.definition.name,
        compiled.definition.version,
        compiled.definition_digest,
    ):
        raise ValueError("RunContext identity does not match compiled workflow")
    parent = _scope.get()
    scope = replace(parent, context=context) if parent else _Scope(context, observer=observer)
    token = _scope.set(scope)
    try:
        context.check()
        state = await _controlled(
            compiled.graph.ainvoke(
                {"data": validated.model_dump(mode="python", round_trip=True), "route": ""},
                config={"recursion_limit": max(25, compiled.node_count * 3)},
            ),
            context,
        )
        context.check()
        try:
            output = validate_model(compiled.definition.output_schema, state["data"])
        except (ValidationError, TypeError, ValueError):
            raise ExecutionError("invalid_output") from None
        return RunResult(RunStatus.COMPLETE, context, output=output)
    except ExecutionError as error:
        status = {"cancelled": RunStatus.CANCELLED, "deadline_exceeded": RunStatus.TIMED_OUT}.get(
            error.code, RunStatus.FAILED
        )
        return RunResult(
            status, context, cause=SafeCause(error.code, error.node_id, error.error_type)
        )
    except Exception as error:
        return RunResult(
            RunStatus.FAILED,
            context,
            cause=SafeCause("execution_failed", error_type=type(error).__name__),
        )
    finally:
        _scope.reset(token)


def run_workflow(
    compiled: CompiledWorkflow,
    input_data: Any,
    *,
    context: RunContext | None = None,
    observer: Callable[[RuntimeEvent], None] | None = None,
) -> RunResult:
    return _sync(lambda: arun_workflow(compiled, input_data, context=context, observer=observer))
