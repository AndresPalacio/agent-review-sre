"""Durable rows of the paper broker: one portfolio per account and one row per approved order.

The broker (``integrations/broker/paper``) owns the financial logic; this module only stores and
reads rows, atomically. A fill writes the new portfolio with a compare-and-swap on ``version``
and moves its order out of ``queued`` in the same transaction, so two fills can never both apply
to one portfolio state and an order can never fill twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import Table, func, select, update
from sqlalchemy.engine import URL, Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from fin_graph.server.storage import PaperAccountRow, PaperOrderRow

FinishOutcome = Literal["ok", "conflict", "not_queued"]


class _Conflict(Exception):
    """The portfolio version moved: abort the whole transaction."""


class PaperLedgerStore:
    """Rows of ``paper_accounts`` and ``paper_orders``; values are plain JSON dicts."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def account(self, account_id: str) -> tuple[dict[str, Any] | None, int]:
        """The stored portfolio and its version; ``(None, 0)`` before the first fill."""
        async with self.sessions() as session:
            row = await session.get(PaperAccountRow, account_id)
            return (None, 0) if row is None else (dict(row.portfolio), row.version)

    async def order(self, client_order_id: str) -> dict[str, Any] | None:
        async with self.sessions() as session:
            row = await session.get(PaperOrderRow, client_order_id)
            return None if row is None else dict(row.execution)

    async def submit(
        self,
        account_id: str,
        client_order_id: str,
        *,
        ticker: str,
        session_date: str,
        execution: dict[str, Any],
        at: datetime,
    ) -> tuple[bool, dict[str, Any]]:
        """Insert a queued order; ``(False, stored)`` when the id already exists."""
        if not client_order_id or len(client_order_id) > 128:
            raise ValueError("invalid client_order_id")
        async with self.sessions.begin() as session:
            try:
                async with session.begin_nested():
                    session.add(
                        PaperOrderRow(
                            client_order_id=client_order_id,
                            account_id=account_id,
                            ticker=ticker,
                            status="queued",
                            session_date=session_date,
                            execution=execution,
                            submitted_at=at,
                            updated_at=at,
                        )
                    )
                    await session.flush()
                return True, execution
            except IntegrityError:
                row = await session.get(PaperOrderRow, client_order_id)
                if row is None:
                    raise
                return False, dict(row.execution)

    async def finish(
        self,
        account_id: str,
        client_order_id: str,
        execution: dict[str, Any],
        *,
        status: str,
        at: datetime,
        portfolio: dict[str, Any] | None = None,
        expected_version: int = 0,
    ) -> FinishOutcome:
        """Close a queued order, and with ``portfolio`` apply its fill, in one transaction.

        ``conflict``: the portfolio changed since ``expected_version`` was read (reload and
        retry). ``not_queued``: the order already left the queue (cancelled or filled).
        """
        try:
            async with self.sessions.begin() as session:
                moved = cast(
                    Any,
                    await session.execute(
                        update(PaperOrderRow)
                        .where(
                            PaperOrderRow.client_order_id == client_order_id,
                            PaperOrderRow.account_id == account_id,
                            PaperOrderRow.status == "queued",
                        )
                        .values(status=status, execution=execution, updated_at=at)
                    ),
                )
                if moved.rowcount != 1:
                    return "not_queued"
                if portfolio is None:
                    return "ok"
                if expected_version == 0:
                    try:
                        async with session.begin_nested():
                            session.add(
                                PaperAccountRow(
                                    account_id=account_id,
                                    portfolio=portfolio,
                                    version=1,
                                    updated_at=at,
                                )
                            )
                            await session.flush()
                    except IntegrityError as exc:
                        raise _Conflict from exc
                    return "ok"
                swapped = cast(
                    Any,
                    await session.execute(
                        update(PaperAccountRow)
                        .where(
                            PaperAccountRow.account_id == account_id,
                            PaperAccountRow.version == expected_version,
                        )
                        .values(portfolio=portfolio, version=expected_version + 1, updated_at=at)
                    ),
                )
                if swapped.rowcount != 1:
                    raise _Conflict
                return "ok"
        except _Conflict:
            # Raising out of the block rolled back the order update too.
            return "conflict"

    async def orders(
        self,
        account_id: str,
        *,
        statuses: Sequence[str] = (),
        session_date: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Orders of ``account_id``, oldest first, optionally by status and session day."""
        query = select(PaperOrderRow.execution).where(PaperOrderRow.account_id == account_id)
        if statuses:
            query = query.where(PaperOrderRow.status.in_(list(statuses)))
        if session_date is not None:
            query = query.where(PaperOrderRow.session_date == session_date)
        query = query.order_by(PaperOrderRow.submitted_at).limit(max(1, min(limit, 1000)))
        async with self.sessions() as session:
            return [dict(item) for item in (await session.scalars(query)).all()]

    async def count(self, account_id: str, *, session_date: str, statuses: Sequence[str]) -> int:
        query = (
            select(func.count())
            .select_from(PaperOrderRow)
            .where(
                PaperOrderRow.account_id == account_id,
                PaperOrderRow.session_date == session_date,
                PaperOrderRow.status.in_(list(statuses)),
            )
        )
        async with self.sessions() as session:
            return int((await session.execute(query)).scalar_one())


async def create_paper_tables(engine: AsyncEngine) -> None:
    """Create the two tables when missing (local SQLite; servers use the migrations)."""
    tables = [cast(Table, row.__table__) for row in (PaperAccountRow, PaperOrderRow)]

    def create(sync: Connection) -> None:
        for table in tables:
            table.create(sync, checkfirst=True)

    async with engine.begin() as connection:
        await connection.run_sync(create)


@asynccontextmanager
async def open_sqlite_paper_ledger(path: Path) -> AsyncIterator[PaperLedgerStore]:
    """A paper ledger in a local SQLite file, shared by CLI processes."""
    resolved = await asyncio.to_thread(_prepare, path)
    engine = create_async_engine(
        URL.create("sqlite+aiosqlite", database=str(resolved)), connect_args={"timeout": 30}
    )
    try:
        await create_paper_tables(engine)
        yield PaperLedgerStore(engine)
    finally:
        await engine.dispose()


def _prepare(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
