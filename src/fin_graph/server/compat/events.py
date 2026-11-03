"""Bounded adaptation from internal progress records to the legacy SSE dialect."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from fin_graph.compat.ai_hedge_fund.schemas import (
    CompleteEvent,
    ErrorEvent,
    ProgressUpdateEvent,
    StartEvent,
)
from fin_graph.compat.contracts import JsonObject


class LegacyDialect(StrEnum):
    HEDGE_FUND = "hedge-fund"
    BACKTEST = "backtest"


class InternalEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["start", "progress", "complete", "error"]
    payload: JsonObject = Field(default_factory=dict)


def _frame(event: StartEvent | ProgressUpdateEvent | ErrorEvent | CompleteEvent) -> bytes:
    return event.to_sse().encode("utf-8")


def _legacy_event(
    event: InternalEvent,
) -> StartEvent | ProgressUpdateEvent | ErrorEvent | CompleteEvent:
    if event.kind == "start":
        return StartEvent(timestamp=event.payload.get("timestamp"))
    if event.kind == "progress":
        return ProgressUpdateEvent.model_validate({"type": "progress", **event.payload})
    if event.kind == "error":
        return ErrorEvent.model_validate({"type": "error", **event.payload})
    data = event.payload.get("data", event.payload)
    if not isinstance(data, dict):
        data = {"result": data}
    return CompleteEvent(data=data, timestamp=event.payload.get("timestamp"))


async def legacy_sse(
    source: AsyncIterator[InternalEvent], dialect: LegacyDialect
) -> AsyncIterator[bytes]:
    """Emit the shared v1 wire format and stop after its first terminal event."""

    del dialect  # Both reference endpoints use the same event envelope.
    async for event in source:
        yield _frame(_legacy_event(event))
        if event.kind in {"complete", "error"}:
            return


type EventEmitter = Callable[[InternalEvent], Awaitable[None]]
type RunChild = Callable[[EventEmitter], Awaitable[dict[str, Any]]]


async def stream_legacy_run(
    child: RunChild,
    dialect: LegacyDialect,
    *,
    queue_size: int = 128,
) -> AsyncIterator[bytes]:
    """Run one child behind a bounded queue and always tear it down with the stream."""

    if queue_size <= 0:
        raise ValueError("queue_size must be positive")
    queue: asyncio.Queue[InternalEvent] = asyncio.Queue(maxsize=queue_size)

    async def emit(event: InternalEvent) -> None:
        if event.kind not in {"start", "complete", "error"}:
            await queue.put(event)

    task: asyncio.Future[dict[str, Any]] = asyncio.ensure_future(child(emit))
    try:
        yield _frame(StartEvent())
        while not task.done() or not queue.empty():
            if not queue.empty():
                yield _frame(_legacy_event(queue.get_nowait()))
                continue
            queue_task = asyncio.create_task(queue.get())
            waiters = {
                cast(asyncio.Future[object], task),
                cast(asyncio.Future[object], queue_task),
            }
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if queue_task in done:
                yield _frame(_legacy_event(queue_task.result()))
            else:
                queue_task.cancel()
                await asyncio.gather(queue_task, return_exceptions=True)
        try:
            result = await task
        except asyncio.CancelledError:
            raise
        except Exception:
            yield _frame(
                ErrorEvent(
                    message=(
                        "Internal error during backtest"
                        if dialect is LegacyDialect.BACKTEST
                        else "Internal error during hedge fund run"
                    ),
                    code=(
                        "backtest_failed" if dialect is LegacyDialect.BACKTEST else "internal_error"
                    ),
                )
            )
            return
        yield _frame(CompleteEvent(data=result))
    except (asyncio.CancelledError, GeneratorExit):
        raise
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
