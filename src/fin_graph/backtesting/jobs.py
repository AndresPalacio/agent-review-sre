"""Durable, bounded backtest job supervisor over the compatibility store."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Protocol

from fin_graph.backtesting.artifacts import (
    BacktestArtifactStore,
    BacktestRunOutput,
    SignalPage,
)
from fin_graph.backtesting.models import BacktestEvent, BacktestEventKind
from fin_graph.conformance.contracts import JsonObject
from fin_graph.persistence.reference_app import BacktestJobRecord, JobStatus, ReferenceStore


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


type RunnerEventSink = Callable[[BacktestEvent], Awaitable[None] | None]
type BacktestRunner = Callable[..., Awaitable[BacktestRunOutput]]


_TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class BacktestJobManager:
    """Own background tasks while records and replay data survive process restarts."""

    def __init__(
        self,
        store: ReferenceStore,
        artifacts: BacktestArtifactStore,
        runners: Mapping[str, BacktestRunner],
        *,
        max_workers: int = 2,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one")
        self.store = store
        self.artifacts = artifacts
        self.runners = dict(runners)
        self._semaphore = asyncio.Semaphore(max_workers)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self, kind: str, request: JsonObject) -> BacktestJobRecord:
        if kind not in self.runners:
            raise KeyError(f"Unknown backtest kind {kind!r}")
        record = await self.store.create_backtest_job(kind, request)
        self._schedule(record)
        return record

    def _schedule(self, record: BacktestJobRecord) -> None:
        existing = self._tasks.get(record.id)
        if existing is not None and not existing.done():
            return
        self._cancellations.setdefault(record.id, asyncio.Event())
        self._locks.setdefault(record.id, asyncio.Lock())
        task = asyncio.create_task(self._run(record.id), name=f"backtest:{record.id}")
        self._tasks[record.id] = task

    async def recover(self) -> tuple[str, ...]:
        recovered: list[str] = []
        for record in await self.store.list_backtest_jobs():
            if record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                if record.kind not in self.runners:
                    await self.store.transition_backtest_job(
                        record.id,
                        expected_version=record.version,
                        status=JobStatus.FAILED,
                        error=f"No runner registered for recovered kind {record.kind!r}",
                    )
                    continue
                self._schedule(record)
                recovered.append(record.id)
        return tuple(recovered)

    async def get(self, job_id: str) -> BacktestJobRecord | None:
        return await self.store.get_backtest_job(job_id)

    async def cancel(self, job_id: str) -> BacktestJobRecord | None:
        record = await self.store.get_backtest_job(job_id)
        if record is None or record.status in _TERMINAL:
            return record
        event = self._cancellations.setdefault(job_id, asyncio.Event())
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            latest = await self.store.get_backtest_job(job_id)
            if latest is None or latest.status in _TERMINAL:
                return latest
            event.set()
            return latest

    async def signals(self, job_id: str, *, offset: int = 0, limit: int = 100) -> SignalPage:
        if offset < 0:
            raise ValueError("offset must not be negative")
        if not 1 <= limit <= 2_000:
            raise ValueError("limit must be between 1 and 2000")
        values = self.artifacts.load_signals(job_id)
        end = min(offset + limit, len(values))
        return SignalPage(
            job_id=job_id,
            items=values[offset:end],
            offset=offset,
            limit=limit,
            total=len(values),
            next_offset=end if end < len(values) else None,
        )

    async def events(self, job_id: str) -> tuple[BacktestEvent, ...]:
        return self.artifacts.load_events(job_id)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    async def _emit(
        self,
        job_id: str,
        kind: BacktestEventKind,
        progress: float,
        message: str,
        day: date | None = None,
    ) -> None:
        existing = self.artifacts.load_events(job_id)
        if existing and existing[-1].terminal:
            return
        if kind in {
            BacktestEventKind.COMPLETE,
            BacktestEventKind.CANCELLED,
            BacktestEventKind.ERROR,
        } and any(item.terminal for item in existing):
            return
        self.artifacts.append_event(
            job_id,
            BacktestEvent(
                sequence=len(existing),
                kind=kind,
                progress=max(0, min(1, progress)),
                message=message,
                date=day,
            ),
        )

    async def _runner_event(self, job_id: str, event: BacktestEvent) -> None:
        # The manager is the sole terminal-event authority, which makes cancel/success
        # races deterministic even when an engine emitted its own local terminal event.
        if event.terminal or event.kind is BacktestEventKind.RUNNING:
            return
        await self._emit(job_id, event.kind, event.progress, event.message, event.date)

    async def _transition(
        self,
        record: BacktestJobRecord,
        status: JobStatus,
        *,
        result: JsonObject | None = None,
        error: str | None = None,
    ) -> BacktestJobRecord | None:
        return await self.store.transition_backtest_job(
            record.id,
            expected_version=record.version,
            status=status,
            result=result,
            error=error,
        )

    async def _cancel_record(self, record: BacktestJobRecord) -> None:
        changed = await self._transition(record, JobStatus.CANCELLED)
        if changed is not None:
            await self._emit(record.id, BacktestEventKind.CANCELLED, 1, "Backtest cancelled")

    async def _run(self, job_id: str) -> None:
        cancellation = self._cancellations[job_id]
        lock = self._locks[job_id]
        async with self._semaphore:
            record = await self.store.get_backtest_job(job_id)
            if record is None or record.status in _TERMINAL:
                return
            if record.status is JobStatus.QUEUED:
                changed = await self._transition(record, JobStatus.RUNNING)
                if changed is None:
                    return
                record = changed
            await self._emit(job_id, BacktestEventKind.RUNNING, 0, "Backtest started")
            if cancellation.is_set():
                async with lock:
                    latest = await self.store.get_backtest_job(job_id)
                    if latest is not None and latest.status not in _TERMINAL:
                        await self._cancel_record(latest)
                return
            runner = self.runners[record.kind]

            async def event_sink(event: BacktestEvent) -> None:
                await self._runner_event(job_id, event)

            try:
                output = await runner(
                    record.request, event_sink=event_sink, cancellation=cancellation
                )
                validated = BacktestRunOutput.model_validate(output)
            except asyncio.CancelledError:
                cancellation.set()
                async with lock:
                    latest = await self.store.get_backtest_job(job_id)
                    if latest is not None and latest.status not in _TERMINAL:
                        await self._cancel_record(latest)
                raise
            except Exception as exc:
                async with lock:
                    latest = await self.store.get_backtest_job(job_id)
                    if latest is None or latest.status in _TERMINAL:
                        return
                    if cancellation.is_set():
                        await self._cancel_record(latest)
                    else:
                        detail = f"{type(exc).__name__}: {exc}"
                        changed = await self._transition(latest, JobStatus.FAILED, error=detail)
                        if changed is not None:
                            await self._emit(
                                job_id,
                                BacktestEventKind.ERROR,
                                1,
                                f"Backtest failed: {detail}",
                            )
                return

            async with lock:
                latest = await self.store.get_backtest_job(job_id)
                if latest is None or latest.status in _TERMINAL:
                    return
                if cancellation.is_set():
                    await self._cancel_record(latest)
                    return
                published = self.artifacts.publish_success(job_id, validated)
                changed = await self._transition(
                    latest, JobStatus.SUCCEEDED, result=validated.result
                )
                if changed is None:
                    self.artifacts.discard_success(job_id)
                    return
                for artifact in published:
                    await self.store.create_artifact(
                        job_id,
                        artifact.kind,
                        artifact.path,
                        {"sha256": artifact.sha256, "bytes": artifact.bytes},
                    )
                await self._emit(job_id, BacktestEventKind.COMPLETE, 1, "Backtest complete")
