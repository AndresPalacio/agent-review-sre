"""Bounded in-process orchestration of persistent analysis threads."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from weakref import WeakValueDictionary

from fin_graph.integrations.context import RunContext
from fin_graph.safety.secrets import redact_secrets
from fin_graph.server.storage import Storage

_LOG = logging.getLogger(__name__)


class RunOverloaded(RuntimeError):
    """The finite local outstanding-run budget is exhausted."""


class RunReservation:
    def __init__(self, manager: RunManager) -> None:
        self.manager = manager
        self.transferred = False
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.manager.pending_count -= 1
            self.released = True


class RunManager:
    def __init__(
        self,
        graph: Any,
        storage: Storage,
        context_factory: Callable[[], object],
        *,
        max_concurrent: int = 4,
        max_pending: int = 128,
        reconcile_interval_seconds: float = 60,
    ) -> None:
        if max_concurrent < 1 or max_pending < 1 or reconcile_interval_seconds <= 0:
            raise ValueError("manager concurrency and interval must be positive")
        self.max_pending = max_pending
        self.pending_count = 0
        self.graph = graph
        self.storage = storage
        self.context_factory = context_factory
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.reconcile_interval_seconds = reconcile_interval_seconds
        self._reconcile_task: asyncio.Task[None] | None = None

    @contextmanager
    def reserve(self) -> Iterator[RunReservation]:
        if self.pending_count >= self.max_pending:
            raise RunOverloaded("run capacity exhausted")
        self.pending_count += 1
        reservation = RunReservation(self)
        try:
            yield reservation
        finally:
            if not reservation.transferred:
                reservation.release()

    async def start(self) -> None:
        await self.reconcile_stale_runs()
        if self._reconcile_task is None:
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reconcile_interval_seconds)
            try:
                await self.reconcile_stale_runs()
            except Exception:
                _LOG.exception("run reconciliation failed")

    async def reconcile_stale_runs(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(minutes=2)
        for run_id, thread_id in await self.storage.stale_running_candidates(cutoff):
            async with self.storage.try_run_lock(thread_id) as acquired:
                if not acquired:
                    continue
                if not await self.storage.interrupt_stale_run(run_id, cutoff):
                    continue
                try:
                    snapshot = await self.graph.aget_state(self._config(thread_id))
                except Exception:
                    continue
                if snapshot.values and not snapshot.next:
                    await self.storage.set_status_with_event(run_id, "completed")
                elif not snapshot.values:
                    await self.storage.set_status_with_event(run_id, "failed", "checkpoint_missing")
                    await self.storage.release_thread_fingerprints(thread_id)

    async def stop(self) -> None:
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            await asyncio.gather(self._reconcile_task, return_exceptions=True)
            self._reconcile_task = None
        pending = dict(self.tasks)
        for task in pending.values():
            task.cancel()
        if pending:
            await asyncio.gather(*pending.values(), return_exceptions=True)
        for run_id in pending:
            current = await self.storage.get_run(run_id)
            if current and current["status"] in {"queued", "running", "recovering"}:
                await self.storage.set_status_with_event(run_id, "interrupted")
        self.tasks.clear()

    async def submit(
        self,
        raw_request: str | dict[str, Any],
        source_hint: str | None,
        *,
        thread_id: str | None = None,
        destinations: dict[str, str] | None = None,
        user_requested: bool = True,
    ) -> dict[str, Any]:
        thread = thread_id or str(uuid4())
        run_id = str(uuid4())
        with self.reserve() as reservation:
            try:
                await self.storage.create_run(run_id, thread)
                return self.schedule_existing(
                    run_id,
                    thread,
                    raw_request,
                    source_hint,
                    destinations=destinations,
                    user_requested=user_requested,
                    reservation=reservation,
                )
            except BaseException:
                await self.storage.set_status_with_event(run_id, "failed", "schedule_unavailable")
                raise

    def schedule_existing(
        self,
        run_id: str,
        thread: str,
        raw_request: str | dict[str, Any],
        source_hint: str | None,
        *,
        destinations: dict[str, str] | None = None,
        user_requested: bool = True,
        reservation: RunReservation | None = None,
    ) -> dict[str, Any]:
        """Schedule a run already committed together with a signal fingerprint."""
        value = {
            "raw_request": raw_request,
            "source_hint": source_hint,
            "user_requested": user_requested,
            "destinations": dict(destinations or {}),
            "thread_id": thread,
            "run_id": run_id,
        }
        self._schedule(run_id, thread, value, reservation=reservation)
        return {"run_id": run_id, "thread_id": thread, "status": "queued"}

    async def recover_existing(self, run_id: str, thread: str, raw_request: str) -> bool:
        """Reclaim an abandoned gateway run and resume its checkpoint once."""
        with self.reserve() as reservation:
            if not await self.storage.claim_gateway_run_recovery(
                run_id, datetime.now(UTC) - timedelta(minutes=2)
            ):
                return False
            try:
                snapshot = await self.graph.aget_state(self._config(thread))
                if snapshot.values and not snapshot.next:
                    await self.storage.set_status_with_event(run_id, "completed")
                elif snapshot.values and snapshot.next:
                    self._schedule(run_id, thread, None, reservation=reservation)
                else:
                    self.schedule_existing(
                        run_id, thread, raw_request, None, reservation=reservation
                    )
            except (Exception, asyncio.CancelledError):
                await self.storage.set_status_with_event(run_id, "failed", "recovery_failed")
                raise
            return True

    async def resume(self, thread_id: str) -> dict[str, Any]:
        lock = self.locks.setdefault(thread_id, asyncio.Lock())
        async with lock, self.storage.run_lock(thread_id):
            current = await self.storage.latest_run(thread_id)
            if current is None:
                raise LookupError("thread not found")
            if current["status"] not in {"interrupted", "failed"}:
                raise ValueError("thread is not resumable")
            snapshot = await self.graph.aget_state(self._config(thread_id))
            if not snapshot.values or not snapshot.next:
                raise ValueError("no unfinished checkpoint available")
            run_id = str(uuid4())
            with self.reserve() as reservation:
                try:
                    await self.storage.create_run(run_id, thread_id)
                    self._schedule(run_id, thread_id, None, reservation=reservation)
                except BaseException:
                    await self.storage.set_status_with_event(
                        run_id, "failed", "schedule_unavailable"
                    )
                    raise
            return {"run_id": run_id, "thread_id": thread_id, "status": "queued"}

    async def wait(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task:
            await task

    def _schedule(
        self,
        run_id: str,
        thread_id: str,
        value: object,
        *,
        reservation: RunReservation | None = None,
    ) -> None:
        if reservation is None:
            with self.reserve() as reserved:
                self._schedule(run_id, thread_id, value, reservation=reserved)
            return
        if reservation.manager is not self or reservation.released or reservation.transferred:
            raise ValueError("invalid run reservation")
        task = asyncio.create_task(self._execute(run_id, thread_id, value))
        reservation.transferred = True
        self.tasks[run_id] = task

        def finished(_: asyncio.Task[None]) -> None:
            self.tasks.pop(run_id, None)
            reservation.release()

        task.add_done_callback(finished)

    async def _execute(self, run_id: str, thread_id: str, value: object) -> None:
        heartbeat = asyncio.create_task(self._heartbeat_run(run_id))
        try:
            await self._execute_locked(run_id, thread_id, value)
        except asyncio.CancelledError:
            current = await self.storage.get_run(run_id)
            if current and current["status"] in {"queued", "running", "recovering"}:
                await self.storage.set_status_with_event(run_id, "interrupted")
            raise
        except Exception as exc:
            # Acquisition can fail before _execute_locked enters its graph try block.
            await self.storage.set_status_with_event(run_id, "failed", type(exc).__name__)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat_run(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(30)
            if not await self.storage.touch_active_run(run_id):
                return

    async def _execute_locked(self, run_id: str, thread_id: str, value: object) -> None:
        lock = self.locks.setdefault(thread_id, asyncio.Lock())
        async with self.semaphore, lock, self.storage.run_lock(thread_id):
            current = await self.storage.get_run(run_id)
            if current is None or current["status"] in {"completed", "failed"}:
                return
            await self.storage.set_status(run_id, "running")
            await self.storage.add_event(run_id, "status", {"status": "running"})
            try:
                report_ready = False
                context = self.context_factory()
                try:
                    async for mode, event in self.graph.astream(
                        value,
                        context=context,
                        stream_mode=["custom", "values"],
                        config=self._config(thread_id),
                    ):
                        if mode == "custom" and isinstance(event, dict):
                            kind = event.get("type")
                            data = event.get("data")
                            if kind == "harness_trace" and isinstance(data, dict):
                                # Public history/SSE exposes execution metadata only.
                                # Redacted arguments stay in the private checkpoint.
                                safe_trace = {
                                    key: redact_secrets(data[key])[:128]
                                    for key in (
                                        "kind",
                                        "name",
                                        "call_id",
                                        "parent",
                                        "node",
                                        "status",
                                        "error",
                                        "observed_at",
                                    )
                                    if isinstance(data.get(key), str)
                                }
                                await self.storage.add_event(run_id, "harness_trace", safe_trace)
                            if kind in {
                                "stage",
                                "report",
                                "done",
                                "plan",
                                "analyst",
                            } and isinstance(data, dict):
                                stage = data.get("stage")
                                if isinstance(stage, str):
                                    safe: dict[str, Any] = {"stage": stage[:80]}
                                    if kind in {"plan", "analyst"}:
                                        wave = data.get("wave")
                                        if (
                                            isinstance(wave, int)
                                            and not isinstance(wave, bool)
                                            and 1 <= wave <= 100
                                        ):
                                            safe["wave"] = wave
                                    if kind == "plan" and isinstance(data.get("tasks"), list):
                                        # The analysis plan names one analyst domain per task.
                                        safe["tasks"] = [
                                            {"domain": task[:64]}
                                            for task in data["tasks"][:18]
                                            if isinstance(task, str)
                                        ]
                                    if kind == "analyst":
                                        domain = data.get("domain")
                                        if isinstance(domain, str):
                                            safe["domain"] = domain[:64]
                                        evidence_ids = data.get("evidence_ids")
                                        if isinstance(evidence_ids, list):
                                            safe["evidence_ids"] = [
                                                item[:128]
                                                for item in evidence_ids[:30]
                                                if isinstance(item, str)
                                            ]
                                        signals = data.get("signals")
                                        if isinstance(signals, dict):
                                            safe["signals"] = {
                                                str(ticker)[:16]: str(signal)[:16]
                                                for ticker, signal in list(signals.items())[:10]
                                            }
                                    await self.storage.add_event(run_id, kind, safe)
                        elif mode == "values" and isinstance(event, dict):
                            report_ready = bool(event.get("report"))
                finally:
                    if isinstance(context, RunContext):
                        await context.aclose()
                await self.storage.set_status_with_event(
                    run_id, "completed", data={"report_ready": report_ready}
                )
            except asyncio.CancelledError:
                await self.storage.set_status_with_event(run_id, "interrupted")
                raise
            except Exception as exc:
                await self.storage.set_status_with_event(run_id, "failed", type(exc).__name__)

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 64}
