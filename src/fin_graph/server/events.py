"""Coalescing wakeups; durable SQL remains the event source of truth."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg

_LOG = logging.getLogger(__name__)
CHANNEL = "fin_graph_events"


class EventListener:
    def __init__(self, database_url: str | None = None, *, retry_seconds: float = 1) -> None:
        self.database_url = (
            database_url.replace("postgresql+psycopg://", "postgresql://", 1)
            if database_url
            else None
        )
        self.retry_seconds = retry_seconds
        self.subscribers: dict[str, set[asyncio.Event]] = {}
        self.task: asyncio.Task[None] | None = None
        self.connection: psycopg.AsyncConnection | None = None
        self.ready = asyncio.Event()

    async def start(self) -> None:
        if self.database_url and self.task is None:
            self.task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.ready.clear()
        self.wake_all()

    @asynccontextmanager
    async def subscribe(self, thread_id: str) -> AsyncIterator[asyncio.Event]:
        signal = asyncio.Event()
        self.subscribers.setdefault(thread_id, set()).add(signal)
        try:
            yield signal
        finally:
            subscribers = self.subscribers[thread_id]
            subscribers.discard(signal)
            if not subscribers:
                del self.subscribers[thread_id]

    def wake(self, thread_id: str) -> None:
        for signal in self.subscribers.get(thread_id, ()):
            signal.set()

    def wake_all(self) -> None:
        for subscribers in self.subscribers.values():
            for signal in subscribers:
                signal.set()

    async def _listen(self) -> None:
        assert self.database_url is not None
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self.database_url, autocommit=True
                ) as connection:
                    self.connection = connection
                    await connection.execute(f"LISTEN {CHANNEL}")
                    self.ready.set()
                    self.wake_all()
                    async for notification in connection.notifies():
                        self.wake(notification.payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.warning("event listener disconnected; retrying")
                self.wake_all()
                await asyncio.sleep(self.retry_seconds)
            finally:
                self.ready.clear()
                self.connection = None
