from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.graphs.remediation.graph import (
    ActionCatalog,
    ActionSpec,
    UncertainActionOutcome,
)
from sre_graph.integrations.context import RunContext
from sre_graph.server.storage import Base, Storage


async def test_action_result_is_replayed_without_second_write() -> None:
    class Args(BaseModel):
        replicas: int

    calls: list[int] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, int]:
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, int]:
        calls.append(args.replicas)
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return True

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    catalog = ActionCatalog(
        [ActionSpec("scale", Args, prepare, execute, verify)], ledger=Storage(engine)
    )
    context = RunContext()

    assert await catalog.execute("scale", {"replicas": 2}, "a1", context) == {"replicas": 2}
    assert await catalog.execute("scale", {"replicas": 2}, "a1", context) == {"replicas": 2}
    assert calls == [2]
    await engine.dispose()


async def test_unknown_network_outcome_is_never_retried() -> None:
    class Args(BaseModel):
        replicas: int

    calls: list[int] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, int]:
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, int]:
        calls.append(args.replicas)
        raise TimeoutError("response was lost")

    async def verify(args: Args, context: RunContext) -> bool:
        return False

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    catalog = ActionCatalog(
        [ActionSpec("scale", Args, prepare, execute, verify)], ledger=Storage(engine)
    )
    with pytest.raises(TimeoutError):
        await catalog.execute("scale", {"replicas": 2}, "a1", RunContext())
    with pytest.raises(UncertainActionOutcome):
        await catalog.execute("scale", {"replicas": 2}, "a1", RunContext())
    assert calls == [2]
    await engine.dispose()
