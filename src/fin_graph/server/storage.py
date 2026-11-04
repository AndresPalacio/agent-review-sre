"""Small durable ledger beside LangGraph's checkpoint tables."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    delete,
    func,
    or_,
    select,
    text,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def checkpoint_thread_ids(thread_id: str) -> tuple[str, str, str]:
    """Checkpoint ids owned by one financial analysis thread."""
    return thread_id, f"chat:{thread_id}", f"orders:{thread_id}"


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(120), nullable=True)


class RunEventRow(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FingerprintRow(Base):
    __tablename__ = "signal_fingerprints"

    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeliveryRow(Base):
    __tablename__ = "deliveries"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    destination: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionExecutionRow(Base):
    __tablename__ = "action_executions"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    thread_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CheckpointThreadRow(Base):
    __tablename__ = "checkpoint_threads"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(20))
    action_name: Mapped[str] = mapped_column(String(100))
    args_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaperAccountRow(Base):
    """A paper portfolio; ``version`` makes every fill a compare-and-swap."""

    __tablename__ = "paper_accounts"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    portfolio: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaperOrderRow(Base):
    """One approved paper order, keyed by its idempotent ``client_order_id``."""

    __tablename__ = "paper_orders"

    client_order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    execution: Mapped[dict[str, Any]] = mapped_column(JSON)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScheduleRow(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=False)


class WatchResultRow(Base):
    __tablename__ = "watch_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GatewayEventRow(Base):
    __tablename__ = "gateway_events"

    platform: Mapped[str] = mapped_column(String(20), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GatewayThreadRow(Base):
    __tablename__ = "gateway_threads"

    platform: Mapped[str] = mapped_column(String(20), primary_key=True)
    destination: Mapped[str] = mapped_column(String(128), primary_key=True)
    remote_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Storage:
    """Transactional run and auth metadata. Requests and reports remain in checkpoints."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        lock_pool_size: int = 8,
        lock_pool_timeout_seconds: float = 15,
    ) -> None:
        if not 1 <= lock_pool_size <= 128 or not 0 < lock_pool_timeout_seconds <= 60:
            raise ValueError("invalid advisory pool bounds")
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        # A held advisory lock must not take a ledger connection: its owner performs
        # ledger queries inside the lock. Separate bounded capacity breaks that cycle.
        self._lock_engine = (
            create_async_engine(
                engine.url,
                pool_size=lock_pool_size,
                max_overflow=0,
                pool_timeout=lock_pool_timeout_seconds,
                pool_pre_ping=True,
            )
            if engine.dialect.name == "postgresql"
            else None
        )
        self._lock_connection: ContextVar[tuple[asyncio.Task[Any], AsyncConnection] | None] = (
            ContextVar(f"storage_advisory_connection_{id(self)}", default=None)
        )
        self._closed = False
        self._disposed = False
        self._close_lock = asyncio.Lock()

    async def close(self) -> None:
        """Dispose advisory capacity after users stop; supplied ledger ownership stays external."""
        async with self._close_lock:
            if self._disposed:
                return
            self._closed = True
            if self._lock_engine is not None:
                await self._lock_engine.dispose()
            self._disposed = True

    @asynccontextmanager
    async def _advisory_connection(self) -> AsyncIterator[AsyncConnection]:
        if self._closed or self._lock_engine is None:
            raise RuntimeError("advisory storage is closed or unavailable")
        owner = asyncio.current_task()
        assert owner is not None
        existing = self._lock_connection.get()
        # ContextVars propagate to spawned tasks. Only the same task may reuse a
        # connection; a child/concurrent task must acquire its own transaction.
        if existing is not None and existing[0] is owner:
            yield existing[1]
            return
        async with self._lock_engine.connect() as connection, connection.begin():
            token = self._lock_connection.set((owner, connection))
            try:
                yield connection
            finally:
                self._lock_connection.reset(token)

    @asynccontextmanager
    async def approval_lock(self, kind: str, thread_id: str) -> AsyncIterator[None]:
        """Serialize checkpoint decisions across Postgres server processes."""
        if kind not in {"chat", "orders"} or not 1 <= len(thread_id) <= 64:
            raise ValueError("invalid approval lock key")
        if self.engine.dialect.name != "postgresql":
            yield
            return
        key = int.from_bytes(
            hashlib.blake2b(f"approval:{kind}:{thread_id}".encode(), digest_size=8).digest(),
            "big",
            signed=True,
        )
        async with self._advisory_connection() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            yield

    @asynccontextmanager
    async def run_lock(self, thread_id: str) -> AsyncIterator[None]:
        """Allow only one Postgres worker to execute a thread at a time."""
        if not 1 <= len(thread_id) <= 64:
            raise ValueError("invalid run lock key")
        if self.engine.dialect.name != "postgresql":
            yield
            return
        key = int.from_bytes(
            hashlib.blake2b(f"run:{thread_id}".encode(), digest_size=8).digest(),
            "big",
            signed=True,
        )
        async with self._advisory_connection() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            yield

    @asynccontextmanager
    async def try_run_lock(self, thread_id: str) -> AsyncIterator[bool]:
        """Skip a still-active worker during background reconciliation."""
        if not 1 <= len(thread_id) <= 64:
            raise ValueError("invalid run lock key")
        if self.engine.dialect.name != "postgresql":
            yield True
            return
        key = int.from_bytes(
            hashlib.blake2b(f"run:{thread_id}".encode(), digest_size=8).digest(),
            "big",
            signed=True,
        )
        async with self._advisory_connection() as connection:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
            )
            yield acquired is True

    async def create_schedule(
        self, schedule_id: str, definition: dict[str, Any], *, enabled: bool = False
    ) -> bool:
        """Insert once; a concurrent request cannot overwrite an existing schedule."""
        if not 1 <= len(schedule_id) <= 64:
            raise ValueError("invalid schedule id")
        async with self.sessions.begin() as session:
            try:
                async with session.begin_nested():
                    session.add(ScheduleRow(id=schedule_id, definition=definition, enabled=enabled))
                    await session.flush()
            except IntegrityError:
                return False
            return True

    async def put_schedule(
        self, schedule_id: str, definition: dict[str, Any], *, enabled: bool = False
    ) -> None:
        if not 1 <= len(schedule_id) <= 64:
            raise ValueError("invalid schedule id")
        async with self.sessions.begin() as session:
            row = await session.get(ScheduleRow, schedule_id, with_for_update=True)
            if row is None:
                session.add(ScheduleRow(id=schedule_id, definition=definition, enabled=enabled))
            else:
                row.definition = definition
                row.enabled = enabled

    async def update_schedule(
        self, schedule_id: str, definition: dict[str, Any], *, enabled: bool = False
    ) -> bool:
        async with self.sessions.begin() as session:
            row = await session.get(ScheduleRow, schedule_id, with_for_update=True)
            if row is None:
                return False
            row.definition = definition
            row.enabled = enabled
            return True

    async def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = await session.get(ScheduleRow, schedule_id)
            return _schedule_dict(row) if row else None

    async def list_schedules(self) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(ScheduleRow).order_by(ScheduleRow.id))).all()
            return [_schedule_dict(row) for row in rows]

    async def delete_schedule(self, schedule_id: str) -> bool:
        async with self.sessions.begin() as session:
            row = await session.get(ScheduleRow, schedule_id)
            if row is None:
                return False
            await session.delete(row)
            return True

    async def record_watch_result(
        self, schedule_id: str, status: str, details: dict[str, Any], checked_at: datetime
    ) -> None:
        """One market-watch check; ``triggered`` means it started an analysis."""
        if not 1 <= len(schedule_id) <= 64 or status not in {"normal", "triggered", "unknown"}:
            raise ValueError("invalid watch result")
        async with self.sessions.begin() as session:
            session.add(
                WatchResultRow(
                    schedule_id=schedule_id,
                    status=status,
                    details=details,
                    checked_at=checked_at,
                )
            )

    async def recent_watch_results(
        self, since: datetime, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("invalid watch result limit")
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(WatchResultRow)
                    .where(WatchResultRow.checked_at >= since)
                    .order_by(WatchResultRow.checked_at.desc(), WatchResultRow.id.desc())
                    .limit(limit)
                )
            ).all()
            return [
                {
                    "schedule_id": row.schedule_id,
                    "status": row.status,
                    "details": row.details,
                    "checked_at": row.checked_at.isoformat(),
                }
                for row in rows
            ]

    async def claim_gateway_event(
        self,
        platform: str,
        event_id: str,
        payload: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> bool:
        if platform not in {"slack", "telegram"} or not 1 <= len(event_id) <= 128:
            raise ValueError("invalid gateway event")
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            try:
                async with session.begin_nested():
                    session.add(
                        GatewayEventRow(
                            platform=platform,
                            event_id=event_id,
                            created_at=now,
                            updated_at=now,
                            status="pending" if payload is not None else "completed",
                            payload=payload,
                            attempts=0,
                            owner=owner,
                        )
                    )
                    await session.flush()
            except IntegrityError:
                return False
            return True

    async def pending_gateway_events(
        self, platform: str, before: datetime, *, limit: int = 100
    ) -> list[tuple[str, dict[str, Any]]]:
        if platform not in {"slack", "telegram"} or not 1 <= limit <= 500:
            raise ValueError("invalid gateway replay query")
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(GatewayEventRow)
                    .where(
                        GatewayEventRow.platform == platform,
                        GatewayEventRow.status == "pending",
                        GatewayEventRow.updated_at < before,
                    )
                    .order_by(GatewayEventRow.updated_at)
                    .limit(limit)
                )
            ).all()
            return [(row.event_id, row.payload) for row in rows if row.payload is not None]

    async def claim_stale_gateway_event(
        self, platform: str, event_id: str, before: datetime, owner: str | None = None
    ) -> bool:
        """Claim a replay candidate only if its heartbeat is still stale."""
        if platform not in {"slack", "telegram"} or not 1 <= len(event_id) <= 128:
            raise ValueError("invalid gateway event")
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(GatewayEventRow)
                    .where(
                        GatewayEventRow.platform == platform,
                        GatewayEventRow.event_id == event_id,
                        GatewayEventRow.status == "pending",
                        GatewayEventRow.updated_at < before,
                    )
                    .values(updated_at=datetime.now(UTC), owner=owner)
                ),
            )
            return result.rowcount == 1

    async def touch_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool:
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(GatewayEventRow)
                    .where(
                        GatewayEventRow.platform == platform,
                        GatewayEventRow.event_id == event_id,
                        GatewayEventRow.status == "pending",
                        GatewayEventRow.owner == owner,
                    )
                    .values(updated_at=datetime.now(UTC))
                ),
            )
            return result.rowcount == 1

    async def complete_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool:
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(GatewayEventRow)
                    .where(
                        GatewayEventRow.platform == platform,
                        GatewayEventRow.event_id == event_id,
                        GatewayEventRow.status == "pending",
                        GatewayEventRow.owner == owner,
                    )
                    .values(status="completed", updated_at=datetime.now(UTC), payload=None)
                ),
            )
            return result.rowcount == 1

    async def fail_gateway_event(
        self, platform: str, event_id: str, owner: str | None = None
    ) -> bool:
        """Record a failed delivery; return whether the event remains replayable."""
        async with self.sessions.begin() as session:
            row = await session.get(GatewayEventRow, (platform, event_id), with_for_update=True)
            if row is None or row.status != "pending" or row.owner != owner:
                return False
            row.attempts += 1
            row.updated_at = datetime.now(UTC)
            if row.attempts >= 5:
                row.status = "dead"
                row.payload = None
                return False
            return True

    async def bind_gateway_thread(
        self,
        platform: str,
        destination: str,
        remote_id: str,
        thread_id: str,
        *,
        approval_id: str | None = None,
    ) -> None:
        if (
            platform not in {"slack", "telegram"}
            or not 1 <= len(destination) <= 128
            or not 1 <= len(remote_id) <= 128
            or not 1 <= len(thread_id) <= 64
            or (approval_id is not None and not 1 <= len(approval_id) <= 64)
        ):
            raise ValueError("invalid gateway thread link")
        async with self.sessions.begin() as session:
            row = await session.get(GatewayThreadRow, (platform, destination, remote_id))
            if row is not None:
                if row.thread_id != thread_id or row.approval_id != approval_id:
                    raise ValueError("remote message is already bound to another request")
                return
            session.add(
                GatewayThreadRow(
                    platform=platform,
                    destination=destination,
                    remote_id=remote_id,
                    thread_id=thread_id,
                    approval_id=approval_id,
                    created_at=datetime.now(UTC),
                )
            )

    async def gateway_thread(self, platform: str, destination: str, remote_id: str) -> str | None:
        async with self.sessions() as session:
            row = await session.get(GatewayThreadRow, (platform, destination, remote_id))
            return row.thread_id if row else None

    async def gateway_approval_id(
        self, platform: str, destination: str, remote_id: str
    ) -> str | None:
        async with self.sessions() as session:
            row = await session.get(GatewayThreadRow, (platform, destination, remote_id))
            return row.approval_id if row else None

    async def checkpoint_threads(self, kind: str, *, limit: int = 100) -> list[str]:
        """The most recently touched standalone threads of ``kind``, newest first."""
        if kind not in {"chat", "orders"} or not 1 <= limit <= 500:
            raise ValueError("invalid checkpoint thread query")
        async with self.sessions() as session:
            rows = await session.scalars(
                select(CheckpointThreadRow.thread_id)
                .where(CheckpointThreadRow.kind == kind)
                .order_by(CheckpointThreadRow.touched_at.desc())
                .limit(limit)
            )
            return list(rows.all())

    async def list_approval_candidates(self, cutoff: datetime) -> list[tuple[str, str]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(CheckpointThreadRow.kind, CheckpointThreadRow.thread_id)
                    .where(CheckpointThreadRow.touched_at < cutoff)
                    .order_by(CheckpointThreadRow.touched_at)
                )
            ).all()
            return [(kind, thread_id) for kind, thread_id in rows]

    async def claim_delivery(self, key: str, destination: str) -> str:
        """Reserve one outbound message; pending means its remote outcome is uncertain."""
        if not 1 <= len(key) <= 128 or not 1 <= len(destination) <= 64:
            raise ValueError("invalid delivery claim")
        async with self.sessions.begin() as session:
            try:
                async with session.begin_nested():
                    session.add(
                        DeliveryRow(
                            idempotency_key=key,
                            destination=destination,
                            status="pending",
                            created_at=datetime.now(UTC),
                        )
                    )
                    await session.flush()
                return "new"
            except IntegrityError:
                row = await session.get(DeliveryRow, key)
                if row is None:
                    raise
                if row.destination != destination:
                    raise ValueError("delivery key was used for another destination") from None
                return row.status

    async def complete_delivery(self, key: str) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(DeliveryRow, key, with_for_update=True)
            if row is None:
                raise ValueError("delivery was not claimed")
            row.status = "completed"

    async def touch_checkpoint_thread(self, thread_id: str, kind: str) -> None:
        """Make standalone chat and order checkpoints visible to retention."""
        if not 1 <= len(thread_id) <= 64 or kind not in {"chat", "orders"}:
            raise ValueError("invalid checkpoint thread")
        now = datetime.now(UTC)
        table = cast(Table, CheckpointThreadRow.__table__)
        dialect = self.engine.dialect.name
        statement: Any
        if dialect == "postgresql":
            statement = pg_insert(table)
        elif dialect == "sqlite":
            statement = sqlite_insert(table)
        else:
            raise ValueError("checkpoint tracking requires PostgreSQL or SQLite")
        statement = statement.values(thread_id=thread_id, kind=kind, touched_at=now)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.thread_id, table.c.kind], set_={"touched_at": now}
        )
        async with self.sessions.begin() as session:
            await session.execute(statement)

    async def record_approval(
        self,
        thread_id: str,
        kind: str,
        actor: str,
        decision: str,
        action_name: str,
        args_digest: str,
    ) -> None:
        """Audit a human decision without persisting raw tool arguments or credentials."""
        if (
            not 1 <= len(thread_id) <= 64
            or kind not in {"chat", "orders"}
            or not 1 <= len(actor) <= 128
            or decision not in {"approve", "edit", "reject"}
            or not 1 <= len(action_name) <= 100
            or len(args_digest) != 64
            or any(char not in "0123456789abcdef" for char in args_digest)
        ):
            raise ValueError("invalid approval audit metadata")
        async with self.sessions.begin() as session:
            session.add(
                ApprovalDecisionRow(
                    thread_id=thread_id,
                    kind=kind,
                    actor=actor,
                    decision=decision,
                    action_name=action_name,
                    args_digest=args_digest,
                    created_at=datetime.now(UTC),
                )
            )

    async def approvals(self, thread_id: str) -> list[dict[str, str]]:
        """Read the audit trail for tests and authorized future operational surfaces."""
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(ApprovalDecisionRow)
                    .where(ApprovalDecisionRow.thread_id == thread_id)
                    .order_by(ApprovalDecisionRow.id)
                )
            ).all()
            return [
                {
                    "kind": row.kind,
                    "actor": row.actor,
                    "decision": row.decision,
                    "action_name": row.action_name,
                    "args_digest": row.args_digest,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    async def claim_action(
        self, key: str, fingerprint: str, thread_id: str | None = None
    ) -> tuple[str, dict[str, Any] | None]:
        """Atomically reserve a side effect; an unfinished claim means uncertain outcome."""
        if (
            not key
            or len(key) > 128
            or len(fingerprint) != 64
            or (thread_id is not None and not 1 <= len(thread_id) <= 128)
        ):
            raise ValueError("invalid action key")
        async with self.sessions.begin() as session:
            try:
                async with session.begin_nested():
                    session.add(
                        ActionExecutionRow(
                            key=key,
                            fingerprint=fingerprint,
                            status="pending",
                            thread_id=thread_id,
                            result=None,
                            created_at=datetime.now(UTC),
                        )
                    )
                    await session.flush()
                return "new", None
            except IntegrityError:
                row = await session.get(ActionExecutionRow, key)
                if row is None:
                    raise
                if row.fingerprint != fingerprint:
                    raise ValueError(
                        "action key was already used for different arguments"
                    ) from None
                if row.thread_id != thread_id:
                    raise ValueError("action key was already used for another thread") from None
                return row.status, row.result

    async def complete_action(self, key: str, result: dict[str, Any]) -> None:
        """Record a confirmed response; never convert an unknown response into success."""
        async with self.sessions.begin() as session:
            row = await session.get(ActionExecutionRow, key, with_for_update=True)
            if row is None:
                raise ValueError("action was not claimed")
            if row.status == "completed":
                if row.result != result:
                    raise ValueError("action result changed")
                return
            row.status = "completed"
            row.result = result

    async def create_run(self, run_id: str, thread_id: str) -> None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            session.add(
                RunRow(
                    run_id=run_id,
                    thread_id=thread_id,
                    status="queued",
                    created_at=now,
                    updated_at=now,
                )
            )

    async def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(RunRow)
                .where(RunRow.run_id == run_id)
                .values(status=status, error=error, updated_at=datetime.now(UTC))
            )

    async def set_status_with_event(
        self,
        run_id: str,
        status: str,
        error: str | None = None,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Commit status and its durable SSE event together, including transactional wakeup."""
        if data is not None and data.get("status", status) != status:
            raise ValueError("event status must match run status")
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            row = await session.get(RunRow, run_id, with_for_update=True)
            if row is None:
                raise ValueError("run does not exist")
            row.status = status
            row.error = error
            row.updated_at = now
            session.add(
                RunEventRow(
                    run_id=run_id,
                    kind="status",
                    data={"status": status, **(data or {})},
                    created_at=now,
                )
            )
            await session.flush()
            if self.engine.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_notify(:channel, :thread_id)"),
                    {"channel": "fin_graph_events", "thread_id": row.thread_id},
                )

    async def restart_interrupted_run(self, run_id: str) -> bool:
        """Claim recovery once across gateway workers without creating a second run."""
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(RunRow)
                    .where(RunRow.run_id == run_id, RunRow.status.in_(("interrupted", "failed")))
                    .values(status="queued", error=None, updated_at=datetime.now(UTC))
                ),
            )
            return bool(result.rowcount)

    async def claim_gateway_run_recovery(self, run_id: str, before: datetime) -> bool:
        """Claim failed runs or active runs whose worker heartbeat expired."""
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(RunRow)
                    .where(
                        RunRow.run_id == run_id,
                        or_(
                            RunRow.status.in_(("interrupted", "failed")),
                            (
                                RunRow.status.in_(("queued", "running", "recovering"))
                                & (RunRow.updated_at < before)
                            ),
                        ),
                    )
                    .values(status="recovering", error=None, updated_at=datetime.now(UTC))
                ),
            )
            return result.rowcount == 1

    async def touch_active_run(self, run_id: str) -> bool:
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(RunRow)
                    .where(
                        RunRow.run_id == run_id,
                        RunRow.status.in_(("queued", "running", "recovering")),
                    )
                    .values(updated_at=datetime.now(UTC))
                ),
            )
            return result.rowcount == 1

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
            return _run_dict(row) if row else None

    async def latest_run(self, thread_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(RunRow)
                .where(RunRow.thread_id == thread_id)
                .order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
                .limit(1)
            )
            return _run_dict(row) if row else None

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            query = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
            rows = (await session.scalars(query)).all()
            return [_run_dict(row) for row in rows]

    async def add_event(self, run_id: str, kind: str, data: dict[str, Any]) -> int:
        async with self.sessions.begin() as session:
            row = RunEventRow(run_id=run_id, kind=kind, data=data, created_at=datetime.now(UTC))
            session.add(row)
            await session.flush()
            if self.engine.dialect.name == "postgresql":
                thread_id = await session.scalar(
                    select(RunRow.thread_id).where(RunRow.run_id == run_id)
                )
                await session.execute(
                    text("SELECT pg_notify(:channel, :thread_id)"),
                    {"channel": "fin_graph_events", "thread_id": thread_id},
                )
            return row.id

    async def events(self, thread_id: str, after_id: int, limit: int = 500) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(RunEventRow)
                    .join(RunRow)
                    .where(RunRow.thread_id == thread_id, RunEventRow.id > after_id)
                    .order_by(RunEventRow.id)
                    .limit(limit)
                )
            ).all()
            return [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "type": row.kind,
                    "data": row.data,
                    "ts": row.created_at.isoformat(),
                }
                for row in rows
            ]

    async def stale_running_candidates(self, before: datetime) -> list[tuple[str, str]]:
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(RunRow.run_id, RunRow.thread_id).where(
                        RunRow.status.in_(("queued", "running")), RunRow.updated_at < before
                    )
                )
            ).all()
            return [(run_id, thread_id) for run_id, thread_id in rows]

    async def interrupt_stale_run(self, run_id: str, before: datetime) -> bool:
        """Claim and publish a stale interruption in one transaction."""
        async with self.sessions.begin() as session:
            thread_id = (
                await session.execute(
                    update(RunRow)
                    .where(
                        RunRow.run_id == run_id,
                        RunRow.status.in_(("queued", "running")),
                        RunRow.updated_at < before,
                    )
                    .values(status="interrupted", updated_at=datetime.now(UTC))
                    .returning(RunRow.thread_id)
                )
            ).scalar_one_or_none()
            if thread_id is None:
                return False
            session.add(
                RunEventRow(
                    run_id=run_id,
                    kind="status",
                    data={"status": "interrupted"},
                    created_at=datetime.now(UTC),
                )
            )
            await session.flush()
            if self.engine.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT pg_notify(:channel, :thread_id)"),
                    {"channel": "fin_graph_events", "thread_id": thread_id},
                )
            return True

    async def claim_fingerprint(
        self,
        source: str,
        fingerprint: str,
        thread_id: str,
        now: datetime,
        window: timedelta,
    ) -> bool:
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(FingerprintRow)
                    .where(
                        FingerprintRow.source == source,
                        FingerprintRow.fingerprint == fingerprint,
                        FingerprintRow.expires_at <= now,
                    )
                    .values(thread_id=thread_id, expires_at=now + window)
                ),
            )
            if result.rowcount:
                return True
            try:
                async with session.begin_nested():
                    session.add(
                        FingerprintRow(
                            source=source,
                            fingerprint=fingerprint,
                            thread_id=thread_id,
                            expires_at=now + window,
                        )
                    )
                    await session.flush()
            except IntegrityError:
                return False
            return True

    async def fingerprint_thread(self, source: str, fingerprint: str) -> str | None:
        async with self.sessions() as session:
            row = await session.get(FingerprintRow, (source, fingerprint))
            return row.thread_id if row else None

    async def claim_webhook_run(
        self,
        source: str,
        fingerprint: str,
        thread_id: str,
        run_id: str,
        now: datetime,
        window: timedelta,
    ) -> tuple[bool, str]:
        """Claim a notification and create its queued run in one transaction."""
        async with self.sessions.begin() as session:
            row = await session.get(FingerprintRow, (source, fingerprint), with_for_update=True)
            if row is not None and _as_utc(row.expires_at) > now:
                return False, row.thread_id
            if row is None:
                try:
                    async with session.begin_nested():
                        session.add(
                            FingerprintRow(
                                source=source,
                                fingerprint=fingerprint,
                                thread_id=thread_id,
                                expires_at=now + window,
                            )
                        )
                        await session.flush()
                except IntegrityError:
                    row = await session.get(
                        FingerprintRow, (source, fingerprint), with_for_update=True
                    )
                    if row is None or _as_utc(row.expires_at) > now:
                        return False, row.thread_id if row else ""
            if row is not None:
                row.thread_id = thread_id
                row.expires_at = now + window
            session.add(
                RunRow(
                    run_id=run_id,
                    thread_id=thread_id,
                    status="queued",
                    created_at=now,
                    updated_at=now,
                )
            )
            return True, thread_id

    async def release_fingerprint(self, source: str, fingerprint: str, thread_id: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(FingerprintRow).where(
                    FingerprintRow.source == source,
                    FingerprintRow.fingerprint == fingerprint,
                    FingerprintRow.thread_id == thread_id,
                )
            )

    async def release_thread_fingerprints(self, thread_id: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(FingerprintRow).where(FingerprintRow.thread_id == thread_id)
            )

    async def put_key(self, key: str, role: str) -> None:
        if role not in {"viewer", "operator", "approver", "admin"}:
            raise ValueError("invalid role")
        digest = hashlib.sha256(key.encode()).hexdigest()
        async with self.sessions.begin() as session:
            if await session.get(ApiKeyRow, digest) is None:
                session.add(ApiKeyRow(key_hash=digest, role=role, created_at=datetime.now(UTC)))

    async def key_role(self, key: str) -> str | None:
        digest = hashlib.sha256(key.encode()).hexdigest()
        async with self.sessions() as session:
            row = await session.get(ApiKeyRow, digest)
            return row.role if row else None

    async def expired_threads(self, cutoff: datetime) -> list[str]:
        async with self.sessions() as session:
            activity = union_all(
                select(RunRow.thread_id.label("thread_id"), RunRow.updated_at.label("seen_at")),
                select(
                    CheckpointThreadRow.thread_id.label("thread_id"),
                    CheckpointThreadRow.touched_at.label("seen_at"),
                ),
            ).subquery()
            query = (
                select(activity.c.thread_id)
                .group_by(activity.c.thread_id)
                .having(func.max(activity.c.seen_at) < cutoff)
            )
            return list((await session.scalars(query)).all())

    async def delete_thread(self, thread_id: str, cutoff: datetime) -> bool:
        """Delete a wholly expired thread; recheck age under the transaction."""
        async with self.sessions.begin() as session:
            activity = union_all(
                select(RunRow.updated_at.label("seen_at")).where(RunRow.thread_id == thread_id),
                select(CheckpointThreadRow.touched_at.label("seen_at")).where(
                    CheckpointThreadRow.thread_id == thread_id
                ),
            ).subquery()
            newest = await session.scalar(select(func.max(activity.c.seen_at)))
            if newest is not None and newest.tzinfo is None:
                newest = newest.replace(tzinfo=UTC)
            if newest is None or newest >= cutoff:
                return False
            run_ids = select(RunRow.run_id).where(RunRow.thread_id == thread_id)
            await session.execute(delete(RunEventRow).where(RunEventRow.run_id.in_(run_ids)))
            await session.execute(delete(RunRow).where(RunRow.thread_id == thread_id))
            await session.execute(
                delete(CheckpointThreadRow).where(CheckpointThreadRow.thread_id == thread_id)
            )
            await session.execute(
                delete(ApprovalDecisionRow).where(ApprovalDecisionRow.thread_id == thread_id)
            )
            await session.execute(
                delete(ActionExecutionRow).where(
                    ActionExecutionRow.thread_id.in_(checkpoint_thread_ids(thread_id))
                )
            )
            await session.execute(
                delete(FingerprintRow).where(FingerprintRow.thread_id == thread_id)
            )
            await session.execute(
                delete(GatewayThreadRow).where(GatewayThreadRow.thread_id == thread_id)
            )
            return True

    async def purge_before(self, cutoff: datetime) -> list[str]:
        deleted = []
        for thread_id in await self.expired_threads(cutoff):
            if await self.delete_thread(thread_id, cutoff):
                deleted.append(thread_id)
        await self.purge_metadata_before(cutoff)
        return deleted

    async def purge_metadata_before(self, cutoff: datetime) -> None:
        """Purge independent metadata after checkpoint-aware thread cleanup."""
        async with self.sessions.begin() as session:
            await session.execute(delete(FingerprintRow).where(FingerprintRow.expires_at < cutoff))
            await session.execute(delete(WatchResultRow).where(WatchResultRow.checked_at < cutoff))
            await session.execute(
                delete(GatewayEventRow).where(
                    GatewayEventRow.created_at < cutoff,
                    GatewayEventRow.status.in_(("completed", "dead")),
                )
            )


def _complete_on_cancel[T](
    method: Callable[..., Coroutine[Any, Any, T]],
) -> Callable[..., Coroutine[Any, Any, T]]:
    """Finish a ledger operation before honoring a caller's cancellation.

    Cancelling a query midway leaves its cursor referenced by the CancelledError
    traceback; SQLite then keeps that connection's read lock and every later write
    fails with "database is locked". The operation runs in its own task, and the
    caller's cancellation is re-raised once it has completed.
    """

    @functools.wraps(method)
    async def run(*args: Any, **kwargs: Any) -> T:
        operation = asyncio.ensure_future(method(*args, **kwargs))
        cancellation: asyncio.CancelledError | None = None
        while not operation.done():
            try:
                # Unlike shield(), wait() never raises the operation's own errors here.
                await asyncio.wait((operation,))
            except asyncio.CancelledError as exc:
                cancellation = exc
        if cancellation is not None:
            if not operation.cancelled():
                operation.exception()  # retrieved; the caller's cancellation prevails
            raise cancellation
        return operation.result()

    return run


for _name, _method in list(vars(Storage).items()):
    if inspect.iscoroutinefunction(_method):
        setattr(Storage, _name, _complete_on_cancel(_method))


def _run_dict(row: RunRow) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "thread_id": row.thread_id,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "error": row.error,
    }


def _schedule_dict(row: ScheduleRow) -> dict[str, Any]:
    return {"id": row.id, "definition": row.definition, "enabled": row.enabled}


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
